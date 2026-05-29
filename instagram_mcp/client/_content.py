"""Content fetch mixin for InstagramClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import json as _json

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


def _caption_insights(length: float, hashtags: float, emoji_rate: float, cta_rate: float) -> List[str]:
    tips = []
    if length < 50:
        tips.append("Captions are very short \u2014 try 100-150 chars for more context and discoverability")
    elif length > 500:
        tips.append("Captions are long \u2014 consider breaking them up with line breaks for readability")
    if hashtags < 5:
        tips.append("Low hashtag count \u2014 using 10-15 targeted hashtags improves reach")
    elif hashtags > 25:
        tips.append("High hashtag count \u2014 reduce to 10-20 relevant hashtags for better quality signal")
    if emoji_rate < 0.3:
        tips.append("Low emoji usage \u2014 emojis in captions increase engagement rate by ~15%")
    if cta_rate < 0.2:
        tips.append("Low CTA usage \u2014 adding a call-to-action (e.g. 'comment below') doubles comments")
    if not tips:
        tips.append("Caption strategy looks solid \u2014 good length, hashtags, and engagement signals")
    return tips


class ContentMixin:
    """Content retrieval methods (posts, stories, hashtags, etc.)."""

    # ── Content ──────────────────────────────────────────────────────────────

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
                allow_redirects=False,
            )

            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                raise FetchError(
                    f"Tagged Tab: redirected (HTTP {status}) — session may be expired or rate-limited"
                )
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
                allow_redirects=False,
            )

            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                raise FetchError(
                    f"Reposts Tab: redirected (HTTP {status}) — session may be expired or rate-limited"
                )
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
                allow_redirects=False,
            )

            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                raise FetchError(
                    f"Reels Tab: redirected (HTTP {status}) — session may be expired or rate-limited"
                )
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
        from ..parser import shortcode_to_media_id

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
                        allow_redirects=False,
                    )
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"media_info({shortcode}) request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"media_info({shortcode}): redirected (HTTP {resp.status_code}) — session may be expired")
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
                "X-IG-App-ID": self._config.ig_app_id_mobile,
            },
        )
        status = resp.status_code
        if status == 404:
            from ..exceptions import UserNotFoundError
            raise UserNotFoundError(message=f"Post {media_id} not found (404).")
        if status == 403:
            from ..exceptions import PrivateAccountError
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

        # Normalize edges: unwrap 'node' and map fields to a flat standard structure
        # so downstream tools (hashtag_suggest, niche_top, format_hashtag_markdown)
        # can access post.get('username'), post.get('shortcode'), etc. directly.
        def _normalize_edge(edge: Dict) -> Dict:
            node = edge.get("node") or edge  # fallback: edge itself if already flat
            owner = node.get("owner") or {}
            caption_obj = node.get("edge_media_to_caption") or {}
            caption_edges = caption_obj.get("edges") or []
            caption_text = ""
            if caption_edges:
                cap_node = caption_edges[0].get("node") or {}
                caption_text = cap_node.get("text") or ""
            return {
                "shortcode":      node.get("shortcode") or "",
                "username":       owner.get("username") or "",
                "user_id":        str(owner.get("id") or ""),
                "is_verified":    bool(owner.get("is_verified")),
                "like_count":     int(node.get("edge_liked_by", {}).get("count") or 0),
                "comment_count":  int(node.get("edge_media_to_comment", {}).get("count") or 0),
                "play_count":     int(node.get("video_view_count") or 0),
                "taken_at":       int(node.get("taken_at_timestamp") or 0),
                "caption":        caption_text,
                "is_video":       bool(node.get("is_video")),
                "thumbnail_url":  node.get("thumbnail_src") or node.get("display_url") or "",
                "post_url":       f"https://www.instagram.com/p/{node.get('shortcode', '')}/",
                # keep original node for reference
                "_node":          node,
            }

        posts = [_normalize_edge(e) for e in edges]

        return {
            "ok": True,
            "found": True,
            "status_code": 200,
            "tag": tag,
            "posts": posts,
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
                mashup_count = (clips_meta.get("mashup_info") or {}).get("formatted_mashups_count") or ""

                # Carousel
                carousel_count = media.get("carousel_media_count") or 0
                carousel_items = []
                if mtype == 8:
                    for ci in (media.get("carousel_media") or []):
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
        from ..parser import parse_post_html

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
                    resp = await session.get(url, headers=headers, timeout=15, allow_redirects=False)
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"stories request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"stories: redirected (HTTP {resp.status_code}) — session may be expired")
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
                        allow_redirects=False,
                    )
                except Exception as exc:
                    raise FetchError(f"location_search request failed: {exc}") from exc

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"location_search: redirected (HTTP {resp.status_code}) — session may be expired")
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
                        allow_redirects=False,
                    )
                except Exception as exc:
                    raise FetchError(f"location sections request failed: {exc}") from exc

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"location sections: redirected (HTTP {resp.status_code}) — session may be expired")
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
                        allow_redirects=False,
                    )
                except Exception as exc:
                    raise FetchError(f"audio_reels request failed: {exc}") from exc

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"audio_reels: redirected (HTTP {resp.status_code}) — session may be expired")
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
                    resp = await session.get(tray_url, headers=headers, timeout=15, allow_redirects=False)
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"highlights request failed: {exc}") from exc
                    import asyncio as _asyncio
                    await _asyncio.sleep(1)
                    continue

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"highlights: redirected (HTTP {resp.status_code}) — session may be expired")
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
                            media_resp = await session.get(
                                media_url, headers=headers, timeout=20, allow_redirects=False
                            )
                            if media_resp.status_code in (301, 302, 303, 307, 308):
                                logger.warning("highlights media chunk redirected (HTTP %d)", media_resp.status_code)
                            elif media_resp.status_code == 200:
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


    async def home_feed(self, limit: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
        """Get home timeline — posts from accounts you follow."""
        cm, session, csrf = await self._require_auth("home_feed")
        mobile_ua = (
            "Instagram 317.0.0.24.109 Android "
            "(31/12; 420dpi; 1080x2170; Google; Pixel 5; redfin; qcom; en_US; 558903590)"
        )
        params: Dict[str, str] = {"count": str(min(limit, 50))}
        if cursor:
            params["max_id"] = cursor
        # Try www first; fallback to i.instagram.com mobile if redirected
        body: Dict[str, Any] = {}
        for url, hdrs in [
            (
                "https://www.instagram.com/api/v1/feed/timeline/",
                self._auth_headers(csrf, content_type="application/json"),
            ),
            (
                "https://i.instagram.com/api/v1/feed/timeline/",
                {
                    "x-csrftoken": csrf,
                    "x-ig-app-id": self._config.ig_app_id_mobile,
                    "User-Agent": mobile_ua,
                    "accept": "application/json, */*",
                    "Cookie": self._cookie_str(),
                },
            ),
        ]:
            resp = await session.get(url, params=params, headers=hdrs, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308) or resp.text.lstrip().startswith("<"):
                continue
            if resp.status_code not in (200, 201):
                raise FetchError(f"home_feed: HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                body = _json.loads(resp.text)
                break
            except Exception:
                raise FetchError(f"home_feed: invalid JSON: {resp.text[:200]}")
        if not body:
            raise FetchError("home_feed: all endpoints redirected (session rate-limited or expired)")
        items_raw = body.get("feed_items") or body.get("items") or []
        posts = []
        for item in items_raw[:limit]:
            media = item.get("media_or_ad") or item
            pk = str(media.get("pk") or media.get("id") or "")
            code = media.get("code") or media.get("shortcode") or ""
            cap = media.get("caption") or {}
            posts.append({
                "media_id": pk,
                "shortcode": code,
                "media_type": media.get("media_type", 1),
                "username": (media.get("user") or {}).get("username", ""),
                "caption": cap.get("text", "") if isinstance(cap, dict) else str(cap or ""),
                "like_count": media.get("like_count", 0),
                "comment_count": media.get("comment_count", 0),
                "taken_at": media.get("taken_at", 0),
            })
        return {
            "posts": posts,
            "count": len(posts),
            "next_max_id": body.get("next_max_id", ""),
            "more_available": body.get("more_available", False),
        }


    async def saved_posts(self, limit: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
        """Get your saved/bookmarked Instagram posts."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("saved_posts requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        params: Dict[str, str] = {"count": str(min(limit, 50))}
        if cursor:
            params["max_id"] = cursor
        resp = await session.get(
            "https://www.instagram.com/api/v1/feed/saved/",
            params=params,
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
                     "User-Agent": "Instagram 317.0.0.24.109 Android (31/12; 420dpi; 1080x2170; Google; Pixel 5; redfin; qcom; en_US; 558903590)"},
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("saved_posts: redirected (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"saved_posts: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"saved_posts: invalid JSON: {resp.text[:200]}")
        items_raw = body.get("items") or []
        posts = []
        for item in items_raw[:limit]:
            media = item.get("media") or item
            pk = str(media.get("pk") or media.get("id") or "")
            code = media.get("code") or media.get("shortcode") or ""
            cap = media.get("caption") or {}
            posts.append({
                "media_id": pk,
                "shortcode": code,
                "media_type": media.get("media_type", 1),
                "username": (media.get("user") or {}).get("username", ""),
                "caption": cap.get("text", "") if isinstance(cap, dict) else str(cap or ""),
                "like_count": media.get("like_count", 0),
                "taken_at": media.get("taken_at", 0),
            })
        return {
            "posts": posts,
            "count": len(posts),
            "next_max_id": body.get("next_max_id", ""),
            "more_available": body.get("more_available", False),
        }


    async def liked_posts(self, limit: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
        """Get posts you have liked."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("liked_posts requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        params: Dict[str, str] = {"count": str(min(limit, 50))}
        if cursor:
            params["max_id"] = cursor
        resp = await session.get(
            "https://www.instagram.com/api/v1/feed/liked/",
            params=params,
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
                     "User-Agent": "Instagram 317.0.0.24.109 Android (31/12; 420dpi; 1080x2170; Google; Pixel 5; redfin; qcom; en_US; 558903590)"},
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("liked_posts: redirected (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"liked_posts: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"liked_posts: invalid JSON: {resp.text[:200]}")
        items_raw = body.get("items") or []
        posts = []
        for item in items_raw[:limit]:
            pk = str(item.get("pk") or item.get("id") or "")
            code = item.get("code") or item.get("shortcode") or ""
            cap = item.get("caption") or {}
            posts.append({
                "media_id": pk,
                "shortcode": code,
                "media_type": item.get("media_type", 1),
                "username": (item.get("user") or {}).get("username", ""),
                "caption": cap.get("text", "") if isinstance(cap, dict) else str(cap or ""),
                "like_count": item.get("like_count", 0),
                "taken_at": item.get("taken_at", 0),
            })
        return {
            "posts": posts,
            "count": len(posts),
            "next_max_id": body.get("next_max_id", ""),
            "more_available": body.get("more_available", False),
        }


    async def activity_feed(self, limit: int = 30) -> Dict[str, Any]:
        """Get your Instagram notification/activity feed."""
        cm, session, csrf = await self._require_auth("activity_feed")
        # www.instagram.com/news/inbox returns 500; i.instagram.com with mobile UA works
        mobile_ua = (
            "Instagram 317.0.0.24.109 Android "
            "(31/12; 420dpi; 1080x2170; Google; Pixel 5; redfin; qcom; en_US; 558903590)"
        )
        hdrs = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id_mobile,
            "User-Agent": mobile_ua,
            "accept": "application/json, */*",
            "Cookie": self._cookie_str(),
        }
        resp = await session.get(
            "https://i.instagram.com/api/v1/news/inbox/",
            headers=hdrs,
            allow_redirects=False,
        )
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"activity_feed: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("activity_feed: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"activity_feed: invalid JSON: {body_text[:200]}")
        _story_type_map = {
            1: "like", 2: "comment", 3: "follow", 4: "mention",
            5: "tagged", 10: "comment_like", 12: "new_follower",
        }
        notifications = []
        for key in ("new_stories", "old_stories"):
            for story in (body.get(key) or []):
                stype = story.get("story_type", 0)
                user = (story.get("args") or {}).get("profile_id") or ""
                text = (story.get("args") or {}).get("text") or ""
                ts = story.get("timestamp", 0)
                notifications.append({
                    "type": _story_type_map.get(stype, f"type_{stype}"),
                    "user_id": str(user),
                    "text": text[:200],
                    "timestamp": ts,
                })
        notifications = notifications[:limit]
        return {"notifications": notifications, "count": len(notifications)}


    async def hashtag_suggest(
        self,
        seed_hashtag: str,
        target_count: int = 30,
    ) -> Dict[str, Any]:
        """
        Suggest related hashtags for a niche by analyzing top posts under the seed hashtag.

        Fetches top posts, extracts all hashtags they use, ranks by frequency,
        and groups them into tiers by follower count for reach diversification.

        Returns:
            dict with seed, suggested hashtags grouped by tier (mega/macro/mid/micro),
            and a ready-to-use copy-paste set.
        """
        import re as _re

        seed = seed_hashtag.lstrip("#").strip().lower()
        if not seed:
            raise FetchError("hashtag_suggest: seed_hashtag is required")

        # Fetch top posts for the seed hashtag
        result = await self.fetch_hashtag(seed, max_posts=24)
        posts = result.get("posts", []) if result else []
        if not posts:
            raise FetchError(f"hashtag_suggest: no posts found for #{seed}")

        # Extract all hashtags from captions
        hashtag_freq: Dict[str, int] = {}
        for post in posts:
            caption = post.get("caption") or ""
            tags = _re.findall(r"#([A-Za-z0-9_]+)", caption)
            for tag in tags:
                t = tag.lower()
                if t != seed:
                    hashtag_freq[t] = hashtag_freq.get(t, 0) + 1

        # Sort by frequency
        ranked = sorted(hashtag_freq.items(), key=lambda x: -x[1])

        # Take top tags up to target_count
        top_tags = [tag for tag, _ in ranked[:target_count]]

        # Fetch follower counts for the top 15 tags in parallel to tier them
        async def _get_count(tag: str) -> int:
            try:
                info = await self._fetch_hashtag_info(tag)
                return info.get("media_count", 0)
            except Exception:
                return 0

        semaphore = asyncio.Semaphore(5)

        async def _bounded(tag: str) -> tuple:
            async with semaphore:
                return tag, await _get_count(tag)

        counts_pairs = await asyncio.gather(*[_bounded(t) for t in top_tags[:15]])
        count_map = dict(counts_pairs)

        # Tier classification (by media count, not follower count)
        mega, macro, mid, micro, remaining = [], [], [], [], []
        for tag in top_tags:
            n = count_map.get(tag, 0)
            if n >= 10_000_000:
                mega.append(tag)
            elif n >= 1_000_000:
                macro.append(tag)
            elif n >= 100_000:
                mid.append(tag)
            elif n > 0:
                micro.append(tag)
            else:
                remaining.append(tag)  # no count fetched (beyond top-15)

        # Build a balanced suggested set: 2 mega + 5 macro + 10 mid + 10 micro
        balanced = (mega[:2] + macro[:5] + mid[:10] + micro[:10] + remaining)[:target_count]
        copy_paste = " ".join(f"#{t}" for t in balanced)

        return {
            "seed": seed,
            "posts_analyzed": len(posts),
            "unique_hashtags_found": len(hashtag_freq),
            "tiers": {
                "mega_10M_plus": mega[:5],
                "macro_1M_10M": macro[:10],
                "mid_100k_1M": mid[:15],
                "micro_under_100k": micro[:15],
            },
            "balanced_set": balanced,
            "copy_paste": copy_paste,
        }


    async def _fetch_hashtag_info(self, tag: str) -> Dict[str, Any]:
        """Get media_count for a hashtag via the web API."""
        session = await self._get_session(None)
        url = f"https://www.instagram.com/explore/tags/{tag}/?__a=1&__d=dis"
        headers = {
            "x-ig-app-id": self._config.ig_app_id_mobile,
            "x-requested-with": "XMLHttpRequest",
            "referer": f"https://www.instagram.com/explore/tags/{tag}/",
        }
        try:
            resp = await session.get(url, headers=headers, allow_redirects=False)
            if resp.status_code == 200:
                data = _json.loads(resp.text)
                count = (
                    data.get("graphql", {}).get("hashtag", {}).get("edge_hashtag_to_media", {}).get("count")
                    or data.get("data", {}).get("hashtag", {}).get("media_count")
                    or 0
                )
                return {"media_count": int(count)}
        except Exception:
            pass
        return {"media_count": 0}

    # ── Caption Analysis ──────────────────────────────────────────────────────


    async def caption_analyze(
        self,
        username: str,
        max_posts: int = 20,
    ) -> Dict[str, Any]:
        """
        Analyze caption patterns from an account's top posts.

        Extracts: average caption length, emoji usage rate, hashtag count distribution,
        CTA presence (link in bio / follow / comment / swipe), posting cadence patterns.

        Returns:
            dict with pattern summary and top-performing post examples.
        """
        import re as _re
        import statistics

        user = await self.fetch_user(username)
        if not user:
            raise FetchError(f"caption_analyze: user @{username} not found")

        if user.get("is_private"):
            raise FetchError(
                f"caption_analyze: @{username} is a private account — captions are not accessible"
            )

        timeline = user.get("edge_owner_to_timeline_media") or {}
        initial_edges = timeline.get("edges") or []
        page_info = timeline.get("page_info") or {}
        has_next = page_info.get("has_next_page", False)
        end_cursor = page_info.get("end_cursor") or ""

        # Use posts already embedded in fetch_user response as the first batch
        posts = [e.get("node", e) for e in initial_edges]

        # Fetch more pages if max_posts > initial count and next page exists
        if len(posts) < max_posts and has_next and end_cursor:
            user_id = user.get("id") or user.get("pk") or ""
            remaining = max_posts - len(posts)
            feed_data = await self.fetch_user_feed(
                user_id, username, end_cursor, max_posts=remaining
            )
            extra_edges = feed_data.get("edges") or []
            posts.extend(e.get("node", e) for e in extra_edges)

        posts = posts[:max_posts]
        if not posts:
            raise FetchError(f"caption_analyze: no posts found for @{username}")

        def _extract_caption(node: Dict) -> str:
            # GraphQL nodes: edge_media_to_caption.edges[0].node.text
            cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
            if cap_edges:
                return (cap_edges[0].get("node") or {}).get("text") or ""
            # Direct caption field (some API responses)
            cap = node.get("caption")
            if isinstance(cap, dict):
                return cap.get("text") or ""
            return cap or ""

        captions = [_extract_caption(p) for p in posts]
        likes = [
            p.get("like_count")
            or (p.get("edge_liked_by") or {}).get("count")
            or (p.get("edge_media_preview_like") or {}).get("count")
            or 0
            for p in posts
        ]

        emoji_re = _re.compile(
            "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
            "\U00002600-\U000027BF\U0001FA00-\U0001FA9F]+",
            flags=_re.UNICODE,
        )
        cta_patterns = _re.compile(
            r"\b(link in bio|follow|comment below|tag a friend|swipe|dm me|click|shop now|save this)\b",
            _re.IGNORECASE,
        )

        lengths, hashtag_counts, has_emoji, has_cta = [], [], [], []
        for cap in captions:
            lengths.append(len(cap))
            hashtag_counts.append(len(_re.findall(r"#\w+", cap)))
            has_emoji.append(1 if emoji_re.search(cap) else 0)
            has_cta.append(1 if cta_patterns.search(cap) else 0)

        avg_length = statistics.mean(lengths) if lengths else 0
        avg_hashtags = statistics.mean(hashtag_counts) if hashtag_counts else 0
        emoji_rate = sum(has_emoji) / len(has_emoji) if has_emoji else 0
        cta_rate = sum(has_cta) / len(has_cta) if has_cta else 0

        # Top 3 posts by likes
        top_posts = sorted(
            [{"caption": c[:200], "like_count": like} for c, like in zip(captions, likes)],
            key=lambda x: -x["like_count"],
        )[:3]

        # Most common hashtags across all posts
        all_tags: Dict[str, int] = {}
        for cap in captions:
            for tag in _re.findall(r"#(\w+)", cap):
                all_tags[tag.lower()] = all_tags.get(tag.lower(), 0) + 1
        top_hashtags = sorted(all_tags.items(), key=lambda x: -x[1])[:10]

        return {
            "username": username,
            "posts_analyzed": len(posts),
            "avg_caption_length": round(avg_length),
            "avg_hashtag_count": round(avg_hashtags, 1),
            "emoji_usage_rate": round(emoji_rate * 100),
            "cta_usage_rate": round(cta_rate * 100),
            "top_hashtags": [{"tag": t, "count": c} for t, c in top_hashtags],
            "top_posts_by_likes": top_posts,
            "insights": _caption_insights(avg_length, avg_hashtags, emoji_rate, cta_rate),
        }


