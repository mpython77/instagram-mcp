"""Tests for AccountMonitor."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


from instagram_mcp.monitor import AccountMonitor, _ts_str


class TestTsStr:
    def test_valid(self):
        result = _ts_str(1716000000)
        assert "UTC" in result

    def test_zero(self):
        _ts_str(0)  # should not raise


@pytest.mark.asyncio
async def test_monitor_add_basic():
    """Add a monitor — seeds last_post_shortcode from fetch."""
    fetch_calls = []

    async def mock_fetch(username, max_posts):
        fetch_calls.append(username)
        return [{"shortcode": "ABC123", "taken_at": 1716000000, "likes_count": 100, "caption": "Hello"}]

    monitor = AccountMonitor(fetch_fn=mock_fetch)
    entry = await monitor.add(username="testuser", webhook_url="https://example.com/hook")

    assert entry["username"] == "testuser"
    assert entry["last_post_shortcode"] == "ABC123"
    assert "testuser" in fetch_calls


@pytest.mark.asyncio
async def test_monitor_add_fetch_fails():
    """Monitor still adds if seed fetch fails."""
    async def failing_fetch(username, max_posts):
        raise RuntimeError("network error")

    monitor = AccountMonitor(fetch_fn=failing_fetch)
    entry = await monitor.add(username="failuser", webhook_url="https://example.com/hook")
    assert entry["username"] == "failuser"
    assert entry["last_post_shortcode"] == ""


@pytest.mark.asyncio
async def test_monitor_add_invalid_webhook():
    async def mock_fetch(u, n):
        return []

    monitor = AccountMonitor(fetch_fn=mock_fetch)
    with pytest.raises(ValueError, match="webhook_url"):
        await monitor.add(username="user", webhook_url="not-a-url")


@pytest.mark.asyncio
async def test_monitor_add_empty_username():
    async def mock_fetch(u, n):
        return []

    monitor = AccountMonitor(fetch_fn=mock_fetch)
    with pytest.raises(ValueError, match="username"):
        await monitor.add(username="", webhook_url="https://example.com/hook")


def test_monitor_remove_existing():
    monitor = AccountMonitor(fetch_fn=AsyncMock(return_value=[]))
    monitor._entries["testuser"] = MagicMock()
    removed = monitor.remove("testuser")
    assert removed is True
    assert "testuser" not in monitor._entries


def test_monitor_remove_nonexistent():
    monitor = AccountMonitor(fetch_fn=AsyncMock(return_value=[]))
    removed = monitor.remove("nobody")
    assert removed is False


def test_monitor_list_empty():
    monitor = AccountMonitor(fetch_fn=AsyncMock(return_value=[]))
    assert monitor.list_active() == []


def test_monitor_stats_initial():
    monitor = AccountMonitor(fetch_fn=AsyncMock(return_value=[]))
    stats = monitor.stats()
    assert stats["running"] is False
    assert stats["monitored_accounts"] == 0
    assert stats["total_checks"] == 0


@pytest.mark.asyncio
async def test_monitor_detects_new_post():
    """Monitor calls webhook when a new post is detected."""
    webhook_calls = []

    async def mock_fetch(username, max_posts):
        return [{"shortcode": "NEW123", "taken_at": 1716001000, "likes_count": 50, "caption": "New!"}]

    async def mock_webhook(url, payload):
        webhook_calls.append(payload)

    from instagram_mcp.monitor import MonitorEntry
    monitor = AccountMonitor(fetch_fn=mock_fetch, http_post_fn=mock_webhook)

    # Seed with an OLD shortcode so NEW123 is "new"
    entry = MonitorEntry(username="nike", webhook_url="https://example.com", interval=300, last_post_shortcode="OLD000")
    monitor._entries["nike"] = entry

    await monitor._check_account(entry)

    assert len(webhook_calls) == 1
    assert webhook_calls[0]["event"] == "new_post"
    assert webhook_calls[0]["shortcode"] == "NEW123"
    assert webhook_calls[0]["username"] == "nike"


@pytest.mark.asyncio
async def test_monitor_no_webhook_if_same_post():
    """Monitor does NOT call webhook if latest post hasn't changed."""
    webhook_calls = []

    async def mock_fetch(username, max_posts):
        return [{"shortcode": "SAME123", "taken_at": 1716000000, "likes_count": 100, "caption": ""}]

    async def mock_webhook(url, payload):
        webhook_calls.append(payload)

    from instagram_mcp.monitor import MonitorEntry
    monitor = AccountMonitor(fetch_fn=mock_fetch, http_post_fn=mock_webhook)
    entry = MonitorEntry(username="nike", webhook_url="https://example.com", interval=300, last_post_shortcode="SAME123")
    monitor._entries["nike"] = entry

    await monitor._check_account(entry)
    assert len(webhook_calls) == 0


@pytest.mark.asyncio
async def test_monitor_test_webhook():
    """test_webhook delivers test payload and returns True."""
    sent = []

    async def mock_post(url, payload):
        sent.append((url, payload))

    monitor = AccountMonitor(fetch_fn=AsyncMock(return_value=[]), http_post_fn=mock_post)
    result = await monitor.test_webhook("https://example.com/hook", "testuser")

    assert result is True
    assert len(sent) == 1
    assert sent[0][1]["event"] == "test"
