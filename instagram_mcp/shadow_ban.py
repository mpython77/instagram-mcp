"""Shadow-ban detection - detects when Instagram returns 200 OK but empty data."""

from __future__ import annotations

import logging
from typing import Any, Dict, Set

logger = logging.getLogger("instagram_mcp.shadow_ban")


class ShadowBanDetector:
    """Tracks per-proxy response patterns to detect shadow-ban conditions.

    If a proxy returns HTTP 200 but empty data N times in a row
    (threshold configurable, default 3), that proxy is quarantined.
    """

    def __init__(self, threshold: int = 3) -> None:
        self._threshold = threshold
        self._counters: Dict[str, int] = {}
        self._quarantined: Set[str] = set()

    def _is_empty_response(self, response_data: Any) -> bool:
        """Determine if a response contains empty/shadow-banned data."""
        if response_data is None:
            return True
        if isinstance(response_data, dict):
            if not response_data:
                return True
            # Check for dict with "data" key that is None or empty
            if "data" in response_data:
                data_val = response_data["data"]
                if data_val is None:
                    return True
                if isinstance(data_val, (dict, list)) and not data_val:
                    return True
            return False
        if isinstance(response_data, list):
            return len(response_data) == 0
        return False

    def check_response(self, proxy_url: str, response_data: Any) -> bool:
        """Check if a response indicates shadow-ban.

        Returns True if shadow-ban is suspected (counter >= threshold).
        Increments counter on empty responses, resets on non-empty.
        """
        if self._is_empty_response(response_data):
            self._counters[proxy_url] = self._counters.get(proxy_url, 0) + 1
            count = self._counters[proxy_url]
            if count >= self._threshold:
                logger.warning(
                    "Shadow-ban suspected on proxy %s: %d consecutive empty 200s",
                    proxy_url,
                    count,
                )
                return True
            return False
        else:
            # Valid response resets the counter
            self._counters[proxy_url] = 0
            return False

    def quarantine_proxy(self, proxy_url: str) -> None:
        """Mark a proxy as quarantined."""
        self._quarantined.add(proxy_url)
        logger.warning("Proxy quarantined: %s", proxy_url)

    def is_quarantined(self, proxy_url: str) -> bool:
        """Check if a proxy is currently quarantined."""
        return proxy_url in self._quarantined

    def reset(self, proxy_url: str) -> None:
        """Reset counter and remove quarantine for a proxy."""
        self._counters.pop(proxy_url, None)
        self._quarantined.discard(proxy_url)

    def stats(self) -> dict:
        """Return current detection statistics."""
        return {
            "counters": dict(self._counters),
            "quarantined": list(self._quarantined),
            "threshold": self._threshold,
        }
