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
import random
import time
import uuid
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
                session = AsyncSession(
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
                    max_clients=self._config.async_max_clients,
                )
                # Set each cookie with domain=".instagram.com" so libcurl sends them
                # to ALL subdomains (www.instagram.com, i.instagram.com, etc.).
                # Decode octal escapes in values (e.g. rur's \054 → comma) so that
                # Instagram's routing layer receives the correct cookie values.
                if cm and cm.cookies:
                    for name, value in cm.cookies.items():
                        session.cookies.set(
                            name, self._decode_cookie_value(value), domain=".instagram.com"
                        )
                self._auth_session = session
            return self._auth_session

    @staticmethod
    def _decode_cookie_value(value: str) -> str:
        """Decode octal-escaped characters in cookie values.

        Cookie-Editor exports cookies with octal escapes (e.g. \\054 for comma).
        curl_cffi/libcurl interprets these differently from requests, causing
        the rur routing cookie to be mangled and trigger infinite redirect loops.
        Decoding them to real characters before passing to curl_cffi avoids this.
        """
        import re as _re
        return _re.sub(r'\x5c[0-9]{3}', lambda m: chr(int(m.group(0)[1:], 8)), value)

    def _cookie_str(self) -> str:
        """Build a Cookie header string with decoded octal escapes.

        Cookie-Editor exports rur with octal sequences (e.g. \\054 for comma).
        Sending raw \\054 in the Cookie header causes www.instagram.com to return
        302 redirects to login for all API endpoints. Decoding first ensures the
        routing layer receives the correct rur value (commas, not \\054 literals).
        """
        cm = self._cookie_manager
        if not cm:
            return ""
        return "; ".join(
            f"{k}={self._decode_cookie_value(v)}" for k, v in cm.cookies.items()
        )

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
                try:
                    page_result = await self._fetch_single_feed_page(
                        user_id, username, page_size, cursor
                    )
                except FetchError:
                    # Propagate FetchError (RateLimit, Auth, etc.) so the tool can report it
                    raise
                except Exception as exc:
                    logger.warning("Feed page fetch failed for @%s: %s", username, exc)
                    break

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

    async def fetch_media_info(
        self,
        shortcode: str,
        cache_ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch full media info for a single post via /api/v1/media/{id}/info/.

        🔐 Requires authentication (cookies).
        Returns the first 'item' dict from the API response, which includes:
          - media_type (1=image, 2=video, 8=carousel)
          - image_versions2.candidates[0].url  — best image URL
          - video_url                           — present for media_type=2 slides
          - carousel_media[]                    — present for media_type=8
        Each carousel slide follows the same structure as a top-level item.

        Raises:
            FetchError: if auth is missing, post not found, or all retries fail.
        """
        from .parser import shortcode_to_media_id

        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            raise FetchError(
                "instagram_download requires authentication. "
                "Please set up cookies.txt with a valid Instagram session."
            )

        media_id = shortcode_to_media_id(shortcode)
        cache_key = f"media_info:{media_id}"
        ttl = cache_ttl if cache_ttl is not None else self._config.cache_profile_ttl

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            csrf = cm.cookies.get("csrftoken", "") if cm else ""
            url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
            for attempt in range(3):
                try:
                    resp = await session.get(
                        url,
                        headers={
                            "X-IG-App-ID": self._config.ig_app_id,
                            "X-CSRFToken": csrf,
                            "Accept": "application/json",
                        },
                    )
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"media_info({shortcode}) request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code == 404:
                    raise FetchError(f"Post /{shortcode}/ not found — deleted, private, or invalid shortcode.")
                if resp.status_code == 400:
                    raise FetchError(f"Post /{shortcode}/ unavailable (HTTP 400) — may be private or deleted.")
                if resp.status_code == 401:
                    raise FetchError("Session expired. Re-export cookies.txt and restart the server.")
                if resp.status_code != 200:
                    if attempt == 2:
                        raise FetchError(f"media_info({shortcode}): HTTP {resp.status_code}")
                    await asyncio.sleep(1)
                    continue

                try:
                    data = resp.json()
                except (ValueError, TypeError) as exc:
                    raise FetchError(f"media_info({shortcode}): non-JSON response") from exc

                items = data.get("items") or []
                if not items:
                    raise FetchError(f"media_info({shortcode}): empty items list")
                return items[0]

            raise FetchError(f"media_info({shortcode}): all retries exhausted")

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

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

                _taken_at = int(media.get("taken_at") or 0)
                from datetime import datetime as _dt2, timezone as _tz2
                _taken_at_str = (
                    _dt2.fromtimestamp(_taken_at, tz=_tz2.utc).strftime("%Y-%m-%d %H:%M UTC")
                    if _taken_at else ""
                )

                posts.append({
                    # Identity
                    "shortcode":    media.get("code", ""),
                    "url":          f"https://www.instagram.com/p/{media.get('code','')}/",
                    "pk":           str(media.get("pk", "")),
                    "taken_at":     _taken_at,
                    "taken_at_str": _taken_at_str,

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

    # ── Post bulk ─────────────────────────────────────────────────────────────

    async def fetch_post_bulk(
        self,
        shortcodes: List[str],
        max_concurrency: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Fetch multiple posts in parallel.

        Each element is either a parsed dict (ok=True) or an error dict (ok=False).
        The list preserves input order.
        """
        from .parser import parse_post_html

        sem = asyncio.Semaphore(max_concurrency)

        async def _one(sc: str) -> Dict[str, Any]:
            async with sem:
                try:
                    html = await self.fetch_post(sc)
                    info = parse_post_html(html, sc)
                    return {
                        "shortcode":      sc,
                        "ok":             True,
                        "username":       info.username,
                        "full_name":      info.full_name,
                        "is_verified":    info.is_verified,
                        "post_type":      info.post_type,
                        "taken_at_str":   info.taken_at_str,
                        "likes":          info.likes,
                        "comments":       info.comments,
                        "view_count":     info.view_count,
                        "play_count":     info.play_count,
                        "carousel_count": info.carousel_count,
                        "caption":        info.caption,
                        "hashtags":       info.hashtags,
                        "usertags":       info.usertags,
                        "mentions":       info.mentions,
                        "music_artist":   info.music_artist,
                        "music_title":    info.music_title,
                        "location_name":  info.location.name if info.location else "",
                        "post_url":       info.post_url,
                    }
                except FetchError as e:
                    return {"shortcode": sc, "ok": False, "error": str(e)}
                except Exception as e:
                    return {"shortcode": sc, "ok": False, "error": f"parse_error: {e}"}

        return list(await asyncio.gather(*[_one(sc) for sc in shortcodes]))

    # ── Similar accounts ──────────────────────────────────────────────────────

    async def fetch_similar_accounts(
        self,
        username: str,
        limit: int = 20,
        cache_ttl: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch accounts similar to a given user via Instagram's chaining API.

        🔐 Requires authentication (cookies).
        Returns a list of account dicts or None if auth is unavailable.
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

        ttl = cache_ttl if cache_ttl is not None else 600
        cache_key = f"similar:{user_pk}:{limit}"

        async def _do_fetch() -> Optional[List[Dict]]:
            session = await self._get_auth_session()
            cm2 = self._cookie_manager
            csrf = (cm2.cookies.get("csrftoken", "") if cm2 else "") or ""
            headers = {
                "x-csrftoken":      csrf,
                "x-ig-app-id":      self._config.ig_app_id,
                "x-requested-with": "XMLHttpRequest",
                "User-Agent":       self._config.ig_user_agent,
            }
            url = "https://i.instagram.com/api/v1/discover/chaining/"

            for attempt in range(3):
                try:
                    resp = await session.get(
                        url, params={"target_id": user_pk},
                        headers=headers, timeout=15,
                    )
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"similar_accounts request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code == 401:
                    return None
                if resp.status_code == 404:
                    raise FetchError(f"User {username} not found")
                if resp.status_code != 200:
                    raise FetchError(f"similar_accounts HTTP {resp.status_code}")

                users_raw = resp.json().get("users") or []
                accounts = []
                for u in users_raw[:limit]:
                    accounts.append({
                        "username":        u.get("username", ""),
                        "full_name":       u.get("full_name", ""),
                        "pk":              str(u.get("pk", "")),
                        "is_verified":     bool(u.get("is_verified")),
                        "is_private":      bool(u.get("is_private")),
                        "follower_count":  u.get("follower_count"),
                        "biography":       u.get("biography", ""),
                        "profile_pic_url": u.get("profile_pic_url", ""),
                        "category":        u.get("category_name") or u.get("category", ""),
                    })
                return accounts

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=ttl)

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
        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": "936619743392459",
            "x-requested-with": "XMLHttpRequest",
            "Cookie": self._cookie_str(),
        }
        # Try i.instagram.com first (mobile API is more permissive), then www
        hosts = ["https://i.instagram.com", "https://www.instagram.com"]
        last_error = "unknown"

        for host in hosts:
            url = f"{host}/api/v1/friendships/{user_pk}/{endpoint}/"
            try:
                resp = await session.get(url, params=params, headers=headers, timeout=20)
            except Exception as exc:
                last_error = str(exc)
                continue

            if resp.status_code == 404:
                raise FetchError(f"user {user_pk} not found")
            if resp.status_code == 200:
                body = resp.text
                if not body.lstrip().startswith("<"):
                    return _json.loads(body)
            last_error = f"HTTP {resp.status_code}"

        raise FetchError(f"friendships/{endpoint}: {last_error}")

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

    # ─────────────────────────────────────────────────────────────────────────
    # PHOTO UPLOAD
    # ─────────────────────────────────────────────────────────────────────────

    async def upload_photo(
        self,
        image_paths: List[str],
        caption: str = "",
        disable_comments: bool = False,
        hide_like_count: bool = False,
        location_id: str = "",
    ) -> Dict[str, Any]:
        """
        Upload 1–10 images as an Instagram post (single or carousel). Auth required.

        Flow:
          1. Read + validate each image (JPEG natively; PNG → JPEG via Pillow)
          2. POST each image to www.instagram.com/rupload_igphoto/ to get upload_id
          3. POST to /api/v1/media/configure/ (single) or configure_sidecar/ (carousel)

        Returns:
            dict with: ok, post_type, shortcode, url, media_id, caption, images_uploaded
        Raises:
            FetchError: not authenticated, file missing, upload or configure failed
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            raise FetchError(
                "Photo upload requires authentication. "
                "Set up cookies.txt and restart the server."
            )
        if not image_paths:
            raise FetchError("At least one image path is required.")
        if len(image_paths) > 10:
            raise FetchError("Maximum 10 images per post.")

        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        cookie_header = "; ".join(f"{k}={v}" for k, v in (cm.cookies if cm else {}).items())

        is_carousel = len(image_paths) > 1
        uploads: List[tuple] = []
        for path in image_paths:
            item = await self._upload_single_image(session, csrf, cookie_header, path, is_sidecar=is_carousel)
            uploads.append(item)

        if len(uploads) == 1:
            return await self._configure_single(
                session, csrf, uploads[0][0],
                caption, disable_comments, hide_like_count, location_id,
                cookie_header=cookie_header,
            )
        return await self._configure_carousel(
            session, csrf, uploads,
            caption, disable_comments, hide_like_count,
            cookie_header=cookie_header,
        )

    async def _upload_single_image(
        self,
        session: Any,
        csrf: str,
        cookie_header: str,
        path: str,
        is_sidecar: bool = False,
    ) -> tuple:
        """Upload one image file, return (upload_id, width, height)."""
        import os as _os
        import time as _time_mod

        if not _os.path.isfile(path):
            raise FetchError(f"Image file not found: {path!r}")

        with open(path, "rb") as fh:
            raw_bytes = fh.read()

        if not raw_bytes:
            raise FetchError(f"Image file is empty: {path!r}")

        jpeg_bytes, width, height = self._prepare_image(raw_bytes, path)

        upload_id = str(int(time.time() * 1000)) + str(random.randint(100, 999))
        content_len = len(jpeg_bytes)

        rupload_params_dict: Dict[str, Any] = {
            "upload_id":           upload_id,
            "media_type":          "1",
            "upload_media_height": str(height),
            "upload_media_width":  str(width),
            "xsharing_user_ids":   "[]",
            "image_compression":   _json.dumps({
                "lib_name":    "moz",
                "lib_version": "3.1.m",
                "quality":     "87",
            }),
        }
        rupload_params = _json.dumps(rupload_params_dict)

        # Use the web-compatible rupload endpoint
        url = f"https://www.instagram.com/rupload_igphoto/{upload_id}"
        headers = {
            "User-Agent":                  self._config.ig_user_agent,
            "X-Instagram-Rupload-Params":  rupload_params,
            "Content-Type":                "image/jpeg",
            "Content-Length":              str(content_len),
            "X-Entity-Type":               "image/jpeg",
            "X-Entity-Name":               f"instagram_photo_{upload_id}",
            "X-Entity-Length":             str(content_len),
            "Offset":                      "0",
            "Accept-Encoding":             "gzip",
            "x-ig-app-id":                 self._config.ig_app_id,
            "Cookie":                      cookie_header,
            "x-csrftoken":                 csrf,
            "Origin":                      "https://www.instagram.com",
            "Referer":                     "https://www.instagram.com/",
        }

        try:
            resp = await session.post(url, data=jpeg_bytes, headers=headers, timeout=90)
        except Exception as exc:
            raise FetchError(f"rupload request failed for {path!r}: {exc}") from exc

        if resp.status_code == 401:
            raise FetchError("rupload 401 — session expired. Re-export cookies.txt.")
        if resp.status_code == 429:
            raise FetchError("rupload 429 — rate limited. Wait a moment and retry.")
        if resp.status_code not in (200, 201):
            raise FetchError(
                f"rupload HTTP {resp.status_code} for {path!r}: {resp.text[:300]}"
            )

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"rupload returned non-JSON: {resp.text[:200]}")

        uid = str(body.get("upload_id") or "")
        if not uid:
            raise FetchError(f"rupload response missing upload_id: {body}")

        return uid, width, height

    async def _configure_single(
        self,
        session: Any,
        csrf: str,
        upload_id: str,
        caption: str,
        disable_comments: bool,
        hide_like_count: bool,
        location_id: str,
        cookie_header: str = "",
    ) -> Dict[str, Any]:
        """POST /api/v1/media/configure/ to publish a single-image post."""
        cm = self._cookie_manager
        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        payload: Dict[str, Any] = {
            "upload_id":                     upload_id,
            "caption":                       caption,
            "source_type":                   "4",
            "disable_comments":              "1" if disable_comments else "0",
            "like_and_view_counts_disabled": "1" if hide_like_count else "0",
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id
        if location_id:
            payload["location"] = _json.dumps({
                "name":               "",
                "facebook_places_id": location_id,
            })

        url = "https://www.instagram.com/api/v1/media/configure/"
        return await self._post_configure(session, csrf, url, payload, "single", 1, cookie_header=cookie_header)

    async def _configure_carousel(
        self,
        session: Any,
        csrf: str,
        uploads: List[tuple],
        caption: str,
        disable_comments: bool,
        hide_like_count: bool,
        cookie_header: str = "",
    ) -> Dict[str, Any]:
        """POST /api/v1/media/configure_sidecar/ to publish a carousel post."""
        sidecar_id = str(int(time.time() * 1000))
        client_sidecar_id = str(uuid.uuid4())

        cm = self._cookie_manager
        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        children = [
            {
                "upload_id":          upload_id,
                "source_type":        "4",
                "timezone_offset":    "0",
            }
            for upload_id, w, h in uploads
        ]
        payload: Dict[str, Any] = {
            "upload_id":                     sidecar_id,
            "client_sidecar_id":             client_sidecar_id,
            "caption":                       caption,
            "source_type":                   "4",
            "children_metadata":             children,
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id

        url = "https://www.instagram.com/api/v1/media/configure_sidecar/"
        return await self._post_configure(session, csrf, url, payload, "carousel", len(uploads), cookie_header=cookie_header, as_json=True)

    async def publish_story(
        self,
        image_path: str,
        close_friends_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Publish a photo story. Auth required.

        Flow:
          1. Upload image via /rupload_igphoto/ (same as post upload)
          2. POST to /api/v1/media/configure_to_story/ with configure_mode=1

        Returns:
            dict with: ok, media_id, story_url (if code available)
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            raise FetchError("publish_story requires authentication.")

        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        cookie_header = self._cookie_str()

        upload_id, w, h = await self._upload_single_image(
            session, csrf, cookie_header, image_path, is_sidecar=False
        )

        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        payload: Dict[str, Any] = {
            "upload_id":                upload_id,
            "source_type":              "4",
            "configure_mode":           "1",
            "post_to_close_friends_only": "1" if close_friends_only else "0",
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id

        url = "https://www.instagram.com/api/v1/media/configure_to_story/"
        headers = {
            "User-Agent":       self._config.ig_user_agent,
            "Accept":           "*/*",
            "Accept-Language":  "en-US,en;q=0.9",
            "Origin":           "https://www.instagram.com",
            "Referer":          "https://www.instagram.com/",
            "x-ig-app-id":      self._config.ig_app_id,
            "x-csrftoken":      csrf,
            "Content-Type":     "application/x-www-form-urlencoded",
            "Cookie":           cookie_header,
        }
        try:
            resp = await session.post(url, data=payload, headers=headers, timeout=30)
        except Exception as exc:
            raise FetchError(f"configure_to_story failed: {exc}") from exc

        if resp.status_code == 400:
            try:
                msg = resp.json().get("message") or resp.text[:300]
            except Exception:
                msg = resp.text[:300]
            raise FetchError(f"configure_to_story 400: {msg}")
        if resp.status_code == 401:
            raise FetchError("configure_to_story 401 — session expired. Re-export cookies.")
        if resp.status_code not in (200, 201):
            raise FetchError(f"configure_to_story HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"configure_to_story non-JSON: {resp.text[:200]}")

        if body.get("status") == "fail":
            raise FetchError(f"configure_to_story API error: {body.get('message', 'unknown')}")

        media = body.get("media") or {}
        media_id = str(media.get("pk") or media.get("id") or "")
        code = str(media.get("code") or "")

        return {
            "ok":       True,
            "media_id": media_id,
            "story_url": f"https://www.instagram.com/stories/{uid}/{media_id}/" if media_id else "",
        }

    async def _post_configure(
        self,
        session: Any,
        csrf: str,
        url: str,
        payload: Dict[str, Any],
        post_type: str,
        images_count: int,
        cookie_header: str = "",
        as_json: bool = False,
    ) -> Dict[str, Any]:
        """Common POST helper for configure endpoints."""
        headers = {
            "User-Agent":       self._config.ig_user_agent,
            "Accept":           "*/*",
            "Accept-Language":  "en-US,en;q=0.9",
            "Origin":           "https://www.instagram.com",
            "Referer":          "https://www.instagram.com/",
            "x-ig-app-id":      self._config.ig_app_id,
            "x-csrftoken":      csrf,
            "X-Requested-With": "XMLHttpRequest",
            "X-Instagram-AJAX": "1",
        }
        if as_json:
            headers["Content-Type"] = "application/json"
            data = _json.dumps(payload)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = payload

        if cookie_header:
            headers["Cookie"] = cookie_header

        try:
            resp = await session.post(url, data=data, headers=headers, timeout=30)
        except Exception as exc:
            raise FetchError(f"configure request failed: {exc}") from exc

        if resp.status_code == 400:
            try:
                body = resp.json()
                msg = body.get("message") or body.get("error_title") or resp.text[:300]
            except Exception:
                msg = resp.text[:300]
            raise FetchError(f"configure 400 — {msg}")
        if resp.status_code == 401:
            raise FetchError("configure 401 — session expired. Re-export cookies.txt.")
        if resp.status_code == 429:
            raise FetchError("configure 429 — rate limited. Wait a moment and retry.")
        if resp.status_code not in (200, 201):
            raise FetchError(f"configure HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"configure returned non-JSON: {resp.text[:200]}")

        media = body.get("media") or {}
        code = str(media.get("code") or "")
        media_id = str(media.get("pk") or media.get("id") or "")

        return {
            "ok":             True,
            "post_type":      post_type,
            "shortcode":      code,
            "url":            f"https://www.instagram.com/p/{code}/" if code else "",
            "media_id":       media_id,
            "caption":        payload.get("caption", ""),
            "images_uploaded": images_count,
        }

    @staticmethod
    def _prepare_image(raw_bytes: bytes, path: str) -> tuple:
        """
        Validate and normalise image bytes to JPEG.

        Returns (jpeg_bytes, width, height).
        Accepts JPEG directly. Converts PNG (and other formats) via Pillow.
        """
        import struct as _struct

        # JPEG: FF D8 FF
        if raw_bytes[:3] == b"\xff\xd8\xff":
            width, height = InstagramClient._jpeg_dimensions(raw_bytes)
            return raw_bytes, width, height

        # PNG: 89 50 4E 47  — read dimensions from IHDR chunk at bytes 16-24
        is_png = raw_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        if is_png and len(raw_bytes) >= 24:
            width  = _struct.unpack(">I", raw_bytes[16:20])[0]
            height = _struct.unpack(">I", raw_bytes[20:24])[0]
        else:
            width = height = 0

        # Convert to JPEG via Pillow
        try:
            from PIL import Image as _PILImage
            import io as _io
            img = _PILImage.open(_io.BytesIO(raw_bytes))
            if width == 0:
                width, height = img.size
            img = img.convert("RGB")
            out = _io.BytesIO()
            img.save(out, format="JPEG", quality=87, optimize=True)
            return out.getvalue(), width, height
        except ImportError:
            raise FetchError(
                f"Image {path!r} is not a JPEG. "
                "Install Pillow to support PNG and other formats: pip install Pillow"
            )
        except Exception as exc:
            raise FetchError(f"Failed to convert image {path!r} to JPEG: {exc}") from exc

    @staticmethod
    def _jpeg_dimensions(data: bytes) -> tuple:
        """Extract width and height from JPEG SOF markers."""
        import struct as _struct
        i = 2  # skip FF D8
        while i + 8 < len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            ):
                height = _struct.unpack(">H", data[i + 5:i + 7])[0]
                width  = _struct.unpack(">H", data[i + 7:i + 9])[0]
                return width, height
            seg_len = _struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
        return 1080, 1080  # safe fallback

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

    # ── Direct Messages ──────────────────────────────────────────────────────

    async def fetch_dm_inbox(
        self,
        limit: int = 20,
        cursor: Optional[str] = None,
        cache_ttl: int = 30,
    ) -> Dict[str, Any]:
        """Fetch DM inbox threads (requires cookies)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("DM inbox requires authentication. Set up cookies.txt.")

        cache_key = f"dm_inbox:{cursor or 'first'}:{limit}"

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            csrf = (cm.cookies.get("csrftoken", "")) or ""
            params: Dict[str, str] = {
                "visual_message_return_type": "unseen",
                "direction": "older",
                "limit": str(limit),
            }
            if cursor:
                params["cursor"] = cursor

            resp = await session.get(
                "https://www.instagram.com/api/v1/direct_v2/inbox/",
                params=params,
                headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id},
            )
            status = resp.status_code
            if status in (401, 403):
                raise FetchError("DM inbox: session expired. Re-export cookies.txt.")
            if status != 200:
                raise FetchError(f"DM inbox: HTTP {status}")
            try:
                body = resp.json()
            except Exception:
                raise FetchError("DM inbox: invalid JSON response")

            inbox = body.get("inbox") or {}
            threads_raw = inbox.get("threads") or []
            threads = []
            for t in threads_raw:
                users = [
                    {
                        "user_id": str(u.get("pk") or u.get("id") or ""),
                        "username": u.get("username", ""),
                        "full_name": u.get("full_name", ""),
                        "is_verified": bool(u.get("is_verified")),
                    }
                    for u in (t.get("users") or [])
                ]
                items_list = t.get("items") or []
                last_item = items_list[0] if items_list else {}
                threads.append({
                    "thread_id": t.get("thread_v2_id") or t.get("thread_id", ""),
                    "thread_title": t.get("thread_title", ""),
                    "is_group": bool(t.get("is_group")),
                    "users": users,
                    "has_unread": t.get("read_state", 0) != 0,
                    "last_activity_at": t.get("last_activity_at", 0),
                    "last_message_type": last_item.get("item_type", ""),
                    "last_message_text": (
                        last_item.get("text", "")
                        if last_item.get("item_type") == "text" else ""
                    ),
                })
            return {
                "threads": threads,
                "has_older": bool(inbox.get("has_older")),
                "oldest_cursor": inbox.get("oldest_cursor", ""),
                "count": len(threads),
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=cache_ttl)

    async def fetch_dm_thread(
        self,
        thread_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
        cache_ttl: int = 30,
    ) -> Dict[str, Any]:
        """Fetch messages in a DM thread with media content, read receipts, and pagination."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("DM thread requires authentication. Set up cookies.txt.")

        cache_key = f"dm_thread:{thread_id}:{cursor or 'first'}:{limit}"

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            csrf = (cm.cookies.get("csrftoken", "")) or ""
            my_user_id = (cm.cookies.get("ds_user_id", "")) or ""
            params: Dict[str, str] = {"limit": str(limit)}
            if cursor:
                params["cursor"] = cursor

            resp = await session.get(
                f"https://www.instagram.com/api/v1/direct_v2/threads/{thread_id}/",
                params=params,
                headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id},
            )
            status = resp.status_code
            if status in (401, 403):
                raise FetchError("DM thread: session expired. Re-export cookies.")
            if status == 404:
                raise FetchError(f"Thread {thread_id!r} not found.")
            if status != 200:
                raise FetchError(f"DM thread: HTTP {status}")
            try:
                body = resp.json()
            except Exception:
                raise FetchError("DM thread: invalid JSON response")

            thread = body.get("thread") or {}

            # ── Read receipts: last seen item_id per user ─────────────────────
            last_seen_at: Dict[str, Any] = thread.get("last_seen_at") or {}
            # Map user_id → last seen item_id (as int for comparison)
            seen_item_ids: Dict[str, int] = {}
            for uid, info in last_seen_at.items():
                raw_iid = info.get("item_id", "")
                try:
                    seen_item_ids[str(uid)] = int(raw_iid)
                except (ValueError, TypeError):
                    pass

            # ── Build username map from participants ──────────────────────────
            users_raw = thread.get("users") or []
            uid_to_username: Dict[str, str] = {}
            for u in users_raw:
                uid = str(u.get("pk") or u.get("id") or "")
                if uid:
                    uid_to_username[uid] = u.get("username", uid)

            # ── Parse messages ────────────────────────────────────────────────
            items_raw = thread.get("items") or []
            messages = []
            for item in items_raw:
                item_id_str = item.get("item_id", "")
                user_id_str = str(item.get("user_id", ""))
                ts = item.get("timestamp", 0)
                itype = item.get("item_type", "")
                is_mine = user_id_str == my_user_id

                # ── Read status ───────────────────────────────────────────────
                # A message is "read" if at least one OTHER participant has seen
                # an item at or after this message's item_id (snowflake ordering).
                try:
                    item_id_int = int(item_id_str)
                except (ValueError, TypeError):
                    item_id_int = 0

                read_by: List[str] = []
                for uid, last_iid in seen_item_ids.items():
                    if uid != user_id_str and last_iid >= item_id_int:
                        read_by.append(uid_to_username.get(uid, uid))

                msg: Dict[str, Any] = {
                    "item_id": item_id_str,
                    "user_id": user_id_str,
                    "username": "me" if is_mine else uid_to_username.get(user_id_str, user_id_str),
                    "timestamp": ts,
                    "item_type": itype,
                    "is_mine": is_mine,
                    "read_by": read_by,
                    "is_read": bool(read_by) if is_mine else True,
                }

                # ── Content by type ───────────────────────────────────────────
                if itype == "text":
                    msg["text"] = item.get("text", "")

                elif itype == "like":
                    msg["text"] = "❤️"

                elif itype == "media_share":
                    shared = item.get("media_share") or {}
                    code = shared.get("code", "")
                    media_type = shared.get("media_type", 1)  # 1=photo,2=video,8=carousel
                    caption_data = shared.get("caption") or {}
                    caption = (
                        caption_data.get("text", "")[:120]
                        if isinstance(caption_data, dict)
                        else str(caption_data)[:120]
                    )
                    candidates = (shared.get("image_versions2") or {}).get("candidates") or []
                    thumb_url = candidates[0].get("url", "") if candidates else ""
                    video_versions = shared.get("video_versions") or []
                    video_url = video_versions[0].get("url", "") if video_versions else ""
                    media_label = {1: "photo", 2: "video", 8: "carousel"}.get(media_type, "media")
                    msg["text"] = f"[shared {media_label}]"
                    msg["media_url"] = f"https://www.instagram.com/p/{code}/" if code else ""
                    msg["thumb_url"] = thumb_url
                    msg["video_url"] = video_url
                    msg["caption"] = caption
                    msg["media_type"] = media_label

                elif itype == "raven_media":
                    vm = item.get("visual_media") or {}
                    media = vm.get("media") or {}
                    candidates = (media.get("image_versions2") or {}).get("candidates") or []
                    thumb_url = candidates[0].get("url", "") if candidates else ""
                    video_versions = media.get("video_versions") or []
                    video_url = video_versions[0].get("url", "") if video_versions else ""
                    media_label = "video" if video_url else "photo"
                    msg["text"] = f"[disappearing {media_label}]"
                    msg["thumb_url"] = thumb_url
                    msg["video_url"] = video_url
                    msg["media_type"] = f"disappearing_{media_label}"

                elif itype == "voice_media":
                    voice = (item.get("voice_media") or {}).get("media") or {}
                    duration_ms = voice.get("audio", {}).get("duration", 0)
                    msg["text"] = f"[voice message {duration_ms // 1000}s]"
                    msg["audio_url"] = (voice.get("audio") or {}).get("audio_src", "")

                elif itype == "animated_media":
                    gif = (item.get("animated_media") or {}).get("images", {})
                    fixed = (gif.get("fixed_height") or {})
                    msg["text"] = "[GIF]"
                    msg["thumb_url"] = fixed.get("url", "")

                elif itype == "story_share":
                    story = item.get("story_share") or {}
                    msg["text"] = "[story reply]"
                    msg["story_username"] = story.get("text", "")

                elif itype == "action_log":
                    log = item.get("action_log") or {}
                    msg["text"] = f"[{log.get('description', 'action')}]"

                elif itype == "placeholder":
                    # Deleted message or automated/bot message with no visible content
                    msg["text"] = "[message unavailable]"

                elif itype == "xma_media_share":
                    xma = item.get("xma_media_share") or {}
                    title = xma.get("title", "")
                    preview = xma.get("preview_url", "")
                    msg["text"] = f"[shared: {title}]" if title else "[shared content]"
                    msg["media_url"] = preview

                elif itype == "link":
                    link = item.get("link") or {}
                    msg["text"] = link.get("text", "[link]")
                    context = link.get("link_context") or {}
                    msg["media_url"] = context.get("link_url", "")

                else:
                    msg["text"] = f"[{itype}]"

                messages.append(msg)

            participants = [
                {
                    "user_id": str(u.get("pk") or u.get("id") or ""),
                    "username": u.get("username", ""),
                    "full_name": u.get("full_name", ""),
                }
                for u in users_raw
            ]

            # Pagination: prev_cursor loads OLDER messages
            prev_cursor = thread.get("prev_cursor", "")
            # MINCURSOR/MAXCURSOR are Instagram's boundary markers — treat as "no more"
            if prev_cursor in ("MINCURSOR", "MAXCURSOR", ""):
                prev_cursor = ""
            has_older = bool(thread.get("has_older")) and bool(prev_cursor)

            return {
                "thread_id": thread_id,
                "thread_title": thread.get("thread_title", ""),
                "is_group": bool(thread.get("is_group")),
                "participants": participants,
                "messages": messages,
                "message_count": len(messages),
                "has_older": has_older,
                "prev_cursor": prev_cursor,
                "oldest_cursor": thread.get("oldest_cursor", ""),
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=cache_ttl)

    async def _fetch_fb_tokens(self) -> Tuple[str, str]:
        """Fetch fb_dtsg and lsd tokens from Instagram homepage.

        If authenticated, uses the current session to get account-linked tokens
        via CookieManager. Otherwise falls back to an anonymous (no-cookie)
        fetch with HTTP/1.1 to force the legacy page format which embeds tokens.
        """
        import re as _re

        # 1. Prefer authenticated tokens if we have a session
        if self._cookie_manager and self._cookie_manager.is_authenticated:
            try:
                session = await self._get_auth_session()
                return await self._cookie_manager.ensure_csrf_tokens(session)
            except Exception as exc:
                logger.warning(
                    "Authenticated CSRF fetch failed (session may be stale), "
                    "falling back to anonymous: %s",
                    exc,
                )

        # 2. Anonymous fallback (for public tools or if auth fetch failed)
        from curl_cffi.requests import AsyncSession as _AsyncSession
        from curl_cffi import CurlHttpVersion as _CurlHttpVersion

        # HTTP/1.1 + no impersonation forces legacy page format
        tmp = _AsyncSession(http_version=_CurlHttpVersion.V1_1)
        try:
            resp = await tmp.get(
                "https://www.instagram.com/",
                headers={
                    "User-Agent": self._config.ig_user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=20,
                allow_redirects=True,
            )
            html = resp.text
        finally:
            await tmp.close()

        m = _re.search(r'"dtsg":\{"token":"([^"]+)"', html)
        if not m:
            m = _re.search(r'"DTSG","[^"]*","([^"]+)"', html)
        if not m:
            raise FetchError(
                "Could not extract fb_dtsg from Instagram web. "
                "Session may be expired — re-export cookies."
            )
        fb_dtsg = m.group(1)
        m2 = _re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
        lsd = m2.group(1) if m2 else fb_dtsg[:16]
        return fb_dtsg, lsd

    async def resolve_dm_thread_igid(self, username: str) -> str:
        """Resolve a username to its DM thread igid (web thread_v2_id format).

        Strategy:
        1. Search existing inbox threads for a matching username.
        2. If not found, get user_id then call get_or_create via www (not mobile).
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        username_lower = username.lower()

        # Step 1: Search inbox for existing thread
        inbox_resp = await session.get(
            "https://www.instagram.com/api/v1/direct_v2/inbox/?limit=40",
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id},
        )
        if inbox_resp.status_code == 200:
            try:
                inbox_data = inbox_resp.json()
                threads = (inbox_data.get("inbox") or {}).get("threads") or []
                for t in threads:
                    for u in (t.get("users") or []):
                        if (u.get("username") or "").lower() == username_lower:
                            igid = t.get("thread_v2_id") or t.get("thread_id")
                            if igid:
                                return str(igid)
            except Exception:
                pass

        # Step 2: Get user_id, then get_or_create via www
        _ck = self._cookie_str()
        resp = await session.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers={"x-ig-app-id": self._config.ig_app_id, "x-csrftoken": csrf, "Cookie": _ck},
        )
        if resp.status_code != 200:
            raise FetchError(f"Could not fetch profile for '{username}': HTTP {resp.status_code}")
        try:
            pdata = resp.json()
        except Exception:
            raise FetchError(f"Invalid JSON from profile API for '{username}'")
        user = (pdata.get("data") or {}).get("user") or {}
        user_id = user.get("id") or user.get("pk")
        if not user_id:
            raise FetchError(f"User '{username}' not found.")

        tdata = None
        for gc_host in ["https://www.instagram.com", "https://i.instagram.com"]:
            resp2 = await session.post(
                f"{gc_host}/api/v1/direct_v2/threads/get_or_create/",
                data={
                    "recipient_users": f"[[{user_id}]]",
                    "use_unified_inbox": "true",
                },
                headers={
                    "x-csrftoken": csrf,
                    "x-ig-app-id": self._config.ig_app_id,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": _ck,
                    "referer": "https://www.instagram.com/direct/inbox/",
                },
                allow_redirects=False,
            )
            if resp2.status_code in (200, 201):
                try:
                    body_check = resp2.text
                    if not body_check.lstrip().startswith("<"):
                        tdata = resp2.json()
                        if tdata.get("thread") or tdata.get("thread_id"):
                            break
                except Exception:
                    pass
        if tdata is None:
            raise FetchError(
                f"Could not find/create DM thread for '{username}': HTTP {resp2.status_code}"
            )
        thread = tdata.get("thread") or {}
        igid = thread.get("thread_v2_id") or thread.get("thread_id")
        if not igid:
            raise FetchError(f"Could not resolve thread igid for '{username}'")
        return str(igid)

    async def send_dm_text(self, thread_id: str, text: str) -> Dict[str, Any]:
        """Send a text message via Instagram Web GraphQL (IGDirectTextSendMutation)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("Send DM requires authentication. Set up cookies.txt.")
        if not text.strip():
            raise FetchError("Message text cannot be empty.")
        if len(text) > 1000:
            raise FetchError("Message too long (max 1000 chars).")

        csrf = (cm.cookies.get("csrftoken", "")) or ""
        ds_user_id = (cm.cookies.get("ds_user_id", "0")) or "0"

        last_error = "unknown error"
        for attempt in range(3):
            if attempt > 0:
                await asyncio.sleep(attempt * 4)
                cm.invalidate_csrf_cache()
                logger.debug("send_dm_text retry %d/3", attempt + 1)

            try:
                fb_dtsg, lsd = await self._fetch_fb_tokens()
            except Exception as exc:
                last_error = str(exc)
                logger.warning("send_dm_text: token fetch failed (attempt %d): %s", attempt + 1, exc)
                continue

            offline_id = str(int(time.time() * 1000) * (2 ** 22) + random.randint(0, (2 ** 22) - 1))
            variables = {
                "ig_thread_igid": thread_id,
                "offline_threading_id": offline_id,
                "recipient_igids": None,
                "replied_to_client_context": None,
                "replied_to_item_id": None,
                "reply_to_message_id": None,
                "sampled": None,
                "text": {"sensitive_string_value": text},
                "mentions": [],
                "mentioned_user_ids": [],
                "commands": None,
                "forwarded_from_thread_id": None,
                "is_forwarded_from_own_message": None,
                "send_attribution": "igd_web_chat_tab:in_thread",
            }
            data = {
                "av": ds_user_id,
                "__d": "www",
                "__user": ds_user_id,
                "__a": "1",
                "__req": str(random.randint(10, 99)),
                "dpr": "1",
                "__ccg": "GOOD",
                "fb_dtsg": fb_dtsg,
                "jazoest": "2" + str(sum(ord(c) for c in fb_dtsg)),
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "IGDirectTextSendMutation",
                "server_timestamps": "true",
                "variables": _json.dumps(variables),
                "doc_id": "26911679871773184",
            }

            session = await self._get_auth_session()
            resp = await session.post(
                "https://www.instagram.com/api/graphql",
                data=data,
                headers={
                    "x-csrftoken": csrf,
                    "x-fb-friendly-name": "IGDirectTextSendMutation",
                    "x-fb-lsd": lsd,
                    "x-ig-app-id": self._config.ig_app_id,
                    "x-ig-www-claim": "0",
                    "x-asbd-id": "129477",
                    "x-instagram-ajax": "1",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://www.instagram.com/direct/t/{thread_id}/",
                    "Origin": "https://www.instagram.com",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                },
                allow_redirects=False,
            )

            body_text = resp.text
            if body_text.startswith("for (;;);"):
                body_text = body_text[9:]

            if resp.status_code in (301, 302, 303, 307, 308):
                last_error = "GraphQL redirected (session rate-limited)"
                cm.invalidate_csrf_cache()
                continue
            if resp.status_code in (401, 403):
                raise FetchError("Send DM: session expired. Re-export cookies.")
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue

            try:
                body = _json.loads(body_text)
            except Exception:
                last_error = f"invalid JSON: {body_text[:200]}"
                continue

            # Instagram bot-detection: session flagged — invalidate tokens and retry
            if "1357001" in body_text or '"not-logged-in"' in body_text:
                last_error = "session flagged by Instagram (error 1357001)"
                cm.invalidate_csrf_cache()
                logger.warning("send_dm_text: error 1357001 on attempt %d, retrying", attempt + 1)
                continue

            if "error" in body:
                err_val = body.get("error")
                err_code = err_val.get("code") if isinstance(err_val, dict) else None
                if err_code == 1357001:
                    last_error = "session flagged (1357001)"
                    cm.invalidate_csrf_cache()
                    continue
                raise FetchError(
                    f"Send DM failed: {body.get('errorSummary', err_val)}"
                )

            inner = ((body.get("data") or {}).get(
                "xig_direct_text_send_with_slide_messaging_response"
            ) or {})
            msg_id = inner.get("message_id", "")
            if not msg_id:
                raise FetchError(f"Send DM: no message_id in response: {body_text[:300]}")

            return {
                "status": "sent",
                "item_id": msg_id,
                "timestamp": int(inner.get("timestamp_ms", 0)),
                "thread_id": thread_id,
            }

        raise FetchError(f"Send DM failed after 3 attempts: {last_error}")

    async def send_dm_to_username(self, username: str, text: str) -> Dict[str, Any]:
        """Resolve username → thread igid, then send DM via GraphQL."""
        username = username.lstrip("@")
        thread_igid = await self.resolve_dm_thread_igid(username)
        result = await self.send_dm_text(thread_igid, text)
        result["username"] = username
        return result

    async def _gql_mutation(
        self,
        doc_id: str,
        variables: Dict[str, Any],
        friendly_name: str,
        fb_dtsg: str,
        lsd: str,
    ) -> Dict[str, Any]:
        """Generic GraphQL mutation via Instagram web API."""
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        ds_user_id = (cm.cookies.get("ds_user_id", "0") if cm else "0") or "0"
        session = await self._get_auth_session()
        data = {
            "av": ds_user_id, "__d": "www", "__user": ds_user_id, "__a": "1",
            "fb_dtsg": fb_dtsg, "lsd": lsd,
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": friendly_name,
            "server_timestamps": "true",
            "variables": _json.dumps(variables),
            "doc_id": doc_id,
        }
        resp = await session.post(
            "https://www.instagram.com/api/graphql",
            data=data,
            headers={
                "x-csrftoken": csrf, "x-fb-lsd": lsd,
                "x-fb-friendly-name": friendly_name,
                "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/direct/inbox/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(
                f"{friendly_name}: GraphQL redirected (session may be rate-limited). "
                f"Location: {resp.headers.get('location', 'unknown')}"
            )
        body = resp.text
        if body.startswith("for (;;);"):
            body = body[9:]
        if resp.status_code not in (200, 201):
            raise FetchError(f"{friendly_name}: HTTP {resp.status_code}")
        try:
            return _json.loads(body)
        except Exception:
            raise FetchError(f"{friendly_name}: invalid JSON: {body[:200]}")

    async def _gql_mutation_with_retry(
        self,
        doc_id: str,
        variables: Dict[str, Any],
        friendly_name: str,
    ) -> Dict[str, Any]:
        """Fetch CSRF tokens and run a GraphQL mutation, retrying on session-flagged errors."""
        cm = self._cookie_manager
        last_error = "unknown"
        for attempt in range(3):
            if attempt > 0:
                await asyncio.sleep(attempt * 4)
                cm.invalidate_csrf_cache()
                logger.debug("%s retry %d/3", friendly_name, attempt + 1)
            try:
                fb_dtsg, lsd = await self._fetch_fb_tokens()
            except Exception as exc:
                last_error = str(exc)
                continue
            try:
                body = await self._gql_mutation(doc_id, variables, friendly_name, fb_dtsg, lsd)
            except FetchError as exc:
                last_error = str(exc)
                if "1357001" in last_error or "redirected" in last_error:
                    cm.invalidate_csrf_cache()
                    continue
                raise
            body_str = str(body)
            if "1357001" in body_str or '"not-logged-in"' in body_str:
                last_error = "session flagged (1357001)"
                cm.invalidate_csrf_cache()
                continue
            return body
        raise FetchError(f"{friendly_name} failed after 3 attempts: {last_error}")

    async def dm_react(self, thread_id: str, item_id: str, emoji: str = "❤") -> Dict[str, Any]:
        """React to a DM message with an emoji (default: ❤)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_react requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="3672524849516997",
            variables={"thread_id": thread_id, "item_id": item_id, "reaction": emoji},
            friendly_name="IGDirectSendEmojiReactionMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_react failed: {body}")
        return {"status": "reacted", "thread_id": thread_id, "item_id": item_id, "emoji": emoji}

    async def dm_unreact(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        """Remove emoji reaction from a DM message."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_unreact requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="3672524849516997",
            variables={"thread_id": thread_id, "item_id": item_id, "reaction": ""},
            friendly_name="IGDirectSendEmojiReactionMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_unreact failed: {body}")
        return {"status": "unreacted", "thread_id": thread_id, "item_id": item_id}

    async def dm_unsend(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        """Delete/unsend a DM message."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_unsend requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="7166420300085783",
            variables={"thread_id": thread_id, "item_id": item_id},
            friendly_name="IGDirectDeleteItemMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_unsend failed: {body}")
        return {"status": "deleted", "thread_id": thread_id, "item_id": item_id}

    async def dm_mark_seen(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        """Mark a DM thread as seen up to the given item_id."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_mark_seen requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="5994298984009617",
            variables={"thread_id": thread_id, "last_seen_at": item_id},
            friendly_name="IGDirectMarkThreadSeenMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_mark_seen failed: {body}")
        return {"status": "seen", "thread_id": thread_id, "item_id": item_id}

    async def post_comment(self, media_id: str, text: str) -> Dict[str, Any]:
        """Post a comment on an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("post_comment requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        _base_hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        # Try www endpoint first
        resp = await session.post(
            f"https://www.instagram.com/api/v1/media/{media_id}/comment/",
            data={
                "comment_text": text,
                "idempotence_token": str(int(time.time() * 1000)),
            },
            headers=_base_hdrs,
            allow_redirects=False,
        )
        body_text = resp.text
        if resp.status_code in (301, 302, 303, 307, 308) or resp.status_code not in (200, 201) or body_text.startswith("<!"):
            # Fall back to i.instagram.com
            resp = await session.post(
                f"https://i.instagram.com/api/v1/media/{media_id}/comment/",
                data={
                    "comment_text": text,
                    "idempotence_token": str(int(time.time() * 1000)),
                },
                headers=_base_hdrs,
                allow_redirects=False,
            )
            body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"post_comment: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"post_comment: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"post_comment: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"post_comment: API error: {body.get('message', 'unknown')}")
        comment = body.get("comment") or {}
        comment_id = str(comment.get("pk") or comment.get("id") or "")
        return {
            "status": "commented",
            "comment_id": comment_id,
            "text": text,
            "media_id": media_id,
        }

    async def delete_comment(self, media_id: str, comment_id: str) -> Dict[str, Any]:
        """Delete a comment on an Instagram post (own comment or on own post)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("delete_comment requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        headers = {
            "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/media/{media_id}/comment/{comment_id}/delete/",
                data={"comment_or_caption": "0"},
                headers=headers,
                allow_redirects=False,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                continue
            body_text = resp.text
            if resp.status_code not in (200, 201):
                raise FetchError(f"delete_comment: HTTP {resp.status_code}: {body_text[:200]}")
            if body_text.lstrip().startswith("<"):
                raise FetchError(f"delete_comment: got HTML (session blocked): {body_text[:150]}")
            try:
                body = _json.loads(body_text)
            except Exception:
                raise FetchError(f"delete_comment: invalid JSON: {body_text[:200]}")
            if body.get("status") == "fail":
                raise FetchError(f"delete_comment: API error: {body.get('message', 'unknown')}")
            return {"status": "deleted", "comment_id": comment_id, "media_id": media_id}
        raise FetchError("delete_comment: all hosts redirected (session rate-limited)")

    async def search_users(self, query: str, count: int = 10) -> List[Dict[str, Any]]:
        """Search Instagram users by query string."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("search_users requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""

        _ck = self._cookie_str()
        _sh = {"x-csrftoken": csrf, "x-ig-app-id": "936619743392459", "Cookie": _ck}
        # Try i.instagram.com (mobile API) first, fall back to web topsearch
        resp = await session.get(
            "https://i.instagram.com/api/v1/users/search/",
            params={"query": query, "count": str(count)},
            headers=_sh,
        )
        if resp.status_code != 200 or resp.text.lstrip().startswith("<"):
            resp = await session.get(
                "https://www.instagram.com/web/search/topsearch/",
                params={"query": query, "context": "blended", "count": str(count)},
                headers=_sh,
            )
        if resp.status_code != 200:
            raise FetchError(f"search_users: HTTP {resp.status_code}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"search_users: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"search_users: invalid JSON: {body_text[:200]}")

        results = []
        # web topsearch: users[] → {user: {...}}
        for entry in body.get("users") or []:
            u = entry.get("user") or entry  # api/v1/users/search returns flat list
            results.append({
                "user_id": str(u.get("pk") or u.get("id") or ""),
                "username": u.get("username", ""),
                "full_name": u.get("full_name", ""),
                "is_verified": bool(u.get("is_verified")),
                "is_private": bool(u.get("is_private")),
                "follower_count": u.get("follower_count"),
                "profile_pic_url": u.get("profile_pic_url", ""),
            })
        return results

    async def get_user_followers(
        self, user_id: str, count: int = 50, max_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get followers list for a user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("get_user_followers requires authentication.")
        params: Dict[str, str] = {"count": str(count), "search_surface": "follow_list_page"}
        if max_id:
            params["max_id"] = max_id
        body = await self._friendships_get(user_id, "followers", params)
        users = [
            {
                "user_id": str(u.get("pk") or u.get("id") or ""),
                "username": u.get("username", ""),
                "full_name": u.get("full_name", ""),
                "is_verified": bool(u.get("is_verified")),
                "is_private": bool(u.get("is_private")),
            }
            for u in (body.get("users") or [])
        ]
        return {
            "users": users,
            "count": len(users),
            "next_max_id": body.get("next_max_id", ""),
            "has_more": bool(body.get("next_max_id")),
        }

    async def get_user_following(
        self, user_id: str, count: int = 50, max_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get following list for a user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("get_user_following requires authentication.")
        params: Dict[str, str] = {"count": str(count)}
        if max_id:
            params["max_id"] = max_id
        body = await self._friendships_get(user_id, "following", params)
        users = [
            {
                "user_id": str(u.get("pk") or u.get("id") or ""),
                "username": u.get("username", ""),
                "full_name": u.get("full_name", ""),
                "is_verified": bool(u.get("is_verified")),
                "is_private": bool(u.get("is_private")),
            }
            for u in (body.get("users") or [])
        ]
        return {
            "users": users,
            "count": len(users),
            "next_max_id": body.get("next_max_id", ""),
            "has_more": bool(body.get("next_max_id")),
        }

    async def story_mark_seen(
        self,
        reel_media_ids: List[str],
        reel_media_owner_ids: List[str],
        reel_media_taken_at: List[int],
    ) -> Dict[str, Any]:
        """Mark stories as seen."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("story_mark_seen requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        reels_seen: Dict[str, Any] = {}
        for mid, oid, ts in zip(reel_media_ids, reel_media_owner_ids, reel_media_taken_at):
            reels_seen[f"{oid}_{mid}"] = {
                "media_id": mid, "owner_id": oid, "taken_at": ts,
                "seen_at": int(time.time()), "source": "feed",
            }
        resp = await session.post(
            "https://i.instagram.com/api/v1/media/seen/",
            data={
                "reels": _json.dumps(reels_seen),
                "live_vods_skipped": "{}",
                "nuxes_skipped": "{}",
            },
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
        )
        body = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"story_mark_seen: HTTP {resp.status_code}: {body[:200]}")
        if body.lstrip().startswith("<"):
            raise FetchError(f"story_mark_seen: got HTML (session blocked): {body[:150]}")
        return {"status": "seen", "count": len(reel_media_ids)}

    async def story_reply(self, story_owner_username: str, text: str) -> Dict[str, Any]:
        """Reply to a story by sending a DM to the story owner."""
        return await self.send_dm_to_username(story_owner_username, text)

    async def edit_profile(
        self,
        biography: Optional[str] = None,
        full_name: Optional[str] = None,
        external_url: Optional[str] = None,
        email: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit the authenticated user's profile (bio, name, URL)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("edit_profile requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""

        # Get current profile first
        my_id = (cm.cookies.get("ds_user_id", "")) or ""
        info_resp = await session.get(
            f"https://www.instagram.com/api/v1/users/{my_id}/info/",
            headers={"x-csrftoken": csrf, "x-ig-app-id": "936619743392459", "Cookie": self._cookie_str()},
        )
        current: Dict[str, Any] = {}
        if info_resp.status_code == 200:
            try:
                current = info_resp.json().get("user") or {}
            except Exception:
                pass

        data: Dict[str, str] = {
            "biography": biography if biography is not None else (current.get("biography") or ""),
            "full_name": full_name if full_name is not None else (current.get("full_name") or ""),
            "external_url": external_url if external_url is not None else (current.get("external_url") or ""),
            "email": email if email is not None else (current.get("email") or ""),
            "phone_number": phone_number if phone_number is not None else (current.get("phone_number") or ""),
            "username": current.get("username", ""),
            "first_name": (full_name or current.get("full_name") or "").split()[0] if (full_name or current.get("full_name")) else "",
        }

        _ep_headers = {
            "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
            "content-type": "application/x-www-form-urlencoded",
            "referer": "https://www.instagram.com/accounts/edit/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        # Try web-specific endpoint first, then mobile fallback
        for _ep_url in [
            "https://www.instagram.com/api/v1/web/accounts/edit/",
            "https://i.instagram.com/api/v1/accounts/edit/",
            "https://www.instagram.com/api/v1/accounts/edit/",
        ]:
            resp = await session.post(_ep_url, data=data, headers=_ep_headers, allow_redirects=False)
            if resp.status_code in (200, 201) and not resp.text.lstrip().startswith("<"):
                break
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"edit_profile: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"edit_profile: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"edit_profile: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"edit_profile: invalid JSON: {body_text[:200]}")
        user = body.get("user") or {}
        return {
            "status": "updated",
            "username": user.get("username", ""),
            "full_name": user.get("full_name", ""),
            "biography": user.get("biography", ""),
            "external_url": user.get("external_url", ""),
        }

    async def post_save(self, media_id: str) -> Dict[str, Any]:
        """Save/bookmark an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("post_save requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/save/{media_id}/save/",
            data={},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"post_save: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"post_save: HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.text
        if body.lstrip().startswith("<"):
            raise FetchError(f"post_save: got HTML (session may be blocked): {body[:150]}")
        return {"status": "saved", "media_id": media_id}

    async def post_unsave(self, media_id: str) -> Dict[str, Any]:
        """Unsave/unbookmark an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("post_unsave requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/save/{media_id}/unsave/",
            data={},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"post_unsave: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"post_unsave: HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.text
        if body.lstrip().startswith("<"):
            raise FetchError(f"post_unsave: got HTML (session may be blocked): {body[:150]}")
        return {"status": "unsaved", "media_id": media_id}

    async def block_user(self, user_id: str) -> Dict[str, Any]:
        """Block an Instagram user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("block_user requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/friendships/{user_id}/block/",
            data={"user_id": user_id},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"block_user: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"block_user: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"block_user: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"block_user: invalid JSON: {body_text[:200]}")
        fs = body.get("friendship_status") or {}
        return {
            "status": "blocked",
            "user_id": user_id,
            "blocking": bool(fs.get("blocking")),
        }

    async def unblock_user(self, user_id: str) -> Dict[str, Any]:
        """Unblock an Instagram user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("unblock_user requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/friendships/{user_id}/unblock/",
            data={"user_id": user_id},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"unblock_user: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"unblock_user: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"unblock_user: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"unblock_user: invalid JSON: {body_text[:200]}")
        fs = body.get("friendship_status") or {}
        return {
            "status": "unblocked",
            "user_id": user_id,
            "blocking": bool(fs.get("blocking")),
        }

    async def like_post(self, media_id: str, action: str = "like") -> Dict[str, Any]:
        """Like or unlike an Instagram post via /api/v1/web/likes/."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("like_post requires authentication.")
        action = action.lower().strip()
        if action not in ("like", "unlike"):
            raise FetchError(f"like_post: action must be 'like' or 'unlike', got '{action}'")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/likes/{media_id}/{action}/",
            data={},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"like_post: redirected (session rate-limited or expired)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"like_post: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"like_post: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"like_post: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"like_post: API error: {body.get('message', 'unknown')}")
        return {"status": action + "d", "media_id": media_id}

    async def follow_user(self, user_id: str, action: str = "follow") -> Dict[str, Any]:
        """Follow or unfollow an Instagram user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("follow_user requires authentication.")
        action = action.lower().strip()
        if action not in ("follow", "unfollow"):
            raise FetchError(f"follow_user: action must be 'follow' or 'unfollow', got '{action}'")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        endpoint = "create" if action == "follow" else "destroy"
        headers = {
            "x-csrftoken": csrf, "x-ig-app-id": "936619743392459",
            "content-type": "application/x-www-form-urlencoded",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        # i.instagram.com accepts the session for friendships endpoints
        resp = await session.post(
            f"https://i.instagram.com/api/v1/friendships/{endpoint}/{user_id}/",
            data={"user_id": user_id},
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"follow_user: redirected (session rate-limited or expired)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"follow_user: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"follow_user: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"follow_user: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"follow_user: API error: {body.get('message', 'unknown')}")
        fs = body.get("friendship_status") or {}
        return {
            "status": action + "ed",
            "user_id": user_id,
            "following": bool(fs.get("following")),
            "is_private": bool(fs.get("is_private")),
            "outgoing_request": bool(fs.get("outgoing_request")),
        }

    # ── Instagram Notes ───────────────────────────────────────────────────────

    async def _notes_headers(self) -> Tuple[Any, Dict[str, str]]:
        """Return (session, headers) for Notes API calls."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("Notes tools require authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        return session, headers

    async def notes_create(self, text: str, audience: int = 0) -> Dict[str, Any]:
        """
        Create an Instagram Note (visible for 24 h).

        Args:
            text: Note text (max 60 chars).
            audience: 0 = followers, 1 = close friends.

        Returns:
            dict with note_id, text, audience, expires_at.
        """
        if len(text) > 60:
            raise FetchError("Note text must be 60 characters or less.")
        session, headers = await self._notes_headers()
        resp = await session.post(
            "https://i.instagram.com/api/v1/notes/create_note/",
            data={
                "text": text,
                "audience": str(audience),
            },
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("notes_create: redirected — session rate-limited or not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"notes_create: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"notes_create: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"notes_create: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"notes_create: API error: {body.get('message', 'unknown')}")
        note = body.get("note") or {}
        return {
            "note_id": str(note.get("id", "")),
            "text": note.get("text", text),
            "audience": note.get("audience", audience),
            "expires_at": note.get("expires_at"),
        }

    async def notes_get(self) -> List[Dict[str, Any]]:
        """
        Get your active Instagram Notes.

        Returns:
            List of note dicts (note_id, text, audience, expires_at, username).
        """
        session, headers = await self._notes_headers()
        get_headers = {k: v for k, v in headers.items() if k != "content-type"}
        resp = await session.get(
            "https://i.instagram.com/api/v1/notes/get_notes/",
            headers=get_headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("notes_get: redirected — session rate-limited or not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"notes_get: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"notes_get: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"notes_get: invalid JSON: {body_text[:200]}")
        notes_raw = body.get("notes") or []
        result = []
        for n in notes_raw:
            result.append({
                "note_id": str(n.get("id", "")),
                "text": n.get("text", ""),
                "audience": n.get("audience", 0),
                "expires_at": n.get("expires_at"),
                "username": (n.get("user") or {}).get("username", ""),
            })
        return result

    async def notes_delete(self, note_id: str) -> Dict[str, Any]:
        """
        Delete an Instagram Note by ID.

        Returns:
            dict with status='deleted', note_id.
        """
        session, headers = await self._notes_headers()
        resp = await session.post(
            f"https://i.instagram.com/api/v1/notes/delete_note/",
            data={"note_id": note_id},
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("notes_delete: redirected — session rate-limited or not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"notes_delete: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"notes_delete: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"notes_delete: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"notes_delete: API error: {body.get('message', 'unknown')}")
        return {"status": "deleted", "note_id": note_id}

    # ── Broadcast Channels ────────────────────────────────────────────────────

    async def broadcast_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """
        Get info about a broadcast channel (subscribers, title, description).

        Args:
            channel_id: The broadcast channel ID (from channel URL or DM).

        Returns:
            dict with channel_id, title, description, subscriber_count, is_pinned.
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "accept": "application/json, */*",
            "Cookie": self._cookie_str(),
        }
        resp = await session.get(
            f"https://i.instagram.com/api/v1/broadcasts/{channel_id}/info/",
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("broadcast_channel_info: redirected — not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"broadcast_channel_info: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"broadcast_channel_info: got HTML: {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"broadcast_channel_info: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"broadcast_channel_info: {body.get('message', 'unknown')}")
        ch = body.get("broadcast_channel") or body
        return {
            "channel_id": channel_id,
            "title": ch.get("title", ""),
            "description": ch.get("description", ""),
            "subscriber_count": ch.get("subscriber_count", 0),
            "is_pinned": ch.get("is_pinned", False),
            "broadcast_status": ch.get("broadcast_status", ""),
        }

    async def broadcast_channel_posts(
        self, channel_id: str, max_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get posts from a broadcast channel.

        Returns:
            dict with posts (list), next_max_id (for pagination), has_more.
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "accept": "application/json, */*",
            "Cookie": self._cookie_str(),
        }
        params: Dict[str, str] = {}
        if max_id:
            params["max_id"] = max_id
        resp = await session.get(
            f"https://i.instagram.com/api/v1/broadcasts/{channel_id}/posts/",
            params=params,
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("broadcast_channel_posts: redirected — not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"broadcast_channel_posts: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"broadcast_channel_posts: got HTML: {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"broadcast_channel_posts: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"broadcast_channel_posts: {body.get('message', 'unknown')}")
        items = body.get("broadcast_posts") or body.get("items") or []
        posts = []
        for item in items:
            posts.append({
                "post_id": str(item.get("pk", item.get("id", ""))),
                "text": item.get("text", ""),
                "created_at": item.get("created_at") or item.get("taken_at"),
                "like_count": item.get("like_count", 0),
            })
        return {
            "posts": posts,
            "next_max_id": body.get("next_max_id"),
            "has_more": bool(body.get("more_available") or body.get("next_max_id")),
        }
