"""Feed fetch mixin for InstagramClient."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import json as _json

from ..exceptions import FetchError
from ..models import DateRange

logger = logging.getLogger("instagram_mcp.client")


class FeedMixin:
    """Feed-related methods (v1 and GraphQL)."""

    # ── v1/feed/user fetch (max_id pagination) ──────────────────────────────

    async def _fetch_feed_page_v1_attempt(
        self, url: str, proxy_url: Optional[str]
    ) -> Dict[str, Any]:
        """Single attempt: fetch one v1/feed/user page."""
        session = await self._get_session(proxy_url)
        resp = await session.get(url, allow_redirects=False)
        status = resp.status_code

        if status == 200:
            try:
                d = resp.json()
                items = d.get("items", [])
                if not items and not d.get("more_available"):
                    # HTTP 200 but empty items — Instagram is rate-limiting or
                    # the session was detected; treat as a soft failure so caller
                    # can break and the empty result is NOT cached.
                    logger.warning("feed_v1 HTTP 200 but items=[] for url=%s — likely rate-limited", url)
                return {
                    "ok": True,
                    "items": items,
                    "more_available": d.get("more_available", False),
                    "next_max_id": d.get("next_max_id", ""),
                    "status_code": 200,
                    "rate_limited": not items,
                }
            except (ValueError, TypeError):
                return {"ok": False, "items": [], "more_available": False, "next_max_id": "", "status_code": 200, "rate_limited": True}

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
                # Don't cache rate-limited (HTTP 200 but empty) responses
                if not page_result.get("rate_limited"):
                    await self._cache.set(cache_key, page_result, ttl)
                else:
                    logger.warning("feed_v1 skipping cache for empty rate-limited response uid=%s", user_id)

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
            allow_redirects=False,
        )

        status = resp.status_code
        if status in (301, 302):
            raise FetchError(f"fetch_graphql_attempt(@{username}) redirected to login (session expired)")
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

