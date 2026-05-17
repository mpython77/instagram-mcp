"""
Account Monitor — polls Instagram accounts for new posts and fires webhook callbacks.

Usage via MCP tool instagram_monitor:
    action="add"    → start monitoring an account (or list of accounts)
    action="remove" → stop monitoring an account
    action="list"   → show active monitors and their last-seen post
    action="status" → show monitor service health
    action="test"   → send a test webhook to verify the URL works

Webhook payload (HTTP POST, JSON):
    {
        "event": "new_post",
        "username": "nike",
        "shortcode": "DXjuqH9nDVE",
        "post_url": "https://instagram.com/p/DXjuqH9nDVE/",
        "caption": "...",
        "likes": 12345,
        "timestamp": 1716000000,
        "detected_at": 1716000060
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger("instagram_mcp.monitor")


def _mask_url(url: str) -> str:
    """Mask credentials/tokens in a webhook URL."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        path_parts = parsed.path.split("/")
        # Mask the last part which is usually the token
        if len(path_parts) > 1:
            path_parts[-1] = "***"
        masked_path = "/".join(path_parts)
        return urlunparse(parsed._replace(path=masked_path, query=""))
    except Exception:
        return "hidden-url"


class MonitorEntry:
    """State for one monitored account."""

    def __init__(
        self,
        username: str,
        webhook_url: str,
        interval: int,
        last_post_shortcode: str = "",
    ) -> None:
        self.username = username
        self.webhook_url = webhook_url
        self.interval = interval
        self.last_post_shortcode = last_post_shortcode
        self.added_at = int(time.time())
        self.last_check: float = 0.0
        self.notifications_sent: int = 0
        self.consecutive_errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "webhook_url": _mask_url(self.webhook_url),
            "interval_seconds": self.interval,
            "last_post_shortcode": self.last_post_shortcode,
            "added_at": _ts_str(self.added_at),
            "last_check": _ts_str(int(self.last_check)) if self.last_check else "never",
            "notifications_sent": self.notifications_sent,
            "consecutive_errors": self.consecutive_errors,
        }


class AccountMonitor:
    """
    Background service that polls Instagram accounts for new posts
    and POSTs webhook notifications when new content is detected.
    """

    def __init__(
        self,
        fetch_fn: Callable[[str, int], Coroutine[Any, Any, List[Dict[str, Any]]]],
        http_post_fn: Optional[Callable[[str, Dict], Coroutine[Any, Any, None]]] = None,
        default_interval: int = 300,
    ) -> None:
        """
        Args:
            fetch_fn: async (username, max_posts) → list of post dicts
                      Each post must have: shortcode, taken_at, likes_count, caption
            http_post_fn: async (url, payload) → None  — sends the webhook
            default_interval: polling interval in seconds (default 5 min)
        """
        self._fetch_fn = fetch_fn
        self._http_post_fn = http_post_fn or _default_http_post
        self._default_interval = default_interval
        self._entries: Dict[str, MonitorEntry] = {}
        self._task: Optional[asyncio.Task] = None
        self._total_checks: int = 0
        self._total_notifications: int = 0
        self._started_at: Optional[float] = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._loop())
            self._started_at = time.time()
            logger.info("AccountMonitor started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.info("AccountMonitor stopped (checks=%d, notifs=%d)", self._total_checks, self._total_notifications)

    # ── Public API ───────────────────────────────────────────────────────────

    async def add(
        self,
        username: str,
        webhook_url: str,
        interval: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Start monitoring an account."""
        username = username.strip().lstrip("@").lower()
        if not username:
            raise ValueError("username cannot be empty")
        if not webhook_url.startswith(("http://", "https://")):
            raise ValueError("webhook_url must start with http:// or https://")

        # Seed last_post_shortcode with the current latest post so we don't
        # fire a notification for existing content on first check.
        last_shortcode = ""
        try:
            posts = await self._fetch_fn(username, 1)
            if posts:
                last_shortcode = posts[0].get("shortcode", "")
        except Exception as exc:
            logger.warning("Monitor seed fetch failed for @%s: %s", username, exc)

        entry = MonitorEntry(
            username=username,
            webhook_url=webhook_url,
            interval=interval or self._default_interval,
            last_post_shortcode=last_shortcode,
        )
        self._entries[username] = entry
        logger.info("Monitor added: @%s → %s (interval=%ds)", username, webhook_url, entry.interval)
        return entry.to_dict()

    def remove(self, username: str) -> bool:
        """Stop monitoring an account. Returns True if it was being monitored."""
        username = username.strip().lstrip("@").lower()
        if username in self._entries:
            del self._entries[username]
            logger.info("Monitor removed: @%s", username)
            return True
        return False

    def list_active(self) -> List[Dict[str, Any]]:
        """Return all active monitor entries."""
        return [e.to_dict() for e in self._entries.values()]

    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "monitored_accounts": len(self._entries),
            "total_checks": self._total_checks,
            "total_notifications": self._total_notifications,
            "started_at": _ts_str(int(self._started_at)) if self._started_at else "not started",
        }

    async def test_webhook(self, webhook_url: str, username: str = "test") -> bool:
        """Send a test webhook payload. Returns True on success."""
        payload = {
            "event": "test",
            "username": username,
            "message": "instagram-mcp monitor test notification",
            "detected_at": int(time.time()),
        }
        try:
            await self._http_post_fn(webhook_url, payload)
            return True
        except Exception as exc:
            logger.warning("Test webhook failed: %s", exc)
            return False

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(10)  # wake up every 10s, check per-account interval
                now = time.time()
                due = [e for e in self._entries.values() if now - e.last_check >= e.interval]
                if due:
                    await asyncio.gather(*(self._check_account(e) for e in due))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Monitor loop error: %s", exc)

    async def _check_account(self, entry: MonitorEntry) -> None:
        entry.last_check = time.time()
        self._total_checks += 1
        try:
            posts = await self._fetch_fn(entry.username, 5)
        except Exception as exc:
            entry.consecutive_errors += 1
            logger.debug("Monitor fetch failed for @%s: %s", entry.username, exc)
            return

        entry.consecutive_errors = 0
        if not posts:
            return

        latest = posts[0]
        shortcode = latest.get("shortcode", "")
        if not shortcode or shortcode == entry.last_post_shortcode:
            return

        entry.last_post_shortcode = shortcode
        entry.notifications_sent += 1
        self._total_notifications += 1

        payload: Dict[str, Any] = {
            "event": "new_post",
            "username": entry.username,
            "shortcode": shortcode,
            "post_url": f"https://www.instagram.com/p/{shortcode}/",
            "caption": (latest.get("caption") or "")[:500],
            "likes": latest.get("likes_count", 0),
            "timestamp": latest.get("taken_at", 0),
            "detected_at": int(time.time()),
        }

        try:
            await self._http_post_fn(entry.webhook_url, payload)
            logger.info("Webhook sent for @%s new post %s", entry.username, shortcode)
        except Exception as exc:
            logger.warning("Webhook delivery failed for @%s: %s", entry.username, exc)


async def _default_http_post(url: str, payload: Dict[str, Any]) -> None:
    """Default webhook sender using curl_cffi."""
    from curl_cffi.requests import AsyncSession
    async with AsyncSession() as s:
        resp = await s.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "instagram-mcp/1.0"},
            timeout=10,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Webhook returned HTTP {resp.status_code}")


def _ts_str(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)
