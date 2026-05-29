"""Cookie health monitoring - proactive cookie expiry detection."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger("instagram_mcp.cookie_health")

# Critical cookies for Instagram session
_CRITICAL_COOKIES = ("sessionid", "ds_user_id", "csrftoken")

# 24 hours in seconds
_EXPIRY_SOON_THRESHOLD = 86400


class CookieHealthMonitor:
    """Monitors cookie health and detects expiring/expired sessions.

    Accepts a CookieManager instance and inspects its cookies for
    expiry information.
    """

    def __init__(self, cookie_manager: Any) -> None:
        self._cookie_manager = cookie_manager

    def _get_cookies(self) -> Dict[str, Any]:
        """Retrieve cookies dict from the cookie manager."""
        if hasattr(self._cookie_manager, "cookies"):
            return self._cookie_manager.cookies or {}
        if hasattr(self._cookie_manager, "_cookies"):
            return self._cookie_manager._cookies or {}
        return {}

    def check_health(self) -> dict:
        """Check cookie health status.

        Returns:
            Dict with keys: healthy, cookies_checked, expiring_soon, expired
        """
        cookies = self._get_cookies()
        now = time.time()
        expiring_soon: List[str] = []
        expired: List[str] = []
        cookies_checked = 0

        for name in _CRITICAL_COOKIES:
            if name not in cookies:
                expired.append(name)
                continue
            cookies_checked += 1

        # Check cookie expiry metadata if available
        cookie_expiry = getattr(self._cookie_manager, "_cookie_expiry", None)
        if cookie_expiry and isinstance(cookie_expiry, dict):
            for name, exp_time in cookie_expiry.items():
                if name in _CRITICAL_COOKIES:
                    if exp_time <= now:
                        if name not in expired:
                            expired.append(name)
                    elif exp_time - now < _EXPIRY_SOON_THRESHOLD:
                        expiring_soon.append(name)

        healthy = len(expired) == 0 and cookies_checked > 0

        if expired:
            logger.warning("Expired or missing cookies: %s", expired)
        if expiring_soon:
            logger.warning("Cookies expiring soon: %s", expiring_soon)

        return {
            "healthy": healthy,
            "cookies_checked": cookies_checked,
            "expiring_soon": expiring_soon,
            "expired": expired,
        }

    @property
    def needs_refresh(self) -> bool:
        """True if any critical cookie is expired or expiring within 24h."""
        health = self.check_health()
        return len(health["expired"]) > 0 or len(health["expiring_soon"]) > 0
