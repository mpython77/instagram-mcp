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
    HashtagInput, SearchInput, FollowersInput, FollowingInput, PostLikersInput,
    StoriesInput, HighlightsInput, LocationPostsInput, AudioReelsInput,
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
    client.fetch_hashtag = AsyncMock()
    client.fetch_search = AsyncMock()
    client.fetch_followers = AsyncMock()
    client.fetch_following = AsyncMock()
    client.fetch_post_likers = AsyncMock()
    client.fetch_stories = AsyncMock()
    client.fetch_highlights = AsyncMock()
    client.fetch_location_posts = AsyncMock()
    client.fetch_audio_reels = AsyncMock()
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
def mock_exporter():
    from instagram_mcp.exporter import JsonExporter
    exp = MagicMock(spec=JsonExporter)
    exp.save = AsyncMock(return_value=None)
    exp.enabled = False
    return exp

@pytest.fixture
def tools(mock_client, mock_config, mock_exporter):
    mcp_tools = {}
    mcp = MagicMock(spec=FastMCP)

    def tool_decorator(*args, **kwargs):
        def decorator(f):
            name = kwargs.get("name") or f.__name__
            mcp_tools[name] = f
            return f
        return decorator

    mcp.tool = tool_decorator
    register_tools(mcp, mock_client, mock_config, mock_exporter)
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

@pytest.mark.asyncio
async def test_instagram_hashtag_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_hashtag.return_value = {
        "tag": "football",
        "posts": [
            {
                "node": {
                    "__typename": "XIGPolarisVideoMedia",
                    "code": "DXUoQBqiCrY",
                    "play_count": 20820894,
                    "user": {"username": "inovinate", "is_verified": True},
                    "caption": {"text": "Amazing football highlights #football"},
                }
            }
        ],
        "has_more": True,
        "related_searches": ["football skills", "football highlights"],
    }
    params = HashtagInput(tag="football")
    result = await tools["instagram_hashtag"](params, mock_ctx)
    assert "football" in result
    assert "inovinate" in result
    assert "DXUoQBqiCrY" in result

@pytest.mark.asyncio
async def test_instagram_hashtag_not_found(tools, mock_client, mock_ctx):
    mock_client.fetch_hashtag.return_value = None
    params = HashtagInput(tag="xyznonexistent999")
    result = await tools["instagram_hashtag"](params, mock_ctx)
    assert "not found" in result.lower() or "xyznonexistent999" in result

@pytest.mark.asyncio
async def test_instagram_hashtag_error(tools, mock_client, mock_ctx):
    mock_client.fetch_hashtag.side_effect = Exception("Network error")
    params = HashtagInput(tag="football")
    with pytest.raises(ToolError):
        await tools["instagram_hashtag"](params, mock_ctx)

@pytest.mark.asyncio
async def test_instagram_search_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_search.return_value = {
        "query": "cristiano",
        "context": "blended",
        "users": [
            {
                "position": 0,
                "pk": "173560420",
                "username": "cristiano",
                "full_name": "Cristiano Ronaldo",
                "is_verified": True,
                "is_private": False,
                "follower_count_text": "664M followers",
                "you_follow_them": True,
                "they_follow_you": False,
                "has_recent_reel": False,
            }
        ],
        "hashtags": [
            {
                "position": 0,
                "id": "12345",
                "name": "cristiano",
                "media_count": 9114743,
                "subtitle": "9.1M posts",
            }
        ],
        "has_more": True,
    }
    params = SearchInput(query="cristiano")
    result = await tools["instagram_search"](params, mock_ctx)
    assert "cristiano" in result
    assert "Cristiano Ronaldo" in result
    assert "664M" in result
    assert "9.1M posts" in result

@pytest.mark.asyncio
async def test_instagram_search_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_search.return_value = None
    params = SearchInput(query="nike")
    result = await tools["instagram_search"](params, mock_ctx)
    assert "auth" in result.lower() or "401" in result

@pytest.mark.asyncio
async def test_instagram_search_error(tools, mock_client, mock_ctx):
    mock_client.fetch_search.side_effect = Exception("Network error")
    params = SearchInput(query="nike")
    with pytest.raises(ToolError):
        await tools["instagram_search"](params, mock_ctx)

_SAMPLE_FOLLOW_USER = {
    "pk": "123", "username": "testuser", "full_name": "Test User",
    "is_verified": True, "is_private": False, "profile_pic_url": "",
    "has_recent_reel": True, "latest_reel_ts": 1700000000,
    "you_follow_them": True, "they_follow_you": False,
    "follow_req_sent": False, "is_bestie": False, "is_muting": False, "is_blocking": False,
}

@pytest.mark.asyncio
async def test_instagram_followers_list_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"pk": "20269764", "username": "adidas"}
    mock_client.fetch_followers.return_value = {
        "user_pk": "20269764",
        "users": [_SAMPLE_FOLLOW_USER],
        "has_more": False,
        "should_limit": True,
        "big_list": False,
        "page_size": 1,
    }
    params = FollowersInput(username="adidas")
    result = await tools["instagram_followers_list"](params, mock_ctx)
    assert "adidas" in result
    assert "testuser" in result
    assert "⚠️" in result  # should_limit warning

