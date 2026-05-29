"""Threads mixin for InstagramClient."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import json as _json

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


class ThreadsMixin:
    """Threads platform methods."""

    # ── Threads ──────────────────────────────────────────────────────────────

    def _threads_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Return base headers for Threads API calls (public endpoints, no auth)."""
        h = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Mode": "cors",
            "x-ig-app-id": self._THREADS_APP_ID,
        }
        if extra:
            h.update(extra)
        return h


    async def threads_profile(self, username: str) -> Dict[str, Any]:
        """
        Get a Threads profile by username (public, no auth required).

        Scrapes the Threads profile page HTML which contains embedded JSON.
        More reliable than the GraphQL API (whose doc_ids rotate frequently).

        Returns:
            dict with username, display_name, bio, followers, is_verified, threads_count.
        """
        import re as _re

        username = username.lstrip("@").strip().lower()
        if not username:
            raise FetchError("threads_profile: username is required")

        session = await self._get_session(None)
        resp = await session.get(
            f"https://www.threads.net/@{username}",
            headers=self._threads_headers({
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Referer": "https://www.threads.net/",
            }),
            allow_redirects=True,
        )
        if resp.status_code == 404:
            raise FetchError(f"threads_profile: user '{username}' not found")
        if resp.status_code != 200:
            raise FetchError(f"threads_profile: HTTP {resp.status_code}")

        html = resp.text
        if not html or len(html) < 500:
            raise FetchError("threads_profile: empty or too-short response")

        # Extract embedded JSON fields from the Threads page
        def _extract(pattern: str, default: Any = "") -> Any:
            m = _re.search(pattern, html)
            return m.group(1) if m else default

        # pk may have 1-2 fields between it and "username" in the Threads HTML
        pk = _extract(r'"pk":"(\d+)"[^}]{0,120}"username":"' + _re.escape(username) + '"', "")
        if not pk:
            # Fallback: find "username":"natgeo" and look backwards for nearest pk
            m_pk = _re.search(r'"pk":"(\d+)"[^{]*?"username":"' + _re.escape(username) + '"', html)
            pk = m_pk.group(1) if m_pk else ""

        followers = int(_extract(r'"username":"' + _re.escape(username) + r'"[^}]{0,300}"follower_count":(\d+)', 0))
        if not followers:
            followers = int(_extract(r'"follower_count":(\d+)', 0))

        is_private = _extract(r'"text_post_app_is_private":(true|false)', "false") == "true"
        is_verified = _extract(r'"is_verified":(true|false)', "false") == "true"

        if not followers and not pk:
            raise FetchError(f"threads_profile: could not extract profile data for '{username}'")

        return {
            "username": username,
            "display_name": "",
            "bio": "",
            "followers": followers,
            "following": 0,
            "threads_count": 0,
            "is_verified": is_verified,
            "is_private": is_private,
            "profile_pic_url": "",
            "pk": pk,
        }


    async def threads_user_posts(
        self, username: str, max_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get recent Threads posts for a user (public, no auth required).

        Returns:
            dict with posts (list), next_max_id, has_more.
        """
        profile = await self.threads_profile(username)
        user_id = profile.get("pk", "")
        # We already have the HTML from threads_profile — fetch it again for posts
        import re as _re

        session = await self._get_session(None)
        resp = await session.get(
            f"https://www.threads.net/@{username}",
            headers=self._threads_headers({
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Referer": "https://www.threads.net/",
            }),
            allow_redirects=True,
        )
        if resp.status_code == 404:
            raise FetchError(f"threads_user_posts: user '{username}' not found")
        if resp.status_code != 200:
            raise FetchError(f"threads_user_posts: HTTP {resp.status_code}")

        html = resp.text
        # Extract post shortcodes (Threads uses Instagram shortcode format)
        codes = _re.findall(r'"code":"([A-Za-z0-9_-]{10,12})"', html)
        like_counts = [int(x) for x in _re.findall(r'"like_count":(\d+)', html)]
        texts = _re.findall(r'"text":"([^"]{1,500})"', html)
        taken_ats = [int(x) for x in _re.findall(r'"taken_at":(\d+)', html)]

        posts = []
        seen_codes: set = set()
        for i, code in enumerate(codes):
            if code in seen_codes:
                continue
            seen_codes.add(code)
            posts.append({
                "post_id": "",
                "shortcode": code,
                "text": texts[i] if i < len(texts) else "",
                "like_count": like_counts[i] if i < len(like_counts) else 0,
                "reply_count": 0,
                "taken_at": taken_ats[i] if i < len(taken_ats) else None,
                "url": f"https://www.threads.net/@{username}/post/{code}",
            })
            if len(posts) >= 20:
                break

        return {
            "username": username,
            "posts": posts,
            "next_max_id": None,
            "has_more": False,
        }

    # ── Hashtag Suggestions ───────────────────────────────────────────────────

