import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Mock the mcp module before importing anything that uses it
class MockToolError(Exception):
    pass

mock_exceptions_mod = MagicMock()
mock_exceptions_mod.ToolError = MockToolError

mock_fastmcp_mod = MagicMock()
mock_fastmcp_mod.Context = MagicMock
mock_fastmcp_mod.FastMCP = MagicMock

sys.modules["mcp"] = MagicMock()
sys.modules["mcp.server"] = MagicMock()
sys.modules["mcp.server.fastmcp"] = mock_fastmcp_mod
sys.modules["mcp.server.fastmcp.exceptions"] = mock_exceptions_mod

import pytest
from instagram_mcp.tools import register_tools, sanitize_username, _tool_error, _exception_to_tool_error
from instagram_mcp.models import (
    ProfileInput, DeepFeedInput, EngagementAnalysisInput, CollabNetworkInput,
    CompareProfilesInput, BulkProfilesInput, PostInput, ReelsInput,
    RepostsInput, ServerInput, TaggedByInput, PostCommentsInput,
    CacheStats, PostInfo
)
from instagram_mcp.exceptions import InstagramMCPError
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.exceptions import ToolError

@pytest.fixture
def mock_client():
    client = MagicMock()
    client.fetch_user = AsyncMock()
    client.fetch_feed_items = AsyncMock(return_value=[])
    client.fetch_user_feed = AsyncMock()
    client.fetch_bulk = AsyncMock()
    client.fetch_post = AsyncMock()
    client.fetch_tagged_posts_paginated = AsyncMock()
    client.fetch_reposts_paginated = AsyncMock()
    client.fetch_reels_paginated = AsyncMock()
    client.fetch_comments_paginated = AsyncMock()
    client.cache = MagicMock()
    client.cache.stats = AsyncMock()
    client.cache.clear = AsyncMock()
    client.cache.invalidate_prefix = AsyncMock()
    client.proxy_manager = MagicMock()
    client.proxy_manager.get_all_status = AsyncMock()
    client.proxy_manager.stats = MagicMock()
    client.rate_limiter = MagicMock()
    client.rate_limiter.stats = MagicMock()
    client.cookie_manager = MagicMock()
    client.cookie_manager.is_authenticated = True
    return client

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.cache_profile_ttl = 300
    config.cache_feed_ttl = 600
    config.cache_tagged_ttl = 600
    config.cache_reposts_ttl = 600
    config.cache_reels_ttl = 600
    config.cache_comments_ttl = 600
    config.max_pagination_posts = 50
    return config

@pytest.fixture
def mock_ctx():
    ctx = MagicMock(spec=Context)
    ctx.info = AsyncMock()
    ctx.warn = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.error = AsyncMock()
    ctx.debug = AsyncMock()
    ctx.report_progress = AsyncMock()
    return ctx

@pytest.fixture
def tools(mock_client, mock_config):
    mcp_tools = {}
    mcp = MagicMock(spec=FastMCP)
    
    def tool_decorator(*args, **kwargs):
        def decorator(f):
            name = kwargs.get("name") or f.__name__
            mcp_tools[name] = f
            return f
        return decorator
    
    mcp.tool = tool_decorator
    register_tools(mcp, mock_client, mock_config)
    return mcp_tools

def test_sanitize_username():
    assert sanitize_username("  @TestUser  ") == "testuser"
    with pytest.raises(ValueError):
        sanitize_username("  @  ")

def test_tool_error():
    err = _tool_error("msg", "type", "action")
    assert isinstance(err, MockToolError)
    assert "msg" in str(err)
    assert "type" in str(err)
    assert "action" in str(err)

def test_exception_to_tool_error():
    e = InstagramMCPError("msg")
    err = _exception_to_tool_error(e)
    assert "msg" in str(err)
    
    e2 = Exception("random")
    err2 = _exception_to_tool_error(e2)
    assert "random" in str(err2)

@pytest.mark.asyncio
async def test_instagram_profile_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {
        "id": "123",
        "username": "testuser",
        "full_name": "Test User",
        "biography": "Bio",
        "edge_followed_by": {"count": 100},
        "edge_follow": {"count": 50},
        "edge_owner_to_timeline_media": {"count": 10, "edges": []},
        "is_private": False,
        "is_verified": False,
        "highlight_reel_count": 0,
        "is_professional_account": False,
        "external_url": "",
        "category_name": None,
    }
    
    params = ProfileInput(username="testuser", include_feed=False, check_alive=False)
    result = await tools["instagram_profile"](params, mock_ctx)
    assert "testuser" in result
    assert "Bio" in result

