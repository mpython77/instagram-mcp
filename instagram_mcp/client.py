"""
Central Instagram API client — proxy-first, async-native, fast.

Architecture:
  - curl_cffi.requests.AsyncSession (no ThreadPoolExecutor needed)
  - Per-proxy session pool, reused across calls (TLS handshake amortised)
  - Single-flight cache: dedupes concurrent fetches for the same key
  - Centralised retry helper: 3 retries, different proxy each time, no waiting
  - Adaptive rate limiter as optional safety net (high RPS by default w/ proxies)

Pipeline:
  1. Cache lookup (single-flight aware) → instant on hit
  2. Rate limiter token (lightweight)
  3. Retry over different proxies until success or budget exhausted
  4. Store on success, propagate FetchError on full failure
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

import json as _json

from .cache import SmartCache
from .config import MCPConfig
from .cookie_manager import CookieManager
from .exceptions import FetchError
from .models import DateRange
from .proxy_manager import ProxyManager
from .rate_limiter import AdaptiveRateLimiter

# curl_cffi async — preferred, no thread pool needed
try:
    from curl_cffi.requests import AsyncSession  # type: ignore
    CURL_CFFI_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncSession = None  # type: ignore
    CURL_CFFI_AVAILABLE = False

logger = logging.getLogger("instagram_mcp.client")


def _mask_proxy(url: Optional[str]) -> str:
    """Mask credentials in a proxy URL for safe logging."""
    if not url:
        return "direct"
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = f"***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return url


class InstagramClient:
    """Async-native Instagram client — proxy-first, fast, deduplicating."""

    def __init__(
        self,
        config: MCPConfig,
        proxy_manager: ProxyManager,
        rate_limiter: AdaptiveRateLimiter,
        cache: SmartCache,
        cookie_manager: Optional[CookieManager] = None,
    ):
        if not CURL_CFFI_AVAILABLE:
            raise FetchError(
                "curl_cffi is not installed. Run: pip install 'curl-cffi>=0.5'"
            )
        self._config = config
        self._proxy_manager = proxy_manager
        self._rate_limiter = rate_limiter
        self._cache = cache
        self._cookie_manager = cookie_manager
        # Anonymous session pool, keyed by proxy URL (or "direct"); guarded by lock
        self._session_pool: Dict[str, AsyncSession] = {}
        self._session_pool_lock = asyncio.Lock()
        # Single authenticated session (no proxy — reduces ban risk)
        self._auth_session: Optional[AsyncSession] = None
        self._auth_session_lock = asyncio.Lock()
        self._closed = False

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def config(self) -> MCPConfig:
        return self._config

    @property
    def cache(self) -> SmartCache:
        return self._cache

    @property
    def proxy_manager(self) -> ProxyManager:
        return self._proxy_manager

    @property
    def rate_limiter(self) -> AdaptiveRateLimiter:
        return self._rate_limiter

    @property
    def cookie_manager(self) -> Optional[CookieManager]:
        return self._cookie_manager

    # ── Session management ───────────────────────────────────────────────────

    async def _get_session(self, proxy_url: Optional[str]) -> AsyncSession:
        """Return a pooled AsyncSession for this proxy (create on first use)."""
        if self._closed:
            raise FetchError("Client is closed")
        pool_key = proxy_url or "direct"
        async with self._session_pool_lock:
            session = self._session_pool.get(pool_key)
            if session is not None:
                return session

            proxies = (
                {"http": proxy_url, "https": proxy_url} if proxy_url else None
            )
            session = AsyncSession(
                headers=self._config.ig_headers,
                impersonate=self._config.ig_impersonate,
                proxies=proxies,
                timeout=self._config.request_timeout,
                max_clients=self._config.async_max_clients,
            )
            # Evict oldest session if pool exceeds limit
            MAX_POOL_SIZE = 50
            if len(self._session_pool) >= MAX_POOL_SIZE:
                oldest_key = next(iter(self._session_pool))
                old_session = self._session_pool.pop(oldest_key)
                try:
                    await old_session.close()
                except Exception:
                    pass
                logger.debug("Session pool evicted: %s (pool full)", oldest_key)
            self._session_pool[pool_key] = session
            logger.debug("New AsyncSession created for: %s", _mask_proxy(proxy_url))
            return session

    async def _invalidate_session(self, proxy_url: Optional[str]) -> None:
        """Close and remove a session — used when its connection state may be poisoned."""
        pool_key = proxy_url or "direct"
        async with self._session_pool_lock:
            session = self._session_pool.pop(pool_key, None)
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
            logger.debug("Session invalidated: %s", _mask_proxy(proxy_url))

    async def _get_auth_session(self) -> AsyncSession:
        """Return the single authenticated AsyncSession, creating it if needed."""
        if self._closed:
            raise FetchError("Client is closed")
        async with self._auth_session_lock:
            if self._auth_session is None:
                cm = self._cookie_manager
                self._auth_session = AsyncSession(
                    headers={
                        "User-Agent": self._config.ig_user_agent,
                        "Accept": "*/*",
                        "Accept-Language": "en-US,en;q=0.9",
                        "X-IG-App-ID": self._config.ig_app_id,
                        "Origin": "https://www.instagram.com",
                        "Referer": "https://www.instagram.com/",
                    },
                    impersonate=self._config.ig_impersonate,
                    timeout=self._config.request_timeout,
                    cookies=(cm.cookies if cm else {}),
                    max_clients=self._config.async_max_clients,
                )
            return self._auth_session

    async def close(self) -> None:
        """Cleanly shut down the client — close all pooled sessions."""
        if self._closed:
            return
        self._closed = True
        async with self._session_pool_lock:
            sessions = list(self._session_pool.values())
            self._session_pool.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:
                pass
        async with self._auth_session_lock:
            if self._auth_session is not None:
                try:
                    await self._auth_session.close()
                except Exception:
                    pass
                self._auth_session = None
        logger.info("Instagram client closed; %d session(s) released.", len(sessions))

    # Backwards compatibility — sync close (best-effort, fire-and-forget)
    def close_sessions(self) -> None:
        """Sync wrapper around `close()` — schedules cleanup on the running loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.close())
        except RuntimeError:
            # No running loop — best-effort synchronous cleanup is impossible for AsyncSession
            self._closed = True
            self._session_pool.clear()

    # ── Centralised retry helper ─────────────────────────────────────────────

    async def _with_proxy_retry(
        self,
        op_name: str,
        single_attempt: Callable[[Optional[str]], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        Run *single_attempt* up to `max_retries` times, swapping proxies between
        attempts. *single_attempt* receives the chosen proxy URL (or None for
        direct) and must return a dict with at least:
          - "ok": bool
          - "status_code": int

        On 429 → swap proxy, no waiting (and tell rate limiter).
        On other failures → swap proxy.
        """
        tried: Set[str] = set()
        last_error_msg = f"{op_name}: all {self._config.max_retries} retries failed"
        last_status = 0

        for attempt in range(self._config.max_retries):
            proxy_url = await self._proxy_manager.get_best_proxy(exclude=tried)
            if proxy_url:
                tried.add(proxy_url)

            start = time.monotonic()
            try:
                result = await single_attempt(proxy_url)
            except FetchError:
                raise  # configuration / fatal — don't retry
            except Exception as exc:
                e_type = type(exc).__name__
                if proxy_url:
                    await self._proxy_manager.report_failure(proxy_url, str(exc))
                # Network/TLS errors → drop the session so we don't reuse a poisoned one
                if any(
                    needle in e_type
                    for needle in ("Connection", "Timeout", "SSL", "Tls")
                ):
                    await self._invalidate_session(proxy_url)
                last_error_msg = f"{e_type}: {exc} [proxy: {_mask_proxy(proxy_url)}]"
                logger.debug(
                    "%s attempt %d failed: %s",
                    op_name, attempt + 1, last_error_msg,
                )
                continue

            latency = time.monotonic() - start
            status = int(result.get("status_code", 0))
            last_status = status

            if status == 429:
                if proxy_url:
                    await self._proxy_manager.report_failure(proxy_url, "429")
                await self._rate_limiter.on_rate_limited()
                logger.debug("%s 429 — swapping proxy (attempt %d)", op_name, attempt + 1)
                continue

            if not result.get("ok"):
                if proxy_url:
                    await self._proxy_manager.report_failure(proxy_url, f"HTTP {status}")
                last_error_msg = f"HTTP {status}"
                logger.debug(
                    "%s HTTP %d — swapping proxy (attempt %d)",
                    op_name, status, attempt + 1,
                )
                continue

            # Success
            if proxy_url:
                await self._proxy_manager.report_success(proxy_url, latency)
            await self._rate_limiter.on_success()
            return result

        # All retries exhausted
        raise FetchError(
            f"{op_name} — tried {self._config.max_retries} proxies, "
            f"last status={last_status}: {last_error_msg}"
        )

    # ── Profile fetch (web_profile_info) ─────────────────────────────────────

    async def _fetch_profile_attempt(
        self, username: str, proxy_url: Optional[str]
    ) -> Dict[str, Any]:
        """Single attempt: fetch web_profile_info."""
        url = self._config.ig_endpoint.format(username)
        session = await self._get_session(proxy_url)
        resp = await session.get(url)
        status = resp.status_code

        if status == 404:
            # Definitive "not found" — no retry needed; treat as success-with-None
            return {"ok": True, "found": False, "user": None, "status_code": 404}

        if status == 429:
            return {"ok": False, "user": None, "status_code": 429}

        if status != 200:
            return {"ok": False, "user": None, "status_code": status}

        try:
            data = resp.json()
        except (ValueError, TypeError):
            logger.warning(
                "Non-JSON response for @%s (status %d)", username, status
            )
            return {"ok": False, "user": None, "status_code": status}

        user = (data.get("data") or {}).get("user")
        if not user:
            # Empty 200 — Instagram occasionally serves blank shells; treat as not-found
            return {"ok": True, "found": False, "user": None, "status_code": 200}

        return {"ok": True, "found": True, "user": user, "status_code": 200}

    async def fetch_user(
        self,
        username: str,
        cache_ttl: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Fetch Instagram user data with cache + single-flight + proxy rotation.

        Returns:
            dict: user JSON if found
            None: if not found (404 or empty payload)

        Raises:
            FetchError: all retries exhausted with non-404 errors
        """
        cache_key = f"user:{username}"
        ttl = cache_ttl or self._config.cache_profile_ttl

        async def _do_fetch() -> Optional[Dict]:
            await self._rate_limiter.acquire()
            result = await self._with_proxy_retry(
                op_name=f"fetch_user(@{username})",
                single_attempt=lambda p: self._fetch_profile_attempt(username, p),
            )
            if not result.get("found"):
                return None
            return result.get("user")

        # Single-flight: dedup concurrent calls for the same username
        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    # ── v1/feed/user fetch (max_id pagination) ──────────────────────────────

    async def _fetch_feed_page_v1_attempt(
        self, url: str, proxy_url: Optional[str]
    ) -> Dict[str, Any]:
        """Single attempt: fetch one v1/feed/user page."""
        session = await self._get_session(proxy_url)
        resp = await session.get(url)
        status = resp.status_code

        if status == 200:
            try:
                d = resp.json()
                return {
                    "ok": True,
                    "items": d.get("items", []),
                    "more_available": d.get("more_available", False),
                    "next_max_id": d.get("next_max_id", ""),
                    "status_code": 200,
                }
            except (ValueError, TypeError):
                return {"ok": False, "items": [], "more_available": False, "next_max_id": "", "status_code": 200}

        if status == 429:
            return {"ok": False, "items": [], "more_available": False, "next_max_id": "", "status_code": 429}

        return {"ok": False, "items": [], "more_available": False, "next_max_id": "", "status_code": status}

    async def fetch_feed_items(
        self,
        user_id: str,
        max_posts: int,
        since_timestamp: Optional[int] = None,
        cache_ttl: Optional[int] = None,
        page_cb=None,
    ) -> List[Dict]:
        """
        Fetch posts via v1/feed/user with max_id pagination.

        First page uses count=12, subsequent pages use count=50.
        When since_timestamp is set, fetches up to 1000 posts to cover the range.
        Returns raw item dicts in v1/feed/user format (104 fields per item).

        page_cb: optional async callable(page_num, items_so_far, target) — called
                 after each page so callers can report per-page progress.
        """
        items: List[Dict] = []
        fetch_limit = max(max_posts, 1000) if since_timestamp else max_posts
        current_max_id: Optional[str] = None
        first_page = True
        page_num = 0
        STOP_THRESHOLD = 100
        consecutive_old = 0
        feed_endpoint = self._config.ig_feed_endpoint
        ttl = cache_ttl or self._config.cache_feed_ttl

        while len(items) < fetch_limit:
            count = 12 if first_page else 50
            url = f"{feed_endpoint.format(user_id)}?count={count}"
            if current_max_id:
                url += f"&max_id={current_max_id}"

            cache_key = f"feed_v1:{user_id}:{current_max_id or 'first'}"
            cached = await self._cache.get(cache_key)

            if cached is not None:
                page_result = cached
            else:
                await self._rate_limiter.acquire()
                try:
                    _url = url
                    page_result = await self._with_proxy_retry(
                        op_name=f"feed_v1(uid={user_id})",
                        single_attempt=lambda p, u=_url: self._fetch_feed_page_v1_attempt(u, p),
                    )
                except FetchError as e:
                    logger.debug("feed_v1 fetch failed: %s", e)
                    break

                if not page_result.get("ok"):
                    break
                await self._cache.set(cache_key, page_result, ttl)

            first_page = False
            page_num += 1
            batch = page_result.get("items") or []
            if not batch:
                break

            items.extend(batch)

            if page_cb is not None:
                try:
                    coro = page_cb(page_num, len(items), fetch_limit)
                    if asyncio.iscoroutine(coro):
                        await coro
                except Exception:
                    pass

            if since_timestamp:
                for item in batch:
                    t = item.get("taken_at", 0)
                    if t and t < since_timestamp:
                        consecutive_old += 1
                    elif t and t >= since_timestamp:
                        consecutive_old = 0
                if consecutive_old >= STOP_THRESHOLD:
                    break

            next_max_id = page_result.get("next_max_id", "")
            if not page_result.get("more_available") or not next_max_id:
                break
            current_max_id = str(next_max_id)

        logger.debug("feed_v1 fetched uid=%s: %d items in %d pages", user_id, len(items), page_num)
        return items

    # ── GraphQL feed page fetch ──────────────────────────────────────────────

    async def _fetch_graphql_attempt(
        self,
        username: str,
        first: int,
        after: str,
        proxy_url: Optional[str],
    ) -> Dict[str, Any]:
        """Single GraphQL feed page request."""
        variables = _json.dumps({
            "data": {"count": first},
            "username": username,
            "after": after,
            "first": first,
        })

        session = await self._get_session(proxy_url)
        resp = await session.get(
            self._config.ig_graphql_endpoint,
            params={
                "doc_id": self._config.ig_graphql_doc_id,
                "variables": variables,
                "fb_api_caller_class": "RelayModern",
                "server_timestamps": "true",
            },
            headers={"Referer": f"https://www.instagram.com/{username}/"},
        )

        status = resp.status_code
        if status == 429:
            return {"ok": False, "edges": [], "end_cursor": "", "has_next_page": False, "status_code": 429}
        if status != 200:
            logger.debug("GraphQL @%s HTTP %d", username, status)
            return {"ok": False, "edges": [], "end_cursor": "", "has_next_page": False, "status_code": status}

        try:
            data = resp.json()
        except (ValueError, TypeError):
            return {"ok": False, "edges": [], "end_cursor": "", "has_next_page": False, "status_code": 200}

        if data.get("errors") or data.get("status") == "fail":
            logger.debug(
                "GraphQL API error for @%s: %s",
                username, str(data.get("errors") or data.get("message", "unknown"))[:200],
            )
            return {"ok": False, "edges": [], "end_cursor": "", "has_next_page": False, "status_code": 200}

        data_block = data.get("data") or {}

        # New API payload (PolarisProfilePostsTabContentQuery_connection)
        if "xdt_api__v1__feed__user_timeline_graphql_connection" in data_block:
            media = data_block["xdt_api__v1__feed__user_timeline_graphql_connection"] or {}
        else:
            media = (data_block.get("user") or {}).get("edge_owner_to_timeline_media") or {}

        raw_edges = media.get("edges") or []
        edges = [e for e in raw_edges if e is not None]
        page_info = media.get("page_info") or {}

        return {
            "ok": True,
            "edges": edges,
            "end_cursor": page_info.get("end_cursor", ""),
            "has_next_page": page_info.get("has_next_page", False),
            "status_code": 200,
        }

    async def _fetch_single_feed_page(
        self,
        user_id: str,
        username: str,
        first: int,
        after: str,
    ) -> Dict[str, Any]:
        """Fetch a single GraphQL feed page with proxy rotation and retries."""
        try:
            return await self._with_proxy_retry(
                op_name=f"feed_page(@{username})",
                single_attempt=lambda p: self._fetch_graphql_attempt(username, first, after, p),
            )
        except FetchError as e:
            logger.debug("Feed page fetch failed: %s", e)
            return {
                "ok": False, "edges": [], "end_cursor": "",
                "has_next_page": False, "status_code": 0,
            }

    # ── Paginated feed fetch ─────────────────────────────────────────────────

    async def fetch_user_feed(
        self,
        user_id: str,
        username: str,
        end_cursor: str,
        max_posts: int = 50,
        max_age_days: int = 30,
        cache_ttl: Optional[int] = None,
        date_range: Optional[DateRange] = None,
    ) -> Dict[str, Any]:
        """Paginated feed fetch — multiple GraphQL requests across cursors."""
        all_edges: list = []
        cursor = end_cursor
        pages_fetched = 0
        has_more = False
        page_size = self._config.pagination_page_size
        max_age_seconds = max_age_days * 86400
        now = time.time()
        ttl = cache_ttl or self._config.cache_feed_ttl

        STOP_THRESHOLD = 5  # consecutive too-old posts when date_range is set
        consecutive_old = 0
        stop_early = False

        while len(all_edges) < max_posts and cursor:
            cache_key = f"feed_page:{user_id}:{cursor}"
            cached_page = await self._cache.get(cache_key)

            if cached_page is not None:
                page_result = cached_page
            else:
                await self._rate_limiter.acquire()
                page_result = await self._fetch_single_feed_page(
                    user_id, username, page_size, cursor
                )
                if not page_result.get("ok"):
                    logger.debug(
                        "Feed page failed for @%s page=%d status=%d — stopping",
                        username, pages_fetched + 1, page_result.get("status_code", 0),
                    )
                    break
                await self._cache.set(cache_key, page_result, ttl)

            pages_fetched += 1
            edges = [
                e for e in (page_result.get("edges") or [])
                if e is not None and e.get("node") is not None
            ]
            if not edges:
                break

            age_exceeded = False
            for edge in edges:
                node = edge.get("node") or {}
                taken_at = node.get("taken_at_timestamp") or node.get("taken_at") or 0

                if taken_at > 0 and (now - taken_at) > max_age_seconds:
                    age_exceeded = True
                    break

                if date_range:
                    if date_range.is_before_range(taken_at or 0):
                        consecutive_old += 1
                        if consecutive_old >= STOP_THRESHOLD:
                            logger.debug(
                                "Smart stop: %d consecutive older-than-since posts for user_id=%s",
                                STOP_THRESHOLD, user_id,
                            )
                            stop_early = True
                            break
                        continue
                    consecutive_old = 0
                    if not date_range.contains(taken_at or 0):
                        continue

                all_edges.append(edge)
                if len(all_edges) >= max_posts:
                    break

            cursor = page_result.get("end_cursor", "")
            has_more = page_result.get("has_next_page", False)

            if age_exceeded or stop_early or not has_more or not cursor:
                break

        return {
            "edges": all_edges,
            "pages_fetched": pages_fetched,
            "has_more": has_more and bool(cursor),
        }

    # ── Bulk fetch ───────────────────────────────────────────────────────────

    async def fetch_bulk(
        self,
        usernames: List[str],
        concurrency: int = 5,
        cache_ttl: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Check multiple usernames concurrently — single-flight dedups duplicates."""
        # Deduplicate while preserving order
        seen: Set[str] = set()
        unique: List[str] = []
        for u in usernames:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _check_one(username: str) -> Dict[str, Any]:
            async with semaphore:
                try:
                    user = await self.fetch_user(username, cache_ttl)
                    if user is None:
                        return {"username": username, "found": False, "user": None, "error": None}
                    return {"username": username, "found": True, "user": user, "error": None}
                except Exception as e:
                    return {"username": username, "found": False, "user": None, "error": str(e)}

        unique_results = await asyncio.gather(*[_check_one(u) for u in unique])
        result_map = {r["username"]: r for r in unique_results}
        return [result_map[u] for u in usernames if u in result_map]

    # ── Authenticated: Tagged Tab ─────────────────────────────────────────────

    async def fetch_tagged_posts(
        self,
        user_id: str,
        username: str,
        cursor: Optional[str] = None,
        count: int = 12,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch one page of posts from the Tagged Tab (requires cookies).

        These are posts made by OTHER PEOPLE that tag this account —
        completely different from the user's own posts (fetch_user_feed).

        Returns:
            {
                "edges": [...],           # list of post node dicts
                "end_cursor": str,
                "has_next_page": bool,
                "pages_fetched": int,     # always 1 per call
            }

        Raises:
            FetchError: if not authenticated or all requests fail
        """
        if not self._cookie_manager or not self._cookie_manager.is_authenticated:
            raise FetchError("Tagged Tab requires authentication. Set up cookies.txt.")

        cache_key = f"tagged:{user_id}:{cursor or 'first'}"
        ttl = cache_ttl or self._config.cache_tagged_ttl

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            fb_dtsg, lsd = await self._cookie_manager.ensure_csrf_tokens(session)  # type: ignore[union-attr]

            variables = _json.dumps({
                "after": cursor,
                "before": None,
                "count": count,
                "first": count,
                "last": None,
                "user_id": user_id,
            })

            # Tagged tab uses a POST with form-encoded body
            data = {
                "fb_dtsg": fb_dtsg,
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "PolarisProfileTaggedTabContentQuery_connection",
                "variables": variables,
                "server_timestamps": "true",
                "doc_id": self._config.ig_tagged_doc_id,
            }

            resp = await session.post(
                self._config.ig_graphql_endpoint,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://www.instagram.com/{username}/tagged/",
                    "X-FB-LSD": lsd,
                    "X-CSRFToken": self._cookie_manager.cookies.get("csrftoken", ""),  # type: ignore[union-attr]
                },
            )

            status = resp.status_code
            if status == 401 or status == 403:
                raise FetchError(
                    f"Tagged Tab: HTTP {status} — session may be expired. "
                    "Re-export cookies.txt and restart the server."
                )
            if status == 429:
                raise FetchError("Tagged Tab: rate limited (429). Wait a moment and retry.")
            if status != 200:
                raise FetchError(f"Tagged Tab: unexpected HTTP {status}")

            try:
                body = resp.json()
            except (ValueError, TypeError):
                raise FetchError("Tagged Tab: non-JSON response")

            if body.get("errors"):
                raise FetchError(f"Tagged Tab API error: {body['errors']}")

            conn = (
                (body.get("data") or {})
                .get("xdt_api__v1__usertags__user_id__feed_connection")
                or {}
            )
            edges = conn.get("edges") or []
            page_info = conn.get("page_info") or {}

            logger.debug(
                "Tagged @%s: %d edges, has_next=%s cursor=%s",
                username, len(edges),
                page_info.get("has_next_page"),
                page_info.get("end_cursor", "")[:20],
            )
            return {
                "edges": edges,
                "end_cursor": page_info.get("end_cursor") or "",
                "has_next_page": bool(page_info.get("has_next_page")),
                "pages_fetched": 1,
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def fetch_tagged_posts_paginated(
        self,
        user_id: str,
        username: str,
        max_posts: int = 50,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Collect up to max_posts from the Tagged Tab across multiple pages.

        Returns:
            {
                "edges": [...],
                "pages_fetched": int,
                "has_more": bool,
            }
        """
        all_edges: List[Dict] = []
        cursor: Optional[str] = None
        pages = 0
        has_more = False
        page_size = min(12, max_posts)

        while len(all_edges) < max_posts:
            page = await self.fetch_tagged_posts(
                user_id=user_id,
                username=username,
                cursor=cursor,
                count=page_size,
                cache_ttl=cache_ttl,
            )
            edges = page.get("edges") or []
            all_edges.extend(edges)
            pages += 1
            has_more = bool(page.get("has_next_page"))
            cursor = page.get("end_cursor") or ""
            if not has_more or not cursor:
                break

        return {
            "edges": all_edges[:max_posts],
            "pages_fetched": pages,
            "has_more": has_more,
        }

    # ── Reposts Tab ──────────────────────────────────────────────────────────

    async def fetch_reposts(
        self,
        user_id: str,
        username: str,
        max_id: Optional[str] = None,
        count: int = 12,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch one page of items from the Reposts Tab (requires cookies).

        These are posts by OTHER ACCOUNTS that this account chose to REPOST —
        showing what content this account actively amplifies.

        Key difference from Tagged Tab:
        - Pagination uses  max_id  (not GraphQL cursor `after`)
        - Response path:   data.fetch__XDTUserDict.user_reposts_timeline
        - Items are        {media: {...}}  not  {node: {...}}

        Returns:
            {
                "items":     [...],      # list of raw media dicts
                "next_max_id": str,      # cursor for next page (empty if done)
                "has_more":  bool,
                "pages_fetched": int,    # always 1 per call
            }

        Raises:
            FetchError: if not authenticated or all requests fail
        """
        if not self._cookie_manager or not self._cookie_manager.is_authenticated:
            raise FetchError("Reposts Tab requires authentication. Set up cookies.txt.")

        cache_key = f"reposts:{user_id}:{max_id or 'first'}"
        ttl = cache_ttl or self._config.cache_reposts_ttl

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            fb_dtsg, lsd = await self._cookie_manager.ensure_csrf_tokens(session)  # type: ignore[union-attr]

            variables = _json.dumps({
                "max_id": max_id,        # null on first page, cursor on subsequent
                "id": user_id,
            })

            data = {
                "fb_dtsg": fb_dtsg,
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "PolarisProfileRepostsTabContentRefetchQuery",
                "variables": variables,
                "server_timestamps": "true",
                "doc_id": self._config.ig_reposts_doc_id,
            }

            resp = await session.post(
                self._config.ig_graphql_endpoint,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://www.instagram.com/{username}/reels/",
                    "X-FB-LSD": lsd,
                    "X-CSRFToken": self._cookie_manager.cookies.get("csrftoken", ""),  # type: ignore[union-attr]
                },
            )

            status = resp.status_code
            if status in (401, 403):
                raise FetchError(
                    f"Reposts Tab: HTTP {status} — session may be expired. "
                    "Re-export cookies.txt and restart the server."
                )
            if status == 429:
                raise FetchError("Reposts Tab: rate limited (429). Wait a moment and retry.")
            if status != 200:
                raise FetchError(f"Reposts Tab: unexpected HTTP {status}")

            try:
                body = resp.json()
            except (ValueError, TypeError):
                raise FetchError("Reposts Tab: non-JSON response")

            if body.get("errors"):
                raise FetchError(f"Reposts Tab API error: {body['errors']}")

            timeline = (
                (body.get("data") or {})
                .get("fetch__XDTUserDict", {})
                .get("user_reposts_timeline") or {}
            )
            items = timeline.get("repost_grid_items") or []
            next_max_id = timeline.get("repost_next_max_id") or ""
            has_more = bool(timeline.get("repost_more_available"))

            logger.debug(
                "Reposts @%s: %d items, has_more=%s next_max_id=%s",
                username, len(items), has_more, str(next_max_id)[:20],
            )
            return {
                "items": items,
                "next_max_id": next_max_id,
                "has_more": has_more,
                "pages_fetched": 1,
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def fetch_reposts_paginated(
        self,
        user_id: str,
        username: str,
        max_posts: int = 50,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Collect up to max_posts repost items across multiple pages.

        Pagination uses max_id (Reposts Tab cursor), not GraphQL `after`.

        Returns:
            {
                "items":        [...],   # flat list of raw media dicts
                "pages_fetched": int,
                "has_more":     bool,
            }
        """
        all_items: List[Dict] = []
        max_id: Optional[str] = None
        pages = 0
        has_more = False
        page_size = min(12, max_posts)

        while len(all_items) < max_posts:
            page = await self.fetch_reposts(
                user_id=user_id,
                username=username,
                max_id=max_id,
                count=page_size,
                cache_ttl=cache_ttl,
            )
            items = page.get("items") or []
            all_items.extend(items)
            pages += 1
            has_more = bool(page.get("has_more"))
            max_id = page.get("next_max_id") or ""
            if not has_more or not max_id:
                break

        return {
            "items": all_items[:max_posts],
            "pages_fetched": pages,
            "has_more": has_more,
        }

    # ── Reels Tab ────────────────────────────────────────────────────────────

    async def fetch_reels(
        self,
        user_id: str,
        username: str,
        cursor: Optional[str] = None,
        count: int = 12,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch one page of reels from the Reels Tab (requires cookies).

        These are the account's OWN reels, with play_count — the primary reel
        metric not available via the standard feed API.

        Key structural differences from Tagged Tab and Reposts Tab:
        - Variables: {"after": cursor, "data": {"include_feed_video": true,
          "page_size": count, "target_user_id": user_id}, "first": count}
        - Response key: data.xdt_api__v1__clips__user__connection_v2
        - Pagination: GraphQL end_cursor/has_next_page (same as Tagged Tab)
        - Edges: {node: {media: {...}}} — media holds all reel fields
        - view_count is always null; play_count is the correct metric

        Returns:
            {
                "edges":         [...],   # list of {node: {media: {...}}}
                "end_cursor":    str,
                "has_next_page": bool,
                "pages_fetched": int,     # always 1 per call
            }

        Raises:
            FetchError: if not authenticated or all requests fail
        """
        if not self._cookie_manager or not self._cookie_manager.is_authenticated:
            raise FetchError("Reels Tab requires authentication. Set up cookies.txt.")

        cache_key = f"reels:{user_id}:{cursor or 'first'}"
        ttl = cache_ttl or self._config.cache_reels_ttl

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            fb_dtsg, lsd = await self._cookie_manager.ensure_csrf_tokens(session)  # type: ignore[union-attr]

            variables = _json.dumps({
                "after": cursor,
                "before": None,
                "data": {
                    "include_feed_video": True,
                    "page_size": count,
                    "target_user_id": user_id,
                },
                "first": count,
                "last": None,
            })

            data = {
                "fb_dtsg": fb_dtsg,
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "PolarisProfileReelsTabContentQuery_connection",
                "variables": variables,
                "server_timestamps": "true",
                "doc_id": self._config.ig_reels_doc_id,
            }

            resp = await session.post(
                self._config.ig_graphql_endpoint,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://www.instagram.com/{username}/reels/",
                    "X-FB-LSD": lsd,
                    "X-CSRFToken": self._cookie_manager.cookies.get("csrftoken", ""),  # type: ignore[union-attr]
                },
            )

            status = resp.status_code
            if status in (401, 403):
                raise FetchError(
                    f"Reels Tab: HTTP {status} — session may be expired. "
                    "Re-export cookies.txt and restart the server."
                )
            if status == 429:
                raise FetchError("Reels Tab: rate limited (429). Wait a moment and retry.")
            if status != 200:
                raise FetchError(f"Reels Tab: unexpected HTTP {status}")

            try:
                body = resp.json()
            except (ValueError, TypeError):
                raise FetchError("Reels Tab: non-JSON response")

            if body.get("errors"):
                raise FetchError(f"Reels Tab API error: {body['errors']}")

            conn = (
                (body.get("data") or {})
                .get("xdt_api__v1__clips__user__connection_v2")
                or {}
            )
            edges = conn.get("edges") or []
            page_info = conn.get("page_info") or {}

            logger.debug(
                "Reels @%s: %d edges, has_next=%s cursor=%s",
                username, len(edges),
                page_info.get("has_next_page"),
                page_info.get("end_cursor", "")[:20],
            )
            return {
                "edges": edges,
                "end_cursor": page_info.get("end_cursor") or "",
                "has_next_page": bool(page_info.get("has_next_page")),
                "pages_fetched": 1,
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def fetch_reels_paginated(
        self,
        user_id: str,
        username: str,
        max_reels: int = 50,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Collect up to max_reels from the Reels Tab across multiple pages.

        Uses GraphQL cursor pagination (end_cursor / has_next_page),
        same as Tagged Tab — not max_id like Reposts Tab.

        Returns:
            {
                "edges":        [...],   # flat list of raw edge dicts
                "pages_fetched": int,
                "has_more":     bool,
            }
        """
        all_edges: List[Dict] = []
        cursor: Optional[str] = None
        pages = 0
        has_more = False
        page_size = min(12, max_reels)

        while len(all_edges) < max_reels:
            page = await self.fetch_reels(
                user_id=user_id,
                username=username,
                cursor=cursor,
                count=page_size,
                cache_ttl=cache_ttl,
            )
            edges = page.get("edges") or []
            all_edges.extend(edges)
            pages += 1
            has_more = bool(page.get("has_next_page"))
            cursor = page.get("end_cursor") or ""
            if not has_more or not cursor:
                break

        return {
            "edges": all_edges[:max_reels],
            "pages_fetched": pages,
            "has_more": has_more,
        }

    # ── Single post (HTML scrape) ────────────────────────────────────────────

    async def fetch_post(
        self,
        shortcode: str,
        cache_ttl: Optional[int] = None,
    ) -> str:
        """
        Fetch the raw HTML of https://www.instagram.com/p/{shortcode}/.

        Anonymous — no cookies required. Works for all public posts.
        Returns the raw HTML string for parsing by parse_post_html().

        Raises:
            FetchError: if the post is not found (404) or all retries fail
        """
        cache_key = f"post_html:{shortcode}"
        ttl = cache_ttl or self._config.cache_profile_ttl  # reuse profile TTL (5 min)

        async def _do_fetch() -> str:
            await self._rate_limiter.acquire()
            result = await self._with_proxy_retry(
                op_name=f"fetch_post({shortcode})",
                single_attempt=lambda p: self._fetch_post_attempt(shortcode, p),
            )
            return result["html"]

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def _fetch_post_attempt(
        self,
        shortcode: str,
        proxy_url: Optional[str],
    ) -> Dict[str, Any]:
        """Single attempt: fetch post page HTML."""
        url = f"https://www.instagram.com/p/{shortcode}/"
        session = await self._get_session(proxy_url)
        resp = await session.get(
            url,
            headers={
                "User-Agent": self._config.ig_user_agent,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
            },
        )
        status = resp.status_code

        if status == 404:
            raise FetchError(
                f"Post /{shortcode}/ not found — deleted, private, or invalid shortcode."
            )
        if status == 429:
            return {"ok": False, "html": "", "status_code": 429}
        if status != 200:
            return {"ok": False, "html": "", "status_code": status}

        html = resp.text
        # Sanity check: Instagram returns login-wall HTML (~30 KB) when blocked
        if len(html) < 50_000 or "taken_at" not in html:
            logger.warning(
                "fetch_post(%s): suspiciously small/empty HTML (%d bytes) — "
                "may be login wall or bot detection",
                shortcode, len(html),
            )
            return {"ok": False, "html": "", "status_code": status}

        logger.debug("fetch_post(%s): HTML %d bytes OK", shortcode, len(html))
        return {"ok": True, "html": html, "status_code": 200}

    # ── Comments ─────────────────────────────────────────────────────────────

    async def _fetch_comments_attempt(
        self, media_id: str, params: Dict[str, str], proxy_url: Optional[str]
    ) -> Dict[str, Any]:
        url = f"https://www.instagram.com/api/v1/media/{media_id}/comments/"
        session = await self._get_session(proxy_url)
        resp = await session.get(
            url,
            params=params,
            headers={
                "User-Agent": self._config.ig_user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
                "X-IG-App-ID": "936619743392459",
            },
        )
        status = resp.status_code
        if status == 404:
            from .exceptions import UserNotFoundError
            raise UserNotFoundError(message=f"Post {media_id} not found (404).")
        if status == 403:
            from .exceptions import PrivateAccountError
            raise PrivateAccountError(message=f"Post {media_id} is private or access denied (403).")
        if status == 429:
            return {"ok": False, "status_code": 429}
        if status != 200:
            return {"ok": False, "status_code": status}
        try:
            body = resp.json()
        except (ValueError, TypeError):
            return {"ok": False, "status_code": status}
        if body.get("status") not in ("ok", None):
            return {"ok": False, "status_code": status}
        return {
            "ok": True,
            "status_code": 200,
            "comments": body.get("comments") or [],
            "caption": body.get("caption"),
            "comment_count": int(body.get("comment_count") or 0),
            "next_min_id": body.get("next_min_id") or "",
            "has_more": bool(body.get("has_more_headload_comments")),
        }

    async def fetch_comments(
        self,
        media_id: str,
        min_id: Optional[str] = None,
        sort_order: str = "popular",
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch one page of comments from /api/v1/media/{media_id}/comments/.

        Anonymous — no cookies required. ~15 comments per page.

        Key structural details:
        - min_id is a JSON string {"cached_comments_cursor":..., "bifilter_token":...}
        - next_min_id in response = cursor for next page
        - has_more_headload_comments = there are more comments in this direction
        - caption is returned as a top-level field (type=1)
        - GIF comments: giphy_media_info present, text=""
        - has_translation=True on non-English comments (auto-detected)

        Returns:
            {
                "comments":      [...],  # raw comment dicts
                "caption":       {...} or None,
                "comment_count": int,
                "next_min_id":   str,    # JSON string cursor for next page
                "has_more":      bool,
                "pages_fetched": 1,
            }

        Raises:
            FetchError: 404 (not found), 403 (private), 429 (rate limit)
        """
        cache_key = f"comments:{media_id}:{sort_order}:{min_id or 'first'}"
        ttl = cache_ttl or self._config.cache_comments_ttl

        async def _do_fetch() -> Dict[str, Any]:
            await self._rate_limiter.acquire()
            params: Dict[str, str] = {
                "can_support_threading": "true",
                "sort_order": sort_order,
            }
            if min_id:
                params["min_id"] = min_id
            result = await self._with_proxy_retry(
                op_name=f"fetch_comments({media_id})",
                single_attempt=lambda p: self._fetch_comments_attempt(media_id, params, p),
            )
            logger.debug(
                "Comments media=%s: %d comments, has_more=%s",
                media_id,
                len(result.get("comments") or []),
                result.get("has_more"),
            )
            return result

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def fetch_comments_paginated(
        self,
        media_id: str,
        max_comments: int = 100,
        sort_order: str = "popular",
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Collect up to max_comments across multiple pages of a post's comments.

        Pagination: next_min_id JSON string from response → min_id in next request.
        First page omits min_id. Stops when has_more=False or no cursor returned.

        Returns:
            {
                "comments":      [...],  # flat list of raw comment dicts
                "caption":       {...} or None,
                "comment_count": int,    # total on the post (from API)
                "pages_fetched": int,
                "has_more":      bool,
            }
        """
        all_comments: List[Dict] = []
        caption_raw = None
        comment_count = 0
        min_id: Optional[str] = None
        pages = 0
        has_more = False

        while len(all_comments) < max_comments:
            page = await self.fetch_comments(
                media_id=media_id,
                min_id=min_id,
                sort_order=sort_order,
                cache_ttl=cache_ttl,
            )
            batch = page.get("comments") or []
            all_comments.extend(batch)
            pages += 1

            if caption_raw is None:
                caption_raw = page.get("caption")
            if not comment_count:
                comment_count = int(page.get("comment_count") or 0)

            has_more = bool(page.get("has_more"))
            min_id = page.get("next_min_id") or ""

            if not has_more or not min_id or not batch:
                break

        return {
            "comments": all_comments[:max_comments],
            "caption": caption_raw,
            "comment_count": comment_count,
            "pages_fetched": pages,
            "has_more": has_more,
        }

    # ── Hashtag (explore/tags HTML parse) ────────────────────────────────────

    async def _fetch_hashtag_attempt(
        self,
        tag: str,
        proxy_url: Optional[str],
    ) -> Dict[str, Any]:
        """
        Fetch /explore/tags/{tag}/ HTML and extract embedded Relay SSR data.

        Instagram embeds the top posts in <script type="application/json"> blocks
        using the Relay framework's RelayPrefetchedStreamCache pattern.
        The relevant data lives under __bbox → result → data →
            xig_logged_out_popular_search_media_info   (up to 12 top posts)
            popular_search_related_keywords_connection (10 related searches)
        """
        import re as _re
        import json as _json_mod

        url = f"https://www.instagram.com/explore/tags/{tag}/"
        session = await self._get_session(proxy_url)
        resp = await session.get(
            url,
            headers={
                "User-Agent": self._config.ig_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
            },
        )
        status = resp.status_code
        if status == 404:
            return {"ok": True, "found": False, "status_code": 404}
        if status == 429:
            return {"ok": False, "status_code": 429}
        if status != 200:
            return {"ok": False, "status_code": status}

        html = resp.text
        if len(html) < 10_000:
            return {"ok": False, "status_code": status}

        # Extract all <script type="application/json"> blocks
        script_blobs = _re.findall(
            r'<script type="application/json"[^>]*>(.*?)</script>',
            html, _re.DOTALL,
        )

        def _deep_find(obj: Any, key: str, depth: int = 12) -> List[Any]:
            if depth <= 0:
                return []
            results: List[Any] = []
            if isinstance(obj, dict):
                if key in obj:
                    results.append(obj[key])
                for v in obj.values():
                    results.extend(_deep_find(v, key, depth - 1))
            elif isinstance(obj, list):
                for item in obj:
                    results.extend(_deep_find(item, key, depth - 1))
            return results

        media_info: Optional[Dict] = None
        related_kw: List[Dict] = []

        for raw in script_blobs:
            try:
                parsed = _json_mod.loads(raw)
            except _json_mod.JSONDecodeError:
                continue

            for bbox in _deep_find(parsed, "__bbox"):
                if not isinstance(bbox, dict):
                    continue
                result = bbox.get("result")
                if not isinstance(result, dict):
                    continue
                data = result.get("data")
                if not isinstance(data, dict):
                    continue

                if "xig_logged_out_popular_search_media_info" in data:
                    media_info = data["xig_logged_out_popular_search_media_info"]
                    kw_conn = data.get("popular_search_related_keywords_connection") or {}
                    related_kw = [
                        e["node"]["query_text"]
                        for e in (kw_conn.get("edges") or [])
                        if isinstance(e.get("node"), dict)
                    ]
                    break

            if media_info is not None:
                break

        if media_info is None:
            logger.warning("fetch_hashtag(%s): Relay data not found in HTML", tag)
            return {"ok": False, "status_code": 200}

        edges = media_info.get("edges") or []
        page_info = media_info.get("page_info") or {}

        return {
            "ok": True,
            "found": True,
            "status_code": 200,
            "tag": tag,
            "posts": edges,
            "has_more": bool(page_info.get("has_next_page")),
            "end_cursor": page_info.get("end_cursor", ""),
            "related_searches": related_kw,
        }

    async def _fetch_hashtag_sections_page(
        self,
        tag: str,
        max_id: str = "",
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Single page of hashtag top posts via /api/v1/tags/{tag}/sections/ (auth required).

        Returns 30 posts/page with full like_count, play_count, and captions.
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""

        url = f"https://i.instagram.com/api/v1/tags/{tag}/sections/"
        payload: Dict[str, Any] = {
            "tab":            "top",
            "page":           page,
            "next_media_ids": "[]",
            "max_id":         max_id,
        }
        resp = await session.post(
            url,
            data=payload,
            headers={
                "User-Agent":    self._config.ig_user_agent,
                "Accept":        "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin":        "https://www.instagram.com",
                "Referer":       f"https://www.instagram.com/explore/tags/{tag}/",
                "x-ig-app-id":   self._config.ig_app_id,
                "x-ig-www-claim": "0",
                "x-csrftoken":   csrf,
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        status = resp.status_code
        if status == 429:
            raise FetchError(f"fetch_hashtag_sections({tag}) rate limited (429)")
        if status == 401:
            raise FetchError(f"fetch_hashtag_sections({tag}) auth required (401) — check cookies")
        if status not in (200, 201):
            raise FetchError(f"fetch_hashtag_sections({tag}) HTTP {status}")

        body = resp.json()
        sections   = body.get("sections") or []
        more       = bool(body.get("more_available", False))
        next_max_id = body.get("next_max_id", "")

        posts: List[Dict] = []
        for sec in sections:
            medias = (sec.get("layout_content") or {}).get("medias") or []
            for m in medias:
                media = m.get("media") or {}
                user  = media.get("user") or {}
                cap   = media.get("caption") or {}
                mtype = media.get("media_type", 1)

                # Music
                music_meta  = media.get("music_metadata") or {}
                music_info  = (music_meta.get("music_info") or {}).get("music_asset_info") or {}
                music_title = music_info.get("title") or ""
                music_artist = music_info.get("ig_username") or music_info.get("display_artist") or ""
                music_dur_ms = music_info.get("duration_in_ms")
                audio_type   = music_meta.get("audio_type") or ""

                # Tagged users
                usertags = [
                    t.get("user", {}).get("username", "")
                    for t in (media.get("usertags") or {}).get("in") or []
                    if t.get("user")
                ]

                # Coauthors
                coauthors = [
                    c.get("username", "")
                    for c in (media.get("coauthor_producers") or [])
                    if c.get("username")
                ]

                # Location
                locations = [
                    {"name": loc.get("name", ""), "lat": loc.get("lat"), "lng": loc.get("lng")}
                    for loc in (media.get("locations") or [])
                ]

                # Clips metadata (reel-specific)
                clips_meta = media.get("clips_metadata") or {}
                challenge   = clips_meta.get("challenge_info") or {}
                challenge_title = (challenge.get("challenge") or {}).get("title") or ""
                branded_content = clips_meta.get("branded_content_tag_info") or {}
                mashup_count = (clips_meta.get("mashup_info") or {}).get("formatted_mashups_count") or ""

                # Carousel
                carousel_count = media.get("carousel_media_count") or 0
                carousel_items = []
                if mtype == 8:
                    for ci in (media.get("carousel_media") or []):
                        ci_cap = ci.get("caption") or {}
                        carousel_items.append({
                            "media_type":  ci.get("media_type", 1),
                            "shortcode":   ci.get("code") or ci.get("pk", ""),
                            "accessibility_caption": ci.get("accessibility_caption") or "",
                            "dominant_color": ci.get("dominant_color") or "",
                        })

                posts.append({
                    # Identity
                    "shortcode":    media.get("code", ""),
                    "url":          f"https://www.instagram.com/p/{media.get('code','')}/",
                    "pk":           str(media.get("pk", "")),
                    "taken_at":     media.get("taken_at"),

                    # Author
                    "username":     user.get("username", ""),
                    "full_name":    user.get("full_name", ""),
                    "user_pk":      str(user.get("pk", "")),
                    "verified":     bool(user.get("is_verified")),
                    "is_private":   bool(user.get("is_private")),
                    "account_type": user.get("account_type"),  # 1=personal 2=creator 3=business

                    # Content type
                    "media_type":   mtype,           # 1=photo 2=video 8=carousel
                    "product_type": media.get("product_type", ""),  # feed/clips/carousel_container

                    # Engagement
                    "like_count":       media.get("like_count"),
                    "comment_count":    media.get("comment_count"),
                    "play_count":       media.get("play_count") or media.get("ig_play_count"),
                    "repost_count":     media.get("media_repost_count"),
                    "counts_disabled":  bool(media.get("like_and_view_counts_disabled")),

                    # Caption
                    "caption": (cap.get("text") or "") if isinstance(cap, dict) else "",

                    # Image/Video
                    "width":          media.get("original_width"),
                    "height":         media.get("original_height"),
                    "video_duration": media.get("video_duration"),
                    "has_audio":      media.get("has_audio"),
                    "filter_type":    media.get("filter_type"),

                    # Music
                    "music_title":    music_title,
                    "music_artist":   music_artist,
                    "music_duration_ms": music_dur_ms,
                    "audio_type":     audio_type,

                    # Social
                    "tagged_users":   usertags,
                    "coauthors":      coauthors,
                    "is_paid_partnership": bool(media.get("is_paid_partnership")),
                    "accessibility_caption": media.get("accessibility_caption") or "",

                    # Location
                    "locations":      locations,

                    # Carousel
                    "carousel_count": carousel_count,
                    "carousel_items": carousel_items,

                    # Reel extras
                    "challenge":      challenge_title,
                    "mashup_count":   mashup_count,
                })

        return {
            "posts":      posts,
            "more":       more,
            "next_max_id": next_max_id,
        }

    async def fetch_hashtag(
        self,
        tag: str,
        max_posts: int = 12,
        cache_ttl: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch top posts for an Instagram hashtag.

        AUTO-SELECTS mode based on auth availability:

        🌐 ANON (no cookies): parses /explore/tags/{tag}/ HTML — 12 posts max,
           no like counts, no pagination.

        🔐 AUTH (cookies.json present): uses /api/v1/tags/{tag}/sections/ —
           30 posts/page, full like_count + play_count, paginated up to max_posts.

        Returns:
            {
                "tag":             str,
                "posts":           list[dict],
                "has_more":        bool,
                "related_searches": list[str],   # anon mode only
                "auth_used":       bool,
            }
            or None if the tag is not found / page unavailable.
        """
        tag = tag.lstrip("#").lower().strip()
        auth_available = (
            self._cookie_manager is not None
            and getattr(self._cookie_manager, "is_authenticated", False)
        )

        cache_key = f"hashtag:{'auth' if auth_available else 'anon'}:{tag}:{max_posts}"
        ttl = cache_ttl or self._config.cache_profile_ttl

        async def _do_fetch_anon() -> Optional[Dict]:
            await self._rate_limiter.acquire()
            result = await self._with_proxy_retry(
                op_name=f"fetch_hashtag({tag})",
                single_attempt=lambda p: self._fetch_hashtag_attempt(tag, p),
            )
            if not result.get("found"):
                return None
            return {
                "tag":             result["tag"],
                "posts":           result["posts"],
                "has_more":        result["has_more"],
                "related_searches": result["related_searches"],
                "auth_used":       False,
            }

        async def _do_fetch_auth() -> Optional[Dict]:
            all_posts: List[Dict] = []
            max_id = ""
            page = 1
            has_more = False

            while len(all_posts) < max_posts:
                await self._rate_limiter.acquire()
                try:
                    page_data = await self._fetch_hashtag_sections_page(tag, max_id, page)
                except FetchError as e:
                    if not all_posts:
                        raise
                    logger.warning("fetch_hashtag(%s) page %d error: %s", tag, page, e)
                    break

                batch = page_data["posts"]
                if not batch and page == 1:
                    return None
                all_posts.extend(batch)
                has_more  = page_data["more"]
                max_id    = page_data["next_max_id"]
                page     += 1

                if not has_more or not max_id:
                    break

            return {
                "tag":             tag,
                "posts":           all_posts[:max_posts],
                "has_more":        has_more and len(all_posts) >= max_posts,
                "related_searches": [],
                "auth_used":       True,
            }

        if auth_available:
            logger.debug("fetch_hashtag(%s): using auth session (max_posts=%d)", tag, max_posts)
            return await self._cache.get_or_fetch(cache_key, _do_fetch_auth, ttl=ttl)

        logger.debug("fetch_hashtag(%s): using anon HTML parse", tag)
        return await self._cache.get_or_fetch(cache_key, _do_fetch_anon, ttl=ttl)

    # ── Search ────────────────────────────────────────────────────────────────

    async def fetch_search(
        self,
        query: str,
        context: str = "blended",
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Search Instagram for users and/or hashtags.

        context: "blended" (users+hashtags), "user" (users only), "hashtag" (hashtags only)
        Requires auth (cookies). Returns None if auth unavailable or query fails.
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        ttl = cache_ttl if cache_ttl is not None else 300
        cache_key = f"search:{context}:{query.lower()}"

        async def _do_fetch() -> Optional[dict]:
            session = await self._get_auth_session()
            csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""

            headers = {
                "x-csrftoken":      csrf,
                "x-requested-with": "XMLHttpRequest",
            }
            params = {
                "context":      context,
                "query":        query,
                "include_reel": "true",
            }

            for attempt in range(3):
                try:
                    resp = await session.get(
                        "https://www.instagram.com/api/v1/web/search/topsearch/",
                        params=params,
                        headers=headers,
                        timeout=15,
                    )
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"search request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code == 401:
                    return None
                if resp.status_code != 200:
                    raise FetchError(f"search HTTP {resp.status_code}")

                raw = resp.json()

                users = []
                for item in raw.get("users", []):
                    u = item.get("user", {})
                    fs = u.get("friendship_status", {})
                    ctx_type = u.get("search_social_context_snippet_type", "")
                    latest_reel = u.get("latest_reel_media", 0)
                    users.append({
                        "position":            item.get("position", 0),
                        "pk":                  u.get("pk", ""),
                        "username":            u.get("username", ""),
                        "full_name":           u.get("full_name", ""),
                        "is_verified":         u.get("is_verified", False),
                        "is_private":          u.get("is_private", False),
                        "profile_pic_url":     u.get("profile_pic_url", ""),
                        "follower_count_text": u.get("social_context", ""),
                        "social_context_type": ctx_type,
                        "you_follow_them":     fs.get("following", False),
                        "they_follow_you":     fs.get("incoming_request", False),
                        "follow_request_sent": fs.get("outgoing_request", False),
                        "is_bestie":           fs.get("is_bestie", False),
                        "is_restricted":       fs.get("is_restricted", False),
                        "has_recent_reel":     bool(latest_reel),
                        "latest_reel_ts":      latest_reel if latest_reel else None,
                        "has_active_story":    bool(u.get("is_ring_creator", False)),
                        "has_threads":         bool(u.get("show_text_post_app_badge", False)),
                        "download_permission": u.get("third_party_downloads_enabled", 0),
                    })

                hashtags = []
                for item in raw.get("hashtags", []):
                    ht = item.get("hashtag", {})
                    hashtags.append({
                        "position":    item.get("position", 0),
                        "id":          str(ht.get("id", "")),
                        "name":        ht.get("name", ""),
                        "media_count": ht.get("media_count", 0),
                        "subtitle":    ht.get("search_result_subtitle", ""),
                    })

                return {
                    "query":    query,
                    "context":  context,
                    "users":    users,
                    "hashtags": hashtags,
                    "has_more": raw.get("has_more", False),
                }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    # ── Followers / Following / Likers ────────────────────────────────────────

    @staticmethod
    def _shortcode_to_media_id(shortcode: str) -> str:
        """Convert Instagram shortcode to numeric media_id."""
        ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        media_id = 0
        for c in shortcode:
            media_id = media_id * 64 + ALPHA.index(c)
        return str(media_id)

    @staticmethod
    def _parse_follow_user(u: dict, extra_fields: bool = False) -> dict:
        """Parse a user dict from followers/following/likers response."""
        fs = u.get("friendship_status", {})
        result = {
            "pk":              u.get("pk", ""),
            "username":        u.get("username", ""),
            "full_name":       u.get("full_name", ""),
            "is_verified":     u.get("is_verified", False),
            "is_private":      u.get("is_private", False),
            "profile_pic_url": u.get("profile_pic_url", ""),
            "has_recent_reel": bool(u.get("latest_reel_media", 0)),
            "latest_reel_ts":  u.get("latest_reel_media") or None,
        }
        if fs:
            result["you_follow_them"] = fs.get("following", False)
            result["they_follow_you"] = fs.get("followed_by", False)
            result["follow_req_sent"] = fs.get("outgoing_request", False)
            result["is_bestie"]       = fs.get("is_bestie", False)
            result["is_muting"]       = fs.get("muting", False)
            result["is_blocking"]     = fs.get("blocking", False)
        if extra_fields:
            result["is_favorite"] = u.get("is_favorite", False)
        return result

    async def _friendships_get(self, user_pk: str, endpoint: str, params: dict) -> dict:
        """Single authenticated GET to /api/v1/friendships/{pk}/{endpoint}/."""
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        headers = {"x-csrftoken": csrf, "x-requested-with": "XMLHttpRequest"}
        url = f"https://www.instagram.com/api/v1/friendships/{user_pk}/{endpoint}/"

        for attempt in range(3):
            try:
                resp = await session.get(url, params=params, headers=headers, timeout=20)
            except Exception as exc:
                if attempt == 2:
                    raise FetchError(f"friendships/{endpoint} failed: {exc}") from exc
                await asyncio.sleep(1)
                continue

            if resp.status_code == 401:
                raise FetchError("auth required for friendships endpoint")
            if resp.status_code == 404:
                raise FetchError(f"user {user_pk} not found")
            if resp.status_code != 200:
                raise FetchError(f"friendships/{endpoint} HTTP {resp.status_code}")
            return resp.json()

        raise FetchError(f"friendships/{endpoint} failed after 3 attempts")

    async def fetch_followers(
        self,
        user_pk: str,
        max_users: int = 50,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch followers with auto-pagination detection.

        For your OWN account: search_surface=follow_list_page unlocks full
        pagination (next_max_id cursor, same offset pattern as following).
        For OTHER accounts: Instagram limits to ~50 with should_limit=True.

        max_users: ignored if should_limit=True (only ~50 available).
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        ttl = cache_ttl if cache_ttl is not None else 300
        cache_key = f"followers:{user_pk}:{max_users}"

        async def _do_fetch_fol() -> dict:
            all_users: list = []
            max_id = ""
            pages = 0

            while len(all_users) < max_users:
                params: dict = {
                    "count":          "50",
                    "search_surface": "follow_list_page",
                }
                if max_id:
                    params["max_id"] = max_id

                raw      = await self._friendships_get(user_pk, "followers", params)
                batch    = raw.get("users", [])
                all_users.extend(self._parse_follow_user(u) for u in batch)

                should_limit = raw.get("should_limit_list_of_followers", False)
                has_more     = raw.get("has_more", False)
                max_id       = str(raw.get("next_max_id", "")) if has_more else ""
                pages       += 1

                # Stop if Instagram limits (other accounts) or no more pages
                if should_limit or not has_more or not max_id:
                    break

            return {
                "user_pk":       user_pk,
                "users":         all_users[:max_users],
                "has_more":      len(all_users) >= max_users,
                "pages_fetched": pages,
                "should_limit":  raw.get("should_limit_list_of_followers", False),
                "big_list":      raw.get("big_list", False),
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch_fol, ttl=ttl)

    async def fetch_following(
        self,
        user_pk: str,
        max_users: int = 200,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch following list with full pagination (50 per page, next_max_id cursor).
        max_users: how many to fetch total (default 200).
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        ttl = cache_ttl if cache_ttl is not None else 300
        cache_key = f"following:{user_pk}:{max_users}"

        async def _do_fetch_fwg() -> dict:
            all_users: list = []
            max_id = ""
            pages = 0

            while len(all_users) < max_users:
                params: dict = {"count": "50"}
                if max_id:
                    params["max_id"] = max_id

                raw      = await self._friendships_get(user_pk, "following", params)
                batch    = raw.get("users", [])
                all_users.extend(self._parse_follow_user(u, extra_fields=True) for u in batch)
                has_more = raw.get("has_more", False)
                max_id   = str(raw.get("next_max_id", "")) if has_more else ""
                pages   += 1

                if not has_more or not max_id:
                    break

            return {
                "user_pk":      user_pk,
                "users":        all_users[:max_users],
                "has_more":     len(all_users) >= max_users,
                "pages_fetched": pages,
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch_fwg, ttl=ttl)

    async def fetch_post_likers(
        self,
        shortcode: str,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch users who liked a post (~98 returned, no pagination).
        shortcode: post shortcode or full URL.
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        if "/" in shortcode:
            shortcode = [p for p in shortcode.rstrip("/").split("/") if p][-1]

        media_id  = self._shortcode_to_media_id(shortcode)
        ttl       = cache_ttl if cache_ttl is not None else 300
        cache_key = f"likers:{shortcode}"

        async def _do_fetch_lkr() -> dict:
            session = await self._get_auth_session()
            cm2     = self._cookie_manager
            csrf    = (cm2.cookies.get("csrftoken", "") if cm2 else "") or ""
            headers = {"x-csrftoken": csrf, "x-requested-with": "XMLHttpRequest"}
            url     = f"https://www.instagram.com/api/v1/media/{media_id}/likers/"

            for attempt in range(3):
                try:
                    resp = await session.get(url, headers=headers, timeout=15)
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"likers request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code == 401:
                    raise FetchError("auth required for likers endpoint")
                if resp.status_code == 404:
                    raise FetchError(f"post {shortcode} not found")
                if resp.status_code != 200:
                    raise FetchError(f"likers HTTP {resp.status_code}")

                raw   = resp.json()
                users = [self._parse_follow_user(u) for u in raw.get("users", [])]
                return {
                    "shortcode":  shortcode,
                    "media_id":   media_id,
                    "users":      users,
                    "user_count": raw.get("user_count", len(users)),
                }

            raise FetchError("likers failed after 3 attempts")

        return await self._cache.get_or_fetch(cache_key, _do_fetch_lkr, ttl=ttl)

    @staticmethod
    def _parse_story_item(item: dict) -> dict:
        mm = item.get("music_metadata") or {}
        mi = mm.get("music_info") or {}
        mai = mi.get("music_asset_info") or {}
        music_title = mai.get("title") or ""
        music_artist = mai.get("display_artist") or ""

        candidates = (item.get("image_versions2") or {}).get("candidates") or []
        thumbnail_url = candidates[0]["url"] if candidates else ""

        # mentions + hashtags from bloks stickers
        mentions = []
        hashtags = []
        for s in (item.get("story_bloks_stickers") or []):
            bs = s.get("bloks_sticker") or {}
            stype = bs.get("bloks_sticker_type", "")
            sd = bs.get("sticker_data") or {}
            if stype == "mention":
                uname = (sd.get("ig_mention") or {}).get("username", "")
                if uname:
                    mentions.append(uname)
            elif stype == "hashtag":
                tag = (sd.get("ig_hashtag") or {}).get("name", "")
                if tag:
                    hashtags.append(tag)

        # hashtag stickers via story_hashtags (separate field)
        for h in (item.get("story_hashtags") or []):
            tag = (h.get("hashtag") or {}).get("name", "")
            if tag and tag not in hashtags:
                hashtags.append(tag)

        # link stickers — display_url + decoded real URL
        link_stickers = []
        for ls in (item.get("story_link_stickers") or []):
            sl = ls.get("story_link") or {}
            display = sl.get("display_url", "")
            raw_url = sl.get("url", "")
            # decode Instagram redirect → real URL
            try:
                import urllib.parse as _up
                qs = _up.parse_qs(_up.urlparse(raw_url).query)
                real_url = _up.unquote(qs.get("u", [raw_url])[0])
            except Exception:
                real_url = raw_url
            if display or real_url:
                link_stickers.append({"display_url": display, "url": real_url})

        # poll stickers — question + options with vote counts
        polls = []
        for p in (item.get("story_polls") or []):
            ps = p.get("poll_sticker") or {}
            question = ps.get("question", "")
            tallies = [
                {"text": t.get("text", ""), "count": t.get("count", 0)}
                for t in (ps.get("tallies") or [])
            ]
            polls.append({"question": question, "tallies": tallies, "finished": ps.get("finished", False)})

        sfm = item.get("story_feed_media") or []
        linked_post_code = sfm[0].get("media_code", "") if sfm else ""

        # creative config (boomerang / selfie detection)
        cc = item.get("creative_config") or {}
        capture_type = cc.get("capture_type", "")      # "boomerang", "normal", etc.
        camera_facing = cc.get("camera_facing", "")    # "front", "back"

        # which highlight(s) this story belongs to (only present in highlight media)
        hi_added = (item.get("highlights_info") or {}).get("added_to") or []
        highlights_info = [
            {"reel_id": a.get("reel_id", ""), "title": a.get("title", "")}
            for a in hi_added
        ]

        taken_at = item.get("taken_at", 0)
        from datetime import datetime as _dt, timezone as _tz
        try:
            taken_at_str = _dt.fromtimestamp(taken_at, tz=_tz.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            taken_at_str = ""

        return {
            "pk": item.get("pk", ""),
            "shortcode": item.get("code", ""),
            "taken_at": taken_at,
            "taken_at_str": taken_at_str,
            "expiring_at": item.get("expiring_at", 0),
            "media_type": item.get("media_type", 1),
            "duration_secs": item.get("video_duration") or 0.0,
            "width": item.get("original_width", 0),
            "height": item.get("original_height", 0),
            "thumbnail_url": thumbnail_url,
            "caption": (item.get("caption") or ""),
            "accessibility_caption": (item.get("accessibility_caption") or ""),
            "is_paid_partnership": bool(item.get("is_paid_partnership")),
            "can_reshare": bool(item.get("can_reshare")),
            "can_reply": bool(item.get("can_reply")),
            "has_audio": bool(item.get("has_audio")),
            "mentions": mentions,
            "hashtags": hashtags,
            "link_stickers": link_stickers,
            "polls": polls,
            "linked_post_code": linked_post_code,
            "music_title": music_title,
            "music_artist": music_artist,
            "capture_type": capture_type,
            "camera_facing": camera_facing,
            "highlights_info": highlights_info,
        }

    async def fetch_stories(
        self,
        username: str,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch active Instagram Stories for a user (requires cookies).

        Returns dict with username, user_pk, items, story_count, expiring_at,
        can_reply, can_reshare, is_verified. Returns None if not authenticated.
        If reel is null (no active stories), returns empty result.

        Raises:
            FetchError: HTTP errors or non-200 responses
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        user = await self.fetch_user(username)
        if user is None:
            raise FetchError(f"User @{username} not found")

        user_pk = str(user.get("pk") or user.get("id") or "")
        if not user_pk:
            raise FetchError(f"Could not resolve user_pk for @{username}")

        ttl = cache_ttl if cache_ttl is not None else 120
        cache_key = f"stories:{user_pk}"

        async def _do_fetch() -> dict:
            session = await self._get_auth_session()
            cm2 = self._cookie_manager
            csrf = (cm2.cookies.get("csrftoken", "") if cm2 else "") or ""
            headers = {
                "x-csrftoken": csrf,
                "x-ig-app-id": self._config.ig_app_id,
                "x-requested-with": "XMLHttpRequest",
            }
            url = f"https://www.instagram.com/api/v1/feed/user/{user_pk}/story/"

            for attempt in range(3):
                try:
                    resp = await session.get(url, headers=headers, timeout=15)
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"stories request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code == 401:
                    raise FetchError("auth required for stories endpoint")
                if resp.status_code != 200:
                    raise FetchError(f"stories HTTP {resp.status_code}")

                body = resp.json()
                reel = body.get("reel")
                if reel is None:
                    return {
                        "username": username,
                        "user_pk": user_pk,
                        "story_count": 0,
                        "expiring_at": 0,
                        "can_reply": False,
                        "can_reshare": False,
                        "is_verified": bool((user or {}).get("is_verified")),
                        "items": [],
                    }

                raw_items = reel.get("items") or []
                items = [self._parse_story_item(i) for i in raw_items]
                reel_user = reel.get("user") or {}
                return {
                    "username": username,
                    "user_pk": user_pk,
                    "story_count": len(items),
                    "expiring_at": reel.get("expiring_at", 0),
                    "can_reply": bool(reel.get("can_reply")),
                    "can_reshare": bool(reel.get("can_reshare")),
                    "is_verified": bool(reel_user.get("is_verified") or (user or {}).get("is_verified")),
                    "items": items,
                }

            raise FetchError("stories failed after 3 attempts")

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    @staticmethod
    def _parse_highlight(t: dict, media_items: list = None) -> dict:
        cover = (t.get("cover_media") or {})
        cover_url = ((cover.get("cropped_image_version") or {}).get("url") or
                     (cover.get("image_versions2") or {}).get("candidates", [{}])[0].get("url", ""))
        from datetime import datetime as _dt, timezone as _tz
        created_at = t.get("created_at", 0)
        try:
            created_at_str = _dt.fromtimestamp(created_at, tz=_tz.utc).strftime("%Y-%m-%d")
        except Exception:
            created_at_str = ""
        return {
            "id": t.get("id", ""),
            "title": t.get("title", ""),
            "media_count": t.get("media_count", 0),
            "created_at": created_at,
            "created_at_str": created_at_str,
            "updated_at": t.get("updated_timestamp", 0),
            "latest_reel_media": t.get("latest_reel_media", 0),
            "highlight_reel_type": t.get("highlight_reel_type", ""),
            "is_pinned": bool(t.get("is_pinned_highlight")),
            "is_archived": bool(t.get("is_archived")),
            "can_reply": bool(t.get("can_reply")),
            "can_reshare": bool(t.get("can_reshare")),
            "cover_url": cover_url,
            "items": media_items or [],
        }

    @staticmethod
    def _parse_location_or_audio_post(media: dict) -> dict:
        """
        Parse a single media item from location sections or audio reels endpoint.

        Returns a flat dict with shortcode, media_type, like_count, comment_count,
        play_count, taken_at, taken_at_str, username, full_name, is_verified,
        caption, and location_name.
        """
        from datetime import datetime as _dt, timezone as _tz
        user = media.get("user") or {}
        cap  = media.get("caption") or {}
        loc  = media.get("location") or {}

        taken_at = media.get("taken_at") or 0
        try:
            taken_at_str = _dt.fromtimestamp(taken_at, tz=_tz.utc).strftime("%Y-%m-%d %H:%M UTC") if taken_at else ""
        except Exception:
            taken_at_str = ""

        return {
            "shortcode":     media.get("code", ""),
            "media_type":    media.get("media_type", 1),
            "like_count":    media.get("like_count") or 0,
            "comment_count": media.get("comment_count") or 0,
            "play_count":    media.get("play_count") or media.get("ig_play_count") or 0,
            "taken_at":      taken_at,
            "taken_at_str":  taken_at_str,
            "username":      user.get("username", ""),
            "full_name":     user.get("full_name", ""),
            "is_verified":   bool(user.get("is_verified")),
            "caption":       (cap.get("text") or "") if isinstance(cap, dict) else "",
            "location_name": loc.get("name", "") or loc.get("short_name", ""),
        }

    async def fetch_location_posts(
        self,
        location_id: str = "",
        location_name: str = "",
        max_posts: int = 33,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch top posts for an Instagram location (requires auth).

        If location_id is empty, searches by location_name first via
        i.instagram.com/api/v1/location_search/ to resolve an ID.

        Then POSTs to i.instagram.com/api/v1/locations/{loc_id}/sections/
        to get ranked posts.

        Returns:
            dict with keys: location_id, location_name, posts, post_count, more_available
            None if not authenticated.

        Raises:
            FetchError: HTTP errors or search fails to find a location
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        ttl = cache_ttl if cache_ttl is not None else 300
        cache_key = f"location_posts:{location_id or location_name}:{max_posts}"

        async def _do_fetch() -> dict:
            session = await self._get_auth_session()
            cm2 = self._cookie_manager
            csrf = (cm2.cookies.get("csrftoken", "") if cm2 else "") or ""

            loc_id = location_id.strip()
            loc_display_name = location_name.strip()

            # ── Step 1: resolve location_id via search if not provided ──────────
            if not loc_id:
                if not loc_display_name:
                    raise FetchError("fetch_location_posts: provide location_id or location_name")

                search_url = "https://i.instagram.com/api/v1/location_search/"
                try:
                    resp = await session.get(
                        search_url,
                        params={
                            "search_query": loc_display_name,
                            "timestamp":    "0",
                        },
                        headers={
                            "User-Agent":      self._config.ig_user_agent,
                            "Accept":          "*/*",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Origin":          "https://www.instagram.com",
                            "Referer":         "https://www.instagram.com/explore/locations/",
                            "x-ig-app-id":     self._config.ig_app_id,
                            "x-csrftoken":     csrf,
                        },
                        timeout=15,
                    )
                except Exception as exc:
                    raise FetchError(f"location_search request failed: {exc}") from exc

                if resp.status_code != 200:
                    raise FetchError(f"location_search HTTP {resp.status_code}")

                body = resp.json()
                venues = body.get("venues") or []
                if not venues:
                    raise FetchError(f"No location found for query: {loc_display_name!r}")

                first = venues[0]
                loc_id = str(first.get("external_id") or first.get("pk") or "")
                if not loc_id:
                    raise FetchError("location_search returned a venue without an ID")
                loc_display_name = first.get("name", loc_display_name)

            # ── Step 2: fetch ranked posts for the location ──────────────────────
            posts: List[dict] = []
            page = 1
            max_id = ""
            more_available = False

            while len(posts) < max_posts:
                payload: Dict[str, Any] = {
                    "tab":            "ranked",
                    "page":           page,
                    "next_media_ids": "[]",
                    "max_id":         max_id,
                }
                loc_url = f"https://i.instagram.com/api/v1/locations/{loc_id}/sections/"
                try:
                    resp = await session.post(
                        loc_url,
                        data=payload,
                        headers={
                            "User-Agent":      self._config.ig_user_agent,
                            "Accept":          "*/*",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Origin":          "https://www.instagram.com",
                            "Referer":         f"https://www.instagram.com/explore/locations/{loc_id}/",
                            "x-ig-app-id":     self._config.ig_app_id,
                            "x-ig-www-claim":  "0",
                            "x-csrftoken":     csrf,
                            "Content-Type":    "application/x-www-form-urlencoded",
                        },
                        timeout=15,
                    )
                except Exception as exc:
                    raise FetchError(f"location sections request failed: {exc}") from exc

                if resp.status_code == 429:
                    raise FetchError(f"location_posts({loc_id}) rate limited (429)")
                if resp.status_code == 401:
                    raise FetchError(f"location_posts({loc_id}) auth required (401)")
                if resp.status_code not in (200, 201):
                    raise FetchError(f"location_posts({loc_id}) HTTP {resp.status_code}")

                body = resp.json()
                sections = body.get("sections") or []
                more_available = bool(body.get("more_available", False))
                max_id = body.get("next_max_id", "")

                for sec in sections:
                    medias = (sec.get("layout_content") or {}).get("medias") or []
                    for m in medias:
                        media = m.get("media") or {}
                        if not media:
                            continue
                        posts.append(self._parse_location_or_audio_post(media))
                        if len(posts) >= max_posts:
                            break
                    if len(posts) >= max_posts:
                        break

                if not more_available or not max_id:
                    break
                page += 1

            return {
                "location_id":    loc_id,
                "location_name":  loc_display_name,
                "posts":          posts[:max_posts],
                "post_count":     len(posts[:max_posts]),
                "more_available": more_available and len(posts) >= max_posts,
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def fetch_audio_reels(
        self,
        audio_cluster_id: str,
        max_reels: int = 24,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch reels that use a specific audio track (requires auth).

        POSTs to i.instagram.com/api/v1/clips/music/ with the audio_cluster_id.

        Returns:
            dict with: audio_cluster_id, music_title, music_artist,
                       total_reels_str, posts, more_available
            None if not authenticated.

        Raises:
            FetchError: HTTP errors
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        audio_cluster_id = audio_cluster_id.strip()
        ttl = cache_ttl if cache_ttl is not None else 300
        cache_key = f"audio_reels:{audio_cluster_id}:{max_reels}"

        async def _do_fetch() -> dict:
            session = await self._get_auth_session()
            cm2 = self._cookie_manager
            csrf = (cm2.cookies.get("csrftoken", "") if cm2 else "") or ""

            posts: List[dict] = []
            max_id = ""
            more_available = False
            music_title = ""
            music_artist = ""
            total_reels_str = ""

            while len(posts) < max_reels:
                count = min(max_reels - len(posts), 30)
                payload: Dict[str, Any] = {
                    "audio_cluster_id": audio_cluster_id,
                    "count":            count,
                    "max_id":           max_id,
                }
                url = "https://i.instagram.com/api/v1/clips/music/"
                try:
                    resp = await session.post(
                        url,
                        data=payload,
                        headers={
                            "User-Agent":      self._config.ig_user_agent,
                            "Accept":          "*/*",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Origin":          "https://www.instagram.com",
                            "Referer":         f"https://www.instagram.com/reels/audio/{audio_cluster_id}/",
                            "x-ig-app-id":     self._config.ig_app_id,
                            "x-ig-www-claim":  "0",
                            "x-csrftoken":     csrf,
                            "Content-Type":    "application/x-www-form-urlencoded",
                        },
                        timeout=15,
                    )
                except Exception as exc:
                    raise FetchError(f"audio_reels request failed: {exc}") from exc

                if resp.status_code == 429:
                    raise FetchError(f"audio_reels({audio_cluster_id}) rate limited (429)")
                if resp.status_code == 401:
                    raise FetchError(f"audio_reels({audio_cluster_id}) auth required (401)")
                if resp.status_code not in (200, 201):
                    raise FetchError(f"audio_reels({audio_cluster_id}) HTTP {resp.status_code}")

                body = resp.json()
                items = body.get("items") or []
                paging = body.get("paging_info") or {}
                more_available = bool(paging.get("more_available", False))
                max_id = paging.get("max_id", "")

                if not total_reels_str:
                    total_reels_str = body.get("formatted_media_count", "")

                for item in items:
                    media = item.get("media") or {}
                    if not media:
                        continue

                    # Extract music info from first item
                    if not music_title:
                        clips_meta = media.get("clips_metadata") or {}
                        music_info = (
                            (clips_meta.get("music_info") or {})
                            .get("music_asset_info") or {}
                        )
                        orig_sound = clips_meta.get("original_sound_info") or {}
                        music_title  = (
                            music_info.get("title")
                            or orig_sound.get("original_audio_title")
                            or ""
                        )
                        music_artist = (
                            music_info.get("display_artist")
                            or music_info.get("ig_username")
                            or (orig_sound.get("ig_artist") or {}).get("username")
                            or ""
                        )

                    posts.append(self._parse_location_or_audio_post(media))
                    if len(posts) >= max_reels:
                        break

                if not more_available or not max_id or not items:
                    break

            return {
                "audio_cluster_id": audio_cluster_id,
                "music_title":      music_title,
                "music_artist":     music_artist,
                "total_reels_str":  total_reels_str,
                "posts":            posts[:max_reels],
                "more_available":   more_available and len(posts) >= max_reels,
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

    async def fetch_highlights(
        self,
        username: str,
        max_highlights: int = 50,
        include_media: bool = False,
        max_media_highlights: int = 3,
        cache_ttl: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch an account's Highlights tray and optionally media items inside each highlight.

        Returns dict with username, user_pk, is_verified, highlight_count, highlights list.
        Returns None if not authenticated.

        Raises:
            FetchError: HTTP errors
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            return None

        user = await self.fetch_user(username)
        if user is None:
            raise FetchError(f"User @{username} not found")

        user_pk = str(user.get("pk") or user.get("id") or "")
        if not user_pk:
            raise FetchError(f"Could not resolve user_pk for @{username}")

        is_verified = bool((user or {}).get("is_verified"))

        ttl = cache_ttl if cache_ttl is not None else 300
        cache_key = f"highlights:{user_pk}:{include_media}:{max_media_highlights}"

        async def _do_fetch() -> dict:
            session = await self._get_auth_session()
            cm2 = self._cookie_manager
            csrf = (cm2.cookies.get("csrftoken", "") if cm2 else "") or ""
            headers = {
                "x-csrftoken": csrf,
                "x-ig-app-id": self._config.ig_app_id,
                "x-requested-with": "XMLHttpRequest",
            }
            tray_url = f"https://www.instagram.com/api/v1/highlights/{user_pk}/highlights_tray/"

            for attempt in range(3):
                try:
                    resp = await session.get(tray_url, headers=headers, timeout=15)
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"highlights request failed: {exc}") from exc
                    import asyncio as _asyncio
                    await _asyncio.sleep(1)
                    continue

                if resp.status_code == 401:
                    raise FetchError("auth required for highlights endpoint")
                if resp.status_code == 404:
                    raise FetchError(f"user {username} not found")
                if resp.status_code != 200:
                    raise FetchError(f"highlights HTTP {resp.status_code}")

                body = resp.json()
                tray = body.get("tray") or []

                highlights = [self._parse_highlight(t) for t in tray[:max_highlights]]

                if include_media and highlights:
                    top_n = highlights[:max_media_highlights]
                    # batch in chunks of 5 to avoid HTTP 400
                    chunk_size = 5
                    media_map: dict = {}
                    for ci in range(0, len(top_n), chunk_size):
                        chunk = top_n[ci:ci + chunk_size]
                        reel_ids = ",".join(h["id"] for h in chunk)
                        media_url = (
                            f"https://www.instagram.com/api/v1/feed/reels_media/"
                            f"?reel_ids={reel_ids}"
                        )
                        try:
                            media_resp = await session.get(media_url, headers=headers, timeout=20)
                            if media_resp.status_code == 200:
                                for reel in (media_resp.json().get("reels_media") or []):
                                    reel_id = reel.get("id", "")
                                    items = [self._parse_story_item(i) for i in (reel.get("items") or [])]
                                    media_map[reel_id] = items
                            else:
                                logger.warning(
                                    "highlights media chunk HTTP %d for @%s (chunk %d)",
                                    media_resp.status_code, username, ci // chunk_size,
                                )
                        except Exception as exc:
                            logger.warning(
                                "highlights media chunk failed for @%s: %s", username, exc
                            )
                        import asyncio as _aio
                        await _aio.sleep(0.5)   # polite pause between chunks
                    for h in top_n:
                        h["items"] = media_map.get(h["id"], [])

                return {
                    "username": username,
                    "user_pk": user_pk,
                    "is_verified": is_verified,
                    "highlight_count": len(highlights),
                    "highlights": highlights,
                }

            raise FetchError("highlights failed after 3 attempts")

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)
