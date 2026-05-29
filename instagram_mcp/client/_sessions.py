"""Session management mixin for InstagramClient."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Dict, Optional

from ..exceptions import AuthError, FetchError
from ..delay import JitterAsyncSession as _JitterAsyncSession
from ._utils import _mask_proxy

try:
    from curl_cffi.requests import AsyncSession  # type: ignore
except ImportError:  # pragma: no cover
    AsyncSession = None  # type: ignore

logger = logging.getLogger("instagram_mcp.client")


def _get_jitter_session_cls():
    """Look up JitterAsyncSession from the package module to support test patching."""
    client_pkg = sys.modules.get("instagram_mcp.client")
    if client_pkg and hasattr(client_pkg, "JitterAsyncSession"):
        return client_pkg.JitterAsyncSession
    return _JitterAsyncSession


class SessionMixin:
    """Session pool management."""

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
            session = _get_jitter_session_cls()(
                headers=self._config.ig_headers,
                impersonate=self._config.ig_impersonate,
                proxies=proxies,
                timeout=self._config.request_timeout,
                max_clients=self._config.async_max_clients,
                delay_simulator=self._delay_simulator,
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
                # Pool keys are proxy URLs (or the literal "direct"); mask any
                # embedded credentials before logging — Requirement 23.1.
                logger.debug(
                    "Session pool evicted: %s (pool full)",
                    _mask_proxy(oldest_key) if oldest_key != "direct" else oldest_key,
                )
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
        """Return an authenticated AsyncSession. If multi-account pool is configured
        and has healthy members, it rotates through the pool. Otherwise, falls back
        to the single configured _cookie_manager.

        Raises FetchError/AuthError if no valid cookies are loaded.
        """
        if self._closed:
            raise FetchError("Client is closed")

        pool_res = await self._account_pool.get_next_account()
        if pool_res is not None:
            alias, cm = pool_res
        else:
            alias = "default"
            cm = self._cookie_manager

        if not (cm and cm.is_authenticated):
            raise AuthError("No authenticated session available.")

        async with self._auth_session_lock:
            if alias not in self._auth_sessions:
                session = _get_jitter_session_cls()(
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
                    delay_simulator=self._delay_simulator,
                    account_pool=self._account_pool,
                    cookies_path=cm.cookies_path if cm else "",
                )
                if cm.cookies:
                    for name, value in cm.cookies.items():
                        session.cookies.set(
                            name, self._decode_cookie_value(value), domain=".instagram.com"
                        )
                session.account_alias = alias
                self._auth_sessions[alias] = session

            self._cookie_manager = cm
            return self._auth_sessions[alias]