@pytest.mark.asyncio
async def test_instagram_profile_not_found(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = None
    params = ProfileInput(username="notfound", include_feed=False, check_alive=True)
    result = await tools["instagram_profile"](params, mock_ctx)
    assert "NOT_FOUND" in result

@pytest.mark.asyncio
async def test_instagram_profile_error(tools, mock_client, mock_ctx):
    mock_client.fetch_user.side_effect = Exception("API Error")
    params = ProfileInput(username="error")
    with pytest.raises(ToolError):
        await tools["instagram_profile"](params, mock_ctx)

@pytest.mark.asyncio
async def test_instagram_feed_deep(tools, mock_client, mock_ctx):
    import time as _time
    now = int(_time.time())
    mock_client.fetch_user.return_value = {
        "id": "123", "username": "testuser",
        "edge_owner_to_timeline_media": {"count": 2, "edges": [], "page_info": {"has_next_page": False}},
        "edge_followed_by": {"count": 1000},
    }
    mock_client.fetch_feed_items.return_value = [
        {"code": "p1", "taken_at": now - 86400, "like_count": 10, "comment_count": 2, "media_type": 1},
        {"code": "p2", "taken_at": now - 172800, "like_count": 20, "comment_count": 4, "media_type": 1},
    ]
    params = DeepFeedInput(username="testuser", max_posts=10)
    result = await tools["instagram_feed_deep"](params, mock_ctx)
    assert "testuser" in result
    assert "Posts | 2" in result

@pytest.mark.asyncio
async def test_instagram_analyze_engagement(tools, mock_client, mock_ctx):
    import time as _time
    now = int(_time.time())
    mock_client.fetch_user.return_value = {
        "id": "123", "username": "testuser", "edge_followed_by": {"count": 1000},
        "edge_owner_to_timeline_media": {"count": 1, "edges": [], "page_info": {"has_next_page": False}},
    }
    mock_client.fetch_feed_items.return_value = [
        {"code": "p1", "taken_at": now - 86400, "like_count": 10, "comment_count": 2, "media_type": 1},
    ]
    params = EngagementAnalysisInput(username="testuser")
    result = await tools["instagram_analyze_engagement"](params, mock_ctx)
    assert "Engagement Analysis" in result
    assert "testuser" in result

@pytest.mark.asyncio
async def test_instagram_find_collab_network(tools, mock_client, mock_ctx):
    import time as _time
    now = int(_time.time())
    mock_client.fetch_user.return_value = {
        "id": "123", "username": "testuser",
        "edge_owner_to_timeline_media": {"count": 1, "edges": [], "page_info": {"has_next_page": False}},
        "edge_followed_by": {"count": 500},
    }
    mock_client.fetch_feed_items.return_value = [
        {
            "code": "p1",
            "taken_at": now - 86400,
            "media_type": 1,
            "usertags": {"in": [{"user": {"username": "collab1"}}]},
        }
    ]
    params = CollabNetworkInput(username="testuser")
    result = await tools["instagram_find_collab_network"](params, mock_ctx)
    assert "Collaboration Network" in result
    assert "collab1" in result

@pytest.mark.asyncio
async def test_instagram_compare_profiles(tools, mock_client, mock_ctx):
    mock_client.fetch_user.side_effect = [
        {"id": "1", "username": "u1", "edge_followed_by": {"count": 100}, "edge_owner_to_timeline_media": {"count": 10, "edges": []}},
        {"id": "2", "username": "u2", "edge_followed_by": {"count": 200}, "edge_owner_to_timeline_media": {"count": 20, "edges": []}},
    ]
    params = CompareProfilesInput(usernames=["u1", "u2"])
    result = await tools["instagram_compare_profiles"](params, mock_ctx)
    assert "u1" in result
    assert "u2" in result

@pytest.mark.asyncio
async def test_instagram_bulk_check(tools, mock_client, mock_ctx):
    mock_client.fetch_bulk.return_value = [
        {"username": "u1", "found": True, "user": {"id": "1", "username": "u1", "edge_followed_by": {"count": 100}, "edge_owner_to_timeline_media": {"count": 10, "edges": []}}},
        {"username": "u2", "found": False}
    ]
    params = BulkProfilesInput(usernames=["u1", "u2"])
    result = await tools["instagram_bulk_check"](params, mock_ctx)
    assert "u1" in result
    assert "u2" in result

@pytest.mark.asyncio
async def test_instagram_batch_scrape(tools, mock_client, mock_ctx):
    with patch("instagram_mcp.batch_runner.BatchRunner") as mock_runner_cls:
        mock_runner = mock_runner_cls.return_value
        mock_runner.run = AsyncMock(return_value=MagicMock(
            completed=1, total=1, active=1, not_found=0, private=0, dead=0, error=0,
            rate=1.0, elapsed_seconds=1.0
        ))
        from instagram_mcp.tools import BatchScrapeInput
        params = BatchScrapeInput(targets=["u1"])
        result = await tools["instagram_batch_scrape"](params, mock_ctx)
        assert "Batch Scrape Results" in result
        assert "Total targets" in result

@pytest.mark.asyncio
async def test_instagram_server(tools, mock_client, mock_ctx):
    mock_client.cache.stats.return_value = CacheStats(enabled=True, total_entries=10)
    mock_client.proxy_manager.get_all_status.return_value = []
    
    # status
    params = ServerInput(action="status")
    result = await tools["instagram_server"](params, mock_ctx)
    assert "Instagram MCP Server Status" in result
    
    # clear_cache
    mock_client.cache.clear.return_value = 5
    params = ServerInput(action="clear_cache")
    result = await tools["instagram_server"](params, mock_ctx)
    assert "5 entries removed" in result
    
    # clear_user
    mock_client.cache.invalidate_prefix.return_value = 2
    params = ServerInput(action="clear_user", username="testuser")
    result = await tools["instagram_server"](params, mock_ctx)
    assert "testuser" in result
    assert "2 entries removed" in result

@pytest.mark.asyncio
async def test_instagram_tagged_by(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"id": "123", "username": "testuser", "edge_owner_to_timeline_media": {"edges": []}}
    mock_client.fetch_tagged_posts_paginated.return_value = {
        "edges": [{"node": {"shortcode": "t1", "owner": {"username": "other"}}}]
    }
    params = TaggedByInput(username="testuser")
    result = await tools["instagram_tagged_by"](params, mock_ctx)
    assert "Tagged-By Feed" in result
    assert "other" in result

@pytest.mark.asyncio
async def test_instagram_reposts(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"id": "123", "username": "testuser", "edge_owner_to_timeline_media": {"edges": []}}
    mock_client.fetch_reposts_paginated.return_value = {
        "items": [{"shortcode": "r1", "user": {"username": "original"}}]
    }
    params = RepostsInput(username="testuser")
    result = await tools["instagram_reposts"](params, mock_ctx)
    assert "Reposts" in result
    assert "original" in result

@pytest.mark.asyncio
async def test_instagram_post(tools, mock_client, mock_ctx):
    mock_client.fetch_post.return_value = "<html>test</html>"
    with patch("instagram_mcp.tools.parse_post_html") as mock_parse:
        info = PostInfo(
            shortcode="shortcode",
            username="author",
            taken_at=123456789,
            likes=100,
            comments=10
        )
        mock_parse.return_value = info
        
        params = PostInput(post="shortcode")
        result = await tools["instagram_post"](params, mock_ctx)
        assert "author" in result
        assert "100" in result

@pytest.mark.asyncio
async def test_instagram_reels(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"id": "123", "username": "testuser", "edge_owner_to_timeline_media": {"edges": []}}
    mock_client.fetch_reels_paginated.return_value = {
        "edges": [{"node": {"shortcode": "reel1", "play_count": 1000}}]
    }
    params = ReelsInput(username="testuser")
    result = await tools["instagram_reels"](params, mock_ctx)
    assert "Reels" in result
    assert "1.0K" in result

@pytest.mark.asyncio
async def test_instagram_post_comments(tools, mock_client, mock_ctx):
    mock_client.fetch_comments_paginated.return_value = {
        "comments": [{"text": "nice", "user": {"username": "commenter"}}],
        "comment_count": 1
    }
    params = PostCommentsInput(post="shortcode")
    result = await tools["instagram_post_comments"](params, mock_ctx)
    assert "Comments" in result
    assert "commenter" in result
