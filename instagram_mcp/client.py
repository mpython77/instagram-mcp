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
