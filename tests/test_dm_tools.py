"""Tests for DM tools: instagram_dm_inbox, instagram_dm_thread, instagram_dm_send."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Formatter tests (no network) ─────────────────────────────────────────────

from instagram_mcp.formatter import (
    format_dm_inbox_markdown,
    format_dm_thread_markdown,
    format_dm_send_markdown,
)


class TestDMFormatters:
    def test_inbox_empty(self):
        result = format_dm_inbox_markdown({"threads": [], "count": 0, "has_older": False})
        assert "DM Inbox" in result
        assert "No threads" in result

    def test_inbox_with_threads(self):
        data = {
            "threads": [
                {
                    "thread_id": "tid123",
                    "thread_title": "Test User",
                    "is_group": False,
                    "users": [{"username": "testuser", "full_name": "Test", "is_verified": False}],
                    "has_unread": True,
                    "last_activity_at": 1716000000,
                    "last_message_type": "text",
                    "last_message_text": "Hello!",
                }
            ],
            "count": 1,
            "has_older": False,
        }
        result = format_dm_inbox_markdown(data)
        assert "tid123" in result
        assert "Test User" in result
        assert "Hello!" in result

    def test_inbox_with_pagination(self):
        data = {
            "threads": [{"thread_id": "abc", "thread_title": "A", "is_group": False,
                         "users": [], "has_unread": False, "last_activity_at": 0,
                         "last_message_type": "", "last_message_text": ""}],
            "count": 1,
            "has_older": True,
            "oldest_cursor": "cursor123",
        }
        result = format_dm_inbox_markdown(data)
        assert "cursor123" in result
        assert "More threads" in result

    def test_thread_basic(self):
        data = {
            "thread_id": "tid123",
            "thread_title": "Test Convo",
            "is_group": False,
            "participants": [{"user_id": "111", "username": "alice", "full_name": "Alice"}],
            "messages": [
                {"item_id": "m1", "user_id": "111", "timestamp": 1716000000000, "item_type": "text", "text": "Hi there"},
            ],
            "message_count": 1,
            "has_older": False,
            "oldest_cursor": "",
        }
        result = format_dm_thread_markdown(data)
        assert "Test Convo" in result
        assert "alice" in result
        assert "Hi there" in result

    def test_thread_like_message(self):
        data = {
            "thread_id": "tid",
            "thread_title": "T",
            "is_group": False,
            "participants": [{"user_id": "1", "username": "bob", "full_name": "Bob"}],
            "messages": [
                {"item_id": "m1", "user_id": "1", "timestamp": 0, "item_type": "like", "text": "❤️"},
            ],
            "message_count": 1,
            "has_older": False,
            "oldest_cursor": "",
        }
        result = format_dm_thread_markdown(data)
        assert "❤️" in result

    def test_send_result(self):
        data = {
            "status": "sent",
            "item_id": "item123",
            "timestamp": 1716000000,
            "thread_id": "tid123",
        }
        result = format_dm_send_markdown(data)
        assert "sent" in result.lower()
        assert "tid123" in result


# ── Model tests ───────────────────────────────────────────────────────────────

from instagram_mcp.models import DMInboxInput, DMThreadInput, DMSendInput


class TestDMModels:
    def test_inbox_defaults(self):
        inp = DMInboxInput()
        assert inp.limit == 20
        assert inp.cursor == ""

    def test_inbox_custom(self):
        inp = DMInboxInput(limit=50, cursor="abc123")
        assert inp.limit == 50
        assert inp.cursor == "abc123"

    def test_thread_required(self):
        inp = DMThreadInput(thread_id="tid123")
        assert inp.thread_id == "tid123"
        assert inp.limit == 20

    def test_send_required(self):
        inp = DMSendInput(thread_id="tid", text="Hello world")
        assert inp.thread_id == "tid"
        assert inp.text == "Hello world"

    def test_send_max_length(self):
        with pytest.raises(Exception):
            DMSendInput(thread_id="tid", text="x" * 1001)


# ── Client method tests ───────────────────────────────────────────────────────

import asyncio
from instagram_mcp.exceptions import FetchError


@pytest.mark.asyncio
async def test_fetch_dm_inbox_no_auth():
    """fetch_dm_inbox raises FetchError when not authenticated."""
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter

    config = MCPConfig()
    cache = SmartCache()
    rl = AdaptiveRateLimiter()
    pm = ProxyManager()
    client = InstagramClient(config=config, proxy_manager=pm, rate_limiter=rl, cache=cache, cookie_manager=None)

    with pytest.raises(FetchError, match="authentication"):
        await client.fetch_dm_inbox()

    await client.close()


@pytest.mark.asyncio
async def test_fetch_dm_thread_no_auth():
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter

    config = MCPConfig()
    cache = SmartCache()
    rl = AdaptiveRateLimiter()
    pm = ProxyManager()
    client = InstagramClient(config=config, proxy_manager=pm, rate_limiter=rl, cache=cache, cookie_manager=None)

    with pytest.raises(FetchError, match="authentication"):
        await client.fetch_dm_thread("thread123")

    await client.close()


@pytest.mark.asyncio
async def test_send_dm_no_auth():
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter

    config = MCPConfig()
    cache = SmartCache()
    rl = AdaptiveRateLimiter()
    pm = ProxyManager()
    client = InstagramClient(config=config, proxy_manager=pm, rate_limiter=rl, cache=cache, cookie_manager=None)

    with pytest.raises(FetchError, match="authentication"):
        await client.send_dm_text("thread123", "hello")

    await client.close()


@pytest.mark.asyncio
async def test_send_dm_empty_text():
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter
    from instagram_mcp.cookie_manager import CookieManager

    config = MCPConfig()
    cache = SmartCache()
    rl = AdaptiveRateLimiter()
    pm = ProxyManager()
    cm = MagicMock(spec=CookieManager)
    cm.is_authenticated = True
    cm.cookies = {"csrftoken": "abc"}

    client = InstagramClient(config=config, proxy_manager=pm, rate_limiter=rl, cache=cache, cookie_manager=cm)

    with pytest.raises(FetchError, match="empty"):
        await client.send_dm_text("thread123", "   ")

    await client.close()


@pytest.mark.asyncio
async def test_send_dm_too_long():
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter
    from instagram_mcp.cookie_manager import CookieManager

    config = MCPConfig()
    cache = SmartCache()
    rl = AdaptiveRateLimiter()
    pm = ProxyManager()
    cm = MagicMock(spec=CookieManager)
    cm.is_authenticated = True
    cm.cookies = {"csrftoken": "abc"}

    client = InstagramClient(config=config, proxy_manager=pm, rate_limiter=rl, cache=cache, cookie_manager=cm)

    with pytest.raises(FetchError, match="long"):
        await client.send_dm_text("thread123", "x" * 1001)

    await client.close()



# ── Newly wired DM tools: mute / share_post / media_messages ──────────────────

from instagram_mcp.models import (
    DMMediaMessagesInput,
    DMMuteInput,
    DMSharePostInput,
)


class TestNewDMInputModels:
    def test_mute_defaults(self):
        inp = DMMuteInput(thread_id="tid123")
        assert inp.thread_id == "tid123"
        assert inp.mute is True

    def test_mute_unmute(self):
        inp = DMMuteInput(thread_id="tid123", mute=False)
        assert inp.mute is False

    def test_mute_requires_thread_id(self):
        with pytest.raises(Exception):
            DMMuteInput(thread_id="")

    def test_share_post_required(self):
        inp = DMSharePostInput(media_id="123", username="nike")
        assert inp.media_id == "123"
        assert inp.username == "nike"
        assert inp.text == ""

    def test_share_post_requires_media_id(self):
        with pytest.raises(Exception):
            DMSharePostInput(media_id="", thread_id="tid")

    def test_media_messages_defaults(self):
        inp = DMMediaMessagesInput(thread_id="tid123")
        assert inp.thread_id == "tid123"
        assert inp.limit == 50

    def test_media_messages_limit_bounds(self):
        with pytest.raises(Exception):
            DMMediaMessagesInput(thread_id="tid123", limit=0)
        with pytest.raises(Exception):
            DMMediaMessagesInput(thread_id="tid123", limit=201)


def _client_without_auth():
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter

    return InstagramClient(
        config=MCPConfig(),
        proxy_manager=ProxyManager(),
        rate_limiter=AdaptiveRateLimiter(),
        cache=SmartCache(),
        cookie_manager=None,
    )


@pytest.mark.asyncio
async def test_dm_mute_no_auth():
    client = _client_without_auth()
    with pytest.raises(FetchError, match="authentication"):
        await client.dm_mute("thread123")
    await client.close()


@pytest.mark.asyncio
async def test_dm_share_post_no_auth():
    client = _client_without_auth()
    with pytest.raises(FetchError, match="authentication"):
        await client.dm_share_post("media123", thread_id="thread123")
    await client.close()


@pytest.mark.asyncio
async def test_dm_media_messages_no_auth():
    client = _client_without_auth()
    with pytest.raises(FetchError, match="authentication"):
        await client.dm_media_messages("thread123")
    await client.close()



# ── Regression: dm_media_messages filters on the right key ────────────────────
# fetch_dm_thread emits messages keyed by "item_type" (not "type"). Before the
# fix, dm_media_messages filtered on m.get("type") and always returned [].


@pytest.mark.asyncio
async def test_dm_media_messages_filters_by_item_type():
    client = _client_without_auth()
    client.fetch_dm_thread = AsyncMock(
        return_value={
            "messages": [
                {"item_id": "1", "item_type": "text", "text": "hi"},
                {"item_id": "2", "item_type": "media_share", "media_url": "u"},
                {"item_id": "3", "item_type": "raven_media", "thumb_url": "t"},
                {"item_id": "4", "item_type": "voice_media", "audio_url": "a"},
                {"item_id": "5", "item_type": "like", "text": "<3"},
            ]
        }
    )
    out = await client.dm_media_messages("thread123", limit=50)
    types = {m["item_type"] for m in out}
    assert types == {"media_share", "raven_media", "voice_media"}
    assert len(out) == 3
    await client.close()
