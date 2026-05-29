"""
Central Instagram API client - assembled from mixin modules.

Architecture:
  - curl_cffi.requests.AsyncSession (no ThreadPoolExecutor needed)
  - Per-proxy session pool, reused across calls (TLS handshake amortised)
  - Single-flight cache: dedupes concurrent fetches for the same key
  - Centralised retry helper: 3 retries, different proxy each time, no waiting
  - Adaptive rate limiter as optional safety net (high RPS by default w/ proxies)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from ..cache import SmartCache
from ..config import MCPConfig
from ..cookie_manager import CookieManager
from ..exceptions import FetchError
from ..models import DateRange
from ..proxy_manager import ProxyManager
from ..rate_limiter import AdaptiveRateLimiter
from ..account_pool import AccountPool

# curl_cffi async -- preferred, no thread pool needed
try:
    from curl_cffi.requests import AsyncSession  # type: ignore
    CURL_CFFI_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncSession = None  # type: ignore
    CURL_CFFI_AVAILABLE = False

from ..delay import DelaySimulator, JitterAsyncSession

from ._retry import RetryMixin
from ._sessions import SessionMixin
from ._profile import ProfileMixin
from ._feed import FeedMixin
from ._social import SocialMixin
from ._interactions import InteractionsMixin
from ._content import ContentMixin
from ._upload import UploadMixin
from ._dm import DmMixin
from ._threads import ThreadsMixin

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


class InstagramClient(
    RetryMixin,
    SessionMixin,
    ProfileMixin,
    FeedMixin,
    SocialMixin,
    InteractionsMixin,
    ContentMixin,
    UploadMixin,
    DmMixin,
    ThreadsMixin,
):
    """Async-native Instagram client -- proxy-first, fast, deduplicating."""

    def __init__(
        self,
        config: MCPConfig,
        proxy_manager: ProxyManager,
        rate_limiter: AdaptiveRateLimiter,
        cache: SmartCache,
        cookie_manager: Optional[CookieManager] = None,
    ):
        import sys
        _client_mod = sys.modules.get("instagram_mcp.client")
        _curl_available = getattr(_client_mod, "CURL_CFFI_AVAILABLE", CURL_CFFI_AVAILABLE) if _client_mod else CURL_CFFI_AVAILABLE
        if not _curl_available:
            raise FetchError(
                "curl_cffi is not installed. Run: pip install 'curl-cffi>=0.5'"
            )
        self._config = config
        self._proxy_manager = proxy_manager
        self._rate_limiter = rate_limiter
        self._cache = cache
        self._cookie_manager = cookie_manager
        self._delay_simulator = DelaySimulator(
            min_delay_ms=getattr(config, "delay_min_ms", 500),
            max_delay_ms=getattr(config, "delay_max_ms", 2000),
            enabled=True
        )
        self._account_pool = AccountPool(accounts_dir=config.accounts_dir)
        self._account_pool.load_accounts()
        from ..media_cache import MediaCache
        self._media_cache = MediaCache(cache_dir=config.media_cache_dir)
        # Authenticated sessions pool for multi-accounts, keyed by account alias
        self._auth_sessions: Dict[str, AsyncSession] = {}
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

    @property
    def account_pool(self) -> AccountPool:
        return self._account_pool

    @property
    def media_cache(self):
        return self._media_cache

    async def cache_media_urls(self, data: Any) -> Any:
        """Scan data for media URLs, download them, and replace with local file URIs."""
        # Get/reuse a session from the direct pool safely
        try:
            session_or_future = self._get_session(None)
            if asyncio.iscoroutine(session_or_future) or hasattr(session_or_future, "__await__"):
                session = await session_or_future
            else:
                session = session_or_future
        except Exception:
            session = None

        if not session:
            return data
        
        # Helper to avoid duplicating logic
        async def _cache_val(val: str) -> str:
            if not val or not val.startswith("http"):
                return val
            return await self._media_cache.get_or_fetch(val, session)

        from ..models import InstagramProfile, InstagramPost, FeedTagResult, TaggedPost, StoryItem, ReelItem

        if isinstance(data, InstagramProfile):
            if data.profile_pic_url:
                data.profile_pic_url = await _cache_val(data.profile_pic_url)
        elif isinstance(data, InstagramPost):
            if data.display_url:
                data.display_url = await _cache_val(data.display_url)
            if data.thumbnail_url:
                data.thumbnail_url = await _cache_val(data.thumbnail_url)
        elif isinstance(data, TaggedPost):
            if data.display_url:
                data.display_url = await _cache_val(data.display_url)
        elif isinstance(data, StoryItem):
            if data.display_url:
                data.display_url = await _cache_val(data.display_url)
            if data.video_url:
                data.video_url = await _cache_val(data.video_url)
        elif isinstance(data, ReelItem):
            if data.display_url:
                data.display_url = await _cache_val(data.display_url)
            if data.thumbnail_url:
                data.thumbnail_url = await _cache_val(data.thumbnail_url)
        elif isinstance(data, FeedTagResult):
            for post in data.posts:
                await self.cache_media_urls(post)
        elif isinstance(data, list):
            for item in data:
                await self.cache_media_urls(item)
        elif isinstance(data, dict):
            for k, v in list(data.items()):
                if isinstance(v, (str, list, dict, InstagramProfile, InstagramPost, TaggedPost, StoryItem, ReelItem, FeedTagResult)):
                    if isinstance(v, str) and k in ("profile_pic_url", "display_url", "thumbnail_url", "video_url"):
                        data[k] = await _cache_val(v)
                    else:
                        await self.cache_media_urls(v)
        return data

    # ── Session management ───────────────────────────────────────────────────


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
            for s in list(self._auth_sessions.values()):
                try:
                    await s.close()
                except Exception:
                    pass
            self._auth_sessions.clear()

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



def _caption_insights(length: float, hashtags: float, emoji_rate: float, cta_rate: float) -> List[str]:
    tips = []
    if length < 50:
        tips.append("Captions are very short — try 100-150 chars for more context and discoverability")
    elif length > 500:
        tips.append("Captions are long — consider breaking them up with line breaks for readability")
    if hashtags < 5:
        tips.append("Low hashtag count — using 10-15 targeted hashtags improves reach")
    elif hashtags > 25:
        tips.append("High hashtag count — reduce to 10-20 relevant hashtags for better quality signal")
    if emoji_rate < 0.3:
        tips.append("Low emoji usage — emojis in captions increase engagement rate by ~15%")
    if cta_rate < 0.2:
        tips.append("Low CTA usage — adding a call-to-action (e.g. 'comment below') doubles comments")
    if not tips:
        tips.append("Caption strategy looks solid — good length, hashtags, and engagement signals")
    return tips