@pytest.mark.asyncio
async def test_instagram_followers_list_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"pk": "123", "username": "adidas"}
    mock_client.fetch_followers.return_value = None
    params = FollowersInput(username="adidas")
    result = await tools["instagram_followers_list"](params, mock_ctx)
    assert "auth" in result.lower()

@pytest.mark.asyncio
async def test_instagram_followers_list_error(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"pk": "123", "username": "adidas"}
    mock_client.fetch_followers.side_effect = Exception("Network error")
    params = FollowersInput(username="adidas")
    with pytest.raises(ToolError):
        await tools["instagram_followers_list"](params, mock_ctx)

@pytest.mark.asyncio
async def test_instagram_following_list_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"pk": "20269764", "username": "adidas"}
    mock_client.fetch_following.return_value = {
        "user_pk": "20269764",
        "users": [{**_SAMPLE_FOLLOW_USER, "is_favorite": True}],
        "has_more": True,
        "pages_fetched": 4,
    }
    params = FollowingInput(username="adidas", max_users=200)
    result = await tools["instagram_following_list"](params, mock_ctx)
    assert "adidas" in result
    assert "testuser" in result
    assert "⭐" in result

@pytest.mark.asyncio
async def test_instagram_following_list_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"pk": "123", "username": "adidas"}
    mock_client.fetch_following.return_value = None
    params = FollowingInput(username="adidas")
    result = await tools["instagram_following_list"](params, mock_ctx)
    assert "auth" in result.lower()

@pytest.mark.asyncio
async def test_instagram_following_list_error(tools, mock_client, mock_ctx):
    mock_client.fetch_user.return_value = {"pk": "123", "username": "adidas"}
    mock_client.fetch_following.side_effect = Exception("Network error")
    params = FollowingInput(username="adidas")
    with pytest.raises(ToolError):
        await tools["instagram_following_list"](params, mock_ctx)

@pytest.mark.asyncio
async def test_instagram_post_likers_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_post_likers.return_value = {
        "shortcode": "DXUoQBqiCrY",
        "media_id": "3878902202232220376",
        "users": [_SAMPLE_FOLLOW_USER],
        "user_count": 1671287,
    }
    params = PostLikersInput(post="DXUoQBqiCrY")
    result = await tools["instagram_post_likers"](params, mock_ctx)
    assert "DXUoQBqiCrY" in result
    assert "testuser" in result
    assert "1.7M" in result or "1671" in result

@pytest.mark.asyncio
async def test_instagram_post_likers_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_post_likers.return_value = None
    params = PostLikersInput(post="DXUoQBqiCrY")
    result = await tools["instagram_post_likers"](params, mock_ctx)
    assert "auth" in result.lower()

@pytest.mark.asyncio
async def test_instagram_post_likers_error(tools, mock_client, mock_ctx):
    mock_client.fetch_post_likers.side_effect = Exception("Not found")
    params = PostLikersInput(post="DXUoQBqiCrY")
    with pytest.raises(ToolError):
        await tools["instagram_post_likers"](params, mock_ctx)


_SAMPLE_STORY_ITEM = {
    "pk": "3897791644182935799",
    "shortcode": "DYXvNlYAnj3",
    "taken_at": 1778872993,
    "taken_at_str": "2026-05-15 10:23",
    "expiring_at": 1778959393,
    "media_type": 1,
    "duration_secs": 0.0,
    "width": 1170,
    "height": 2080,
    "thumbnail_url": "https://example.com/story.jpg",
    "caption": "",
    "accessibility_caption": "Photo by Cristiano Ronaldo",
    "is_paid_partnership": False,
    "can_reshare": True,
    "can_reply": False,
    "has_audio": False,
    "mentions": ["someuser"],
    "hashtags": [],
    "linked_post_code": "",
    "music_title": "",
    "music_artist": "",
}


@pytest.mark.asyncio
async def test_instagram_stories_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_stories.return_value = {
        "username": "cristiano",
        "user_pk": "173560420",
        "story_count": 1,
        "expiring_at": 1778959393,
        "can_reply": False,
        "can_reshare": True,
        "is_verified": True,
        "items": [_SAMPLE_STORY_ITEM],
    }
    params = StoriesInput(username="cristiano")
    result = await tools["instagram_stories"](params, mock_ctx)
    assert "@cristiano" in result
    assert "Stories" in result


@pytest.mark.asyncio
async def test_instagram_stories_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_stories.return_value = None
    params = StoriesInput(username="cristiano")
    with pytest.raises(ToolError):
        await tools["instagram_stories"](params, mock_ctx)


@pytest.mark.asyncio
async def test_instagram_stories_error(tools, mock_client, mock_ctx):
    mock_client.fetch_stories.side_effect = Exception("stories HTTP 403")
    params = StoriesInput(username="cristiano")
    with pytest.raises(ToolError):
        await tools["instagram_stories"](params, mock_ctx)


