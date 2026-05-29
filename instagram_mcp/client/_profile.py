"""Profile fetch mixin for InstagramClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


class ProfileMixin:
    """Profile-related methods."""

    # ── Profile fetch (web_profile_info) ─────────────────────────────────────

    async def _fetch_profile_attempt(
        self, username: str, proxy_url: Optional[str]
    ) -> Dict[str, Any]:
        """Single attempt: fetch web_profile_info."""
        url = self._config.ig_endpoint.format(username)
        session = await self._get_session(proxy_url)
        resp = await session.get(url, allow_redirects=False)
        status = resp.status_code

        if status in (301, 302):
            raise FetchError(f"fetch_user(@{username}) redirected to login (session expired)")

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

