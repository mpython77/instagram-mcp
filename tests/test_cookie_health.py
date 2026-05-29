"""Tests for cookie health monitoring module."""

import time

import pytest

from instagram_mcp.cookie_health import CookieHealthMonitor


class FakeCookieManager:
    """Fake CookieManager for testing."""

    def __init__(self, cookies=None, expiry=None):
        self.cookies = cookies or {}
        self._cookie_expiry = expiry or {}


class TestCookieHealthMonitor:
    """Tests for CookieHealthMonitor."""

    def test_healthy_cookies(self):
        """All critical cookies present should report healthy."""
        cm = FakeCookieManager(
            cookies={
                "sessionid": "abc123",
                "ds_user_id": "12345",
                "csrftoken": "xyz789",
            }
        )
        monitor = CookieHealthMonitor(cm)
        health = monitor.check_health()

        assert health["healthy"] is True
        assert health["cookies_checked"] == 3
        assert health["expired"] == []
        assert health["expiring_soon"] == []

    def test_no_cookies_unhealthy(self):
        """No cookies at all should report unhealthy."""
        cm = FakeCookieManager(cookies={})
        monitor = CookieHealthMonitor(cm)
        health = monitor.check_health()

        assert health["healthy"] is False
        assert health["cookies_checked"] == 0
        assert "sessionid" in health["expired"]
        assert "ds_user_id" in health["expired"]
        assert "csrftoken" in health["expired"]

    def test_needs_refresh_property(self):
        """needs_refresh should be True when cookies are expired/missing."""
        # Missing cookies
        cm = FakeCookieManager(cookies={})
        monitor = CookieHealthMonitor(cm)
        assert monitor.needs_refresh is True

        # All present
        cm_full = FakeCookieManager(
            cookies={
                "sessionid": "abc",
                "ds_user_id": "123",
                "csrftoken": "xyz",
            }
        )
        monitor_full = CookieHealthMonitor(cm_full)
        assert monitor_full.needs_refresh is False

    def test_expiring_soon_detected(self):
        """Cookies expiring within 24h should be flagged."""
        now = time.time()
        cm = FakeCookieManager(
            cookies={
                "sessionid": "abc",
                "ds_user_id": "123",
                "csrftoken": "xyz",
            },
            expiry={
                "sessionid": now + 3600,  # 1 hour from now - expiring soon
            },
        )
        monitor = CookieHealthMonitor(cm)
        health = monitor.check_health()

        assert "sessionid" in health["expiring_soon"]
        assert monitor.needs_refresh is True

    def test_expired_cookie_detected(self):
        """Cookies with past expiry should be flagged as expired."""
        now = time.time()
        cm = FakeCookieManager(
            cookies={
                "sessionid": "abc",
                "ds_user_id": "123",
                "csrftoken": "xyz",
            },
            expiry={
                "sessionid": now - 100,  # Already expired
            },
        )
        monitor = CookieHealthMonitor(cm)
        health = monitor.check_health()

        assert "sessionid" in health["expired"]
        assert health["healthy"] is False