_SAMPLE_HIGHLIGHT = {
    "id": "highlight:18137625565499186",
    "title": "Travel",
    "media_count": 12,
    "created_at": 1772641522,
    "created_at_str": "2026-02-01",
    "updated_at": 1772816304,
    "is_pinned": False,
    "can_reply": False,
    "can_reshare": True,
    "cover_url": "https://example.com/cover.jpg",
    "items": [],
}


@pytest.mark.asyncio
async def test_instagram_highlights_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_highlights.return_value = {
        "username": "iamjasyra",
        "user_pk": "403019875",
        "is_verified": False,
        "highlight_count": 1,
        "highlights": [_SAMPLE_HIGHLIGHT],
    }
    params = HighlightsInput(username="iamjasyra")
    result = await tools["instagram_highlights"](params, mock_ctx)
    assert "@iamjasyra" in result
    assert "Highlights" in result


@pytest.mark.asyncio
async def test_instagram_highlights_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_highlights.return_value = None
    params = HighlightsInput(username="iamjasyra")
    with pytest.raises(ToolError):
        await tools["instagram_highlights"](params, mock_ctx)


@pytest.mark.asyncio
async def test_instagram_highlights_error(tools, mock_client, mock_ctx):
    mock_client.fetch_highlights.side_effect = Exception("highlights HTTP 401")
    params = HighlightsInput(username="iamjasyra")
    with pytest.raises(ToolError):
        await tools["instagram_highlights"](params, mock_ctx)


# ── Location Posts ─────────────────────────────────────────────────────────────

_SAMPLE_LOCATION_POST = {
    "shortcode": "DXabc123",
    "media_type": 1,
    "like_count": 150,
    "comment_count": 12,
    "play_count": 0,
    "taken_at": 1748000000,
    "taken_at_str": "2025-05-23 10:00 UTC",
    "username": "testuser",
    "full_name": "Test User",
    "is_verified": False,
    "caption": "Beautiful place!",
    "location_name": "Tashkent",
}


@pytest.mark.asyncio
async def test_instagram_location_posts_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_location_posts.return_value = {
        "location_id": "213385402",
        "location_name": "Tashkent",
        "posts": [_SAMPLE_LOCATION_POST],
        "post_count": 1,
        "more_available": False,
    }
    params = LocationPostsInput(location_name="Tashkent")
    result = await tools["instagram_location_posts"](params, mock_ctx)
    assert "Tashkent" in result
    assert "Location Posts" in result


@pytest.mark.asyncio
async def test_instagram_location_posts_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_location_posts.return_value = None
    params = LocationPostsInput(location_id="213385402")
    with pytest.raises(ToolError):
        await tools["instagram_location_posts"](params, mock_ctx)


@pytest.mark.asyncio
async def test_instagram_location_posts_error(tools, mock_client, mock_ctx):
    mock_client.fetch_location_posts.side_effect = Exception("location_posts HTTP 401")
    params = LocationPostsInput(location_id="213385402")
    with pytest.raises(ToolError):
        await tools["instagram_location_posts"](params, mock_ctx)


# ── Audio Reels ────────────────────────────────────────────────────────────────

_SAMPLE_AUDIO_REEL = {
    "shortcode": "DXreel456",
    "media_type": 2,
    "like_count": 2500,
    "comment_count": 80,
    "play_count": 45000,
    "taken_at": 1748100000,
    "taken_at_str": "2025-05-24 08:00 UTC",
    "username": "reelcreator",
    "full_name": "Reel Creator",
    "is_verified": True,
    "caption": "This beat goes hard!",
    "location_name": "",
}


@pytest.mark.asyncio
async def test_instagram_audio_reels_basic(tools, mock_client, mock_ctx):
    mock_client.fetch_audio_reels.return_value = {
        "audio_cluster_id": "260841894490983",
        "music_title": "Blinding Lights",
        "music_artist": "The Weeknd",
        "total_reels_str": "3.2M",
        "posts": [_SAMPLE_AUDIO_REEL],
        "more_available": False,
    }
    params = AudioReelsInput(audio_cluster_id="260841894490983")
    result = await tools["instagram_audio_reels"](params, mock_ctx)
    assert "Audio Reels" in result
    assert "Blinding Lights" in result


@pytest.mark.asyncio
async def test_instagram_audio_reels_no_auth(tools, mock_client, mock_ctx):
    mock_client.fetch_audio_reels.return_value = None
    params = AudioReelsInput(audio_cluster_id="260841894490983")
    with pytest.raises(ToolError):
        await tools["instagram_audio_reels"](params, mock_ctx)


@pytest.mark.asyncio
async def test_instagram_audio_reels_error(tools, mock_client, mock_ctx):
    mock_client.fetch_audio_reels.side_effect = Exception("audio_reels HTTP 401")
    params = AudioReelsInput(audio_cluster_id="260841894490983")
    with pytest.raises(ToolError):
        await tools["instagram_audio_reels"](params, mock_ctx)
