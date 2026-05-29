"""Social/discovery mixin for InstagramClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

import json as _json

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


class SocialMixin:
    """Social graph and discovery methods."""

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
            "x-ig-app-id": self._config.ig_app_id_mobile,
            "x-requested-with": "XMLHttpRequest",
            "Cookie": self._cookie_str(),
        }
        # Try i.instagram.com first (mobile API is more permissive), then www
        hosts = ["https://i.instagram.com", "https://www.instagram.com"]
        last_error = "unknown"

        for host in hosts:
            url = f"{host}/api/v1/friendships/{user_pk}/{endpoint}/"
            try:
                resp = await session.get(
                    url, params=params, headers=headers, timeout=20, allow_redirects=False
                )
            except Exception as exc:
                last_error = str(exc)
                continue

            if resp.status_code in (301, 302, 303, 307, 308):
                last_error = f"Redirected (HTTP {resp.status_code}) — session may be expired or rate-limited"
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
                    resp = await session.get(url, headers=headers, timeout=15, allow_redirects=False)
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"likers request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"likers: redirected (HTTP {resp.status_code}) — session may be expired")
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
                        allow_redirects=False,
                    )
                except Exception as exc:
                    if attempt == 2:
                        raise FetchError(f"search request failed: {exc}") from exc
                    await asyncio.sleep(1)
                    continue

                if resp.status_code in (301, 302, 303, 307, 308):
                    raise FetchError(f"search: redirected (HTTP {resp.status_code}) — session may be expired")
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


    async def search_users(self, query: str, count: int = 10) -> List[Dict[str, Any]]:
        """Search Instagram users by query string."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("search_users requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""

        _ck = self._cookie_str()
        _sh = {"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile, "Cookie": _ck}
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


    async def compare_followers(self, analysis_type: str = "both", max_users: int = 500) -> Dict[str, Any]:
        """Compare followers vs following to find unfollowers and fans."""
        cm, session, csrf = await self._require_auth("compare_followers")
        my_user_id = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        if not my_user_id:
            raise FetchError("compare_followers: cannot determine logged-in user_id from cookies")

        async def _fetch_all(endpoint: str) -> Set[str]:
            users: Set[str] = set()
            max_id = ""
            while len(users) < max_users:
                params: Dict[str, str] = {"count": "100"}
                if max_id:
                    params["max_id"] = max_id
                body = await self._auth_get(
                    f"https://www.instagram.com/api/v1/friendships/{my_user_id}/{endpoint}/",
                    params, csrf, session, "compare_followers",
                )
                page = body.get("users") or []
                for u in page:
                    users.add(str(u.get("pk") or u.get("id") or ""))
                max_id = body.get("next_max_id", "")
                if not max_id or not page:
                    break
            return users

        # Both sets are required for any analysis type:
        # unfollowers = following − followers  (need both)
        # fans        = followers − following  (need both)
        follower_ids = await _fetch_all("followers")
        following_ids = await _fetch_all("following")

        result: Dict[str, Any] = {}
        if analysis_type in ("both", "unfollowers"):
            unfollowers = following_ids - follower_ids
            result["unfollowers"] = sorted(unfollowers)
            result["unfollower_count"] = len(unfollowers)
        if analysis_type in ("both", "fans"):
            fans = follower_ids - following_ids
            result["fans"] = sorted(fans)
            result["fan_count"] = len(fans)
        return result


    async def user_id_lookup(self, value: str, lookup_type: str = "auto") -> Dict[str, Any]:
        """Bidirectional lookup: username → user_id or user_id → username."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("user_id_lookup requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        value = value.strip().lstrip("@")
        mobile_ua = "Instagram 317.0.0.24.109 Android (31/12; 420dpi; 1080x2170; Google; Pixel 5; redfin; qcom; en_US; 558903590)"

        if lookup_type == "auto":
            lookup_type = "id_to_username" if value.isdigit() else "username_to_id"

        if lookup_type == "username_to_id":
            resp = await session.get(
                f"https://www.instagram.com/api/v1/users/{value}/usernameinfo/",
                headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id, "User-Agent": mobile_ua},
                allow_redirects=False,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                raise FetchError("user_id_lookup: redirected (session rate-limited)")
            if resp.status_code not in (200, 201):
                raise FetchError(f"user_id_lookup: HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                body = resp.json()
            except Exception:
                raise FetchError(f"user_id_lookup: invalid JSON: {resp.text[:200]}")
            user = body.get("user") or {}
            return {
                "input": value,
                "username": user.get("username", value),
                "user_id": str(user.get("pk") or user.get("id") or ""),
                "full_name": user.get("full_name", ""),
                "is_private": user.get("is_private", False),
                "is_verified": user.get("is_verified", False),
            }
        else:
            resp = await session.get(
                f"https://www.instagram.com/api/v1/users/{value}/info/",
                headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id, "User-Agent": mobile_ua},
                allow_redirects=False,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                raise FetchError("user_id_lookup: redirected (session rate-limited)")
            if resp.status_code not in (200, 201):
                raise FetchError(f"user_id_lookup: HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                body = resp.json()
            except Exception:
                raise FetchError(f"user_id_lookup: invalid JSON: {resp.text[:200]}")
            user = body.get("user") or {}
            return {
                "input": value,
                "username": user.get("username", ""),
                "user_id": str(user.get("pk") or user.get("id") or value),
                "full_name": user.get("full_name", ""),
                "is_private": user.get("is_private", False),
                "is_verified": user.get("is_verified", False),
            }

