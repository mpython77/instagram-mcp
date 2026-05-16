"""
Tests for the 5 new MCP tools:
  instagram_hashtag_deep, instagram_post_bulk, instagram_similar_accounts,
  instagram_niche_top, instagram_account_report
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from collections import defaultdict

# MockToolError and MCP module mocks are set up in conftest.py (loaded first).
MockToolError = sys.modules["mcp.server.fastmcp.exceptions"].ToolError

import pytest
from instagram_mcp.tools import register_tools, sanitize_username, _tool_error
from instagram_mcp.models import (
    HashtagDeepInput, PostBulkInput, SimilarAccountsInput,
    NicheTopInput, AccountReportInput,
)
from instagram_mcp.formatter import (
    format_hashtag_deep_markdown, format_post_bulk_markdown,
    format_similar_accounts_markdown, format_niche_top_markdown,
    format_account_report_markdown, _compute_hashtag_stats,
)
from mcp.server.fastmcp import FastMCP, Context


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_client():
    client = MagicMock()
    client.fetch_user = AsyncMock()
    client.fetch_feed_items = AsyncMock(return_value=[])
    client.fetch_user_feed = AsyncMock(return_value={"items": []})
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
    client.fetch_post_bulk = AsyncMock()
    client.fetch_similar_accounts = AsyncMock()
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
    config.enabled_toolsets = {"all"}
    config.hide_auth_when_no_cookies = False
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


# ─── Sample data ─────────────────────────────────────────────────────────────

def _make_hashtag_posts(n=5) -> list:
    """Create n fake auth-format hashtag posts."""
    return [
        {
            "shortcode":     f"ABC{i}",
            "username":      f"user{i % 3}",
            "like_count":    100 * (i + 1),
            "comment_count": 10 * (i + 1),
            "play_count":    None,
            "media_type":    1 if i % 2 == 0 else 2,
            "taken_at":      1700000000 + i * 3600,
            "verified":      i == 0,
            "account_type":  2,
            "caption":       f"Caption {i}",
        }
        for i in range(n)
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestHashtagDeepInput:
    def test_strips_hash(self):
        assert HashtagDeepInput(tag="#Travel").tag == "travel"

    def test_max_posts_bounds(self):
        m = HashtagDeepInput(tag="x", max_posts=500)
        assert m.max_posts == 500
        with pytest.raises(Exception):
            HashtagDeepInput(tag="x", max_posts=501)

    def test_empty_tag_raises(self):
        with pytest.raises(Exception):
            HashtagDeepInput(tag="   ")


class TestPostBulkInput:
    def test_cleans_urls(self):
        m = PostBulkInput(shortcodes=["https://www.instagram.com/p/ABC123/"])
        assert m.shortcodes == ["ABC123"]

    def test_max_50(self):
        with pytest.raises(Exception):
            PostBulkInput(shortcodes=["x"] * 51)

    def test_empty_raises(self):
        with pytest.raises(Exception):
            PostBulkInput(shortcodes=[])


class TestSimilarAccountsInput:
    def test_strips_at(self):
        assert SimilarAccountsInput(username="@Nike").username == "nike"

    def test_empty_raises(self):
        with pytest.raises(Exception):
            SimilarAccountsInput(username="@")


class TestNicheTopInput:
    def test_sort_by_validation(self):
        with pytest.raises(Exception):
            NicheTopInput(tag="x", sort_by="invalid")

    def test_all_valid_sorts(self):
        for s in ("engagement", "post_count", "total_likes"):
            m = NicheTopInput(tag="x", sort_by=s)
            assert m.sort_by == s


class TestAccountReportInput:
    def test_strips_at(self):
        assert AccountReportInput(username="@Nike").username == "nike"

    def test_max_posts_200(self):
        m = AccountReportInput(username="x", max_posts=200)
        assert m.max_posts == 200
        with pytest.raises(Exception):
            AccountReportInput(username="x", max_posts=201)


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeHashtagStats:
    def test_empty(self):
        stats = _compute_hashtag_stats([], True)
        assert stats["total_posts"] == 0
        assert stats["avg_likes"] == 0
        assert stats["top_accounts"] == []

    def test_aggregates_correctly(self):
        posts = _make_hashtag_posts(6)
        stats = _compute_hashtag_stats(posts, True)
        assert stats["total_posts"] == 6
        assert stats["avg_likes"] > 0
        assert len(stats["top_accounts"]) == 3  # 3 unique users
        # All top accounts have avg_engagement >= 0
        for acc in stats["top_accounts"]:
            assert acc["avg_engagement"] >= 0
        # Sorted descending
        engs = [a["avg_engagement"] for a in stats["top_accounts"]]
        assert engs == sorted(engs, reverse=True)

    def test_media_types(self):
        posts = _make_hashtag_posts(4)
        stats = _compute_hashtag_stats(posts, True)
        assert sum(stats["media_types"].values()) == 4


class TestFormatHashtagDeep:
    def test_empty_posts(self):
        out = format_hashtag_deep_markdown("test", [], auth_used=True)
        assert "#test" in out
        assert "No posts found" in out

    def test_auth_mode(self):
        posts = _make_hashtag_posts(5)
        out = format_hashtag_deep_markdown("fitness", posts, auth_used=True, top_n=3)
        assert "#fitness" in out
        assert "Top Accounts" in out
        assert "Engagement Summary" in out
        assert "auth" in out

    def test_anon_warning(self):
        posts = _make_hashtag_posts(3)
        out = format_hashtag_deep_markdown("travel", posts, auth_used=False)
        assert "Anon mode" in out


class TestFormatPostBulk:
    def test_all_ok(self):
        results = [
            {"shortcode": "SC1", "ok": True, "username": "user1", "is_verified": True,
             "post_type": "photo", "likes": 500, "comments": 10, "view_count": None,
             "play_count": None, "taken_at_str": "2024-01-01", "caption": "Hi"},
            {"shortcode": "SC2", "ok": True, "username": "user2", "is_verified": False,
             "post_type": "video", "likes": 1200, "comments": 50, "view_count": 50000,
             "play_count": None, "taken_at_str": "2024-01-02", "caption": "Bye"},
        ]
        out = format_post_bulk_markdown(results)
        assert "2/2" in out
        assert "SC1" in out
        assert "SC2" in out
        assert "🖼" in out
        assert "🎬" in out

    def test_mixed_ok_failed(self):
        results = [
            {"shortcode": "SC1", "ok": True, "username": "u", "is_verified": False,
             "post_type": "photo", "likes": 100, "comments": 5, "view_count": None,
             "play_count": None, "taken_at_str": "", "caption": ""},
            {"shortcode": "SC2", "ok": False, "error": "Post not found"},
        ]
        out = format_post_bulk_markdown(results)
        assert "1/2" in out
        assert "Failed" in out
        assert "SC2" in out
        assert "not found" in out.lower()

    def test_all_failed(self):
        results = [{"shortcode": "X", "ok": False, "error": "err"}]
        out = format_post_bulk_markdown(results)
        assert "0/1" in out
        assert "Failed" in out


class TestFormatSimilarAccounts:
    def test_basic(self):
        accounts = [
            {"username": "acc1", "full_name": "Acc One", "is_verified": True,
             "is_private": False, "follower_count": 50000, "category": "Sports"},
            {"username": "acc2", "full_name": "Acc Two", "is_verified": False,
             "is_private": True, "follower_count": None, "category": ""},
        ]
        out = format_similar_accounts_markdown("seed", accounts)
        assert "seed" in out
        assert "acc1" in out
        assert "acc2" in out
        assert "50.0K" in out
        assert "🔒" in out

    def test_empty(self):
        out = format_similar_accounts_markdown("x", [])
        assert "0 accounts" in out


class TestFormatNicheTop:
    def test_basic(self):
        accounts = [
            {"username": "top1", "verified": True, "account_type": 2, "post_count": 5,
             "avg_likes": 2000, "avg_comments": 100, "avg_engagement": 2100, "total_likes": 10000},
        ]
        out = format_niche_top_markdown("fitness", accounts, posts_analysed=90, auth_used=True)
        assert "fitness" in out
        assert "top1" in out
        assert "90" in out
        assert "2.0K" in out

    def test_anon_warning(self):
        out = format_niche_top_markdown("x", [], posts_analysed=12, auth_used=False)
        assert "Anon mode" in out


class TestFormatAccountReport:
    def test_combines_sections(self):
        out = format_account_report_markdown("nike", "engagement md", "collab md")
        assert "nike" in out
        assert "engagement md" in out
        assert "collab md" in out

    def test_no_collab(self):
        out = format_account_report_markdown("x", "engagement md", None)
        assert "Collaboration" not in out
        assert "engagement md" in out


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstagramHashtagDeepTool:
    @pytest.mark.asyncio
    async def test_success_auth(self, tools, mock_client, mock_ctx):
        posts = _make_hashtag_posts(5)
        mock_client.fetch_hashtag.return_value = {
            "tag": "fitness", "posts": posts,
            "has_more": False, "auth_used": True, "related_searches": [],
        }
        fn = tools["instagram_hashtag_deep"]
        result = await fn(HashtagDeepInput(tag="fitness", max_posts=90), mock_ctx)
        assert "fitness" in result
        assert "Top Accounts" in result
        mock_client.fetch_hashtag.assert_called_once_with("fitness", max_posts=90)

    @pytest.mark.asyncio
    async def test_not_found(self, tools, mock_client, mock_ctx):
        mock_client.fetch_hashtag.return_value = None
        fn = tools["instagram_hashtag_deep"]
        with pytest.raises(MockToolError):
            await fn(HashtagDeepInput(tag="nonexistent"), mock_ctx)

    @pytest.mark.asyncio
    async def test_exception(self, tools, mock_client, mock_ctx):
        mock_client.fetch_hashtag.side_effect = Exception("network error")
        fn = tools["instagram_hashtag_deep"]
        with pytest.raises(MockToolError):
            await fn(HashtagDeepInput(tag="test"), mock_ctx)


class TestInstagramPostBulkTool:
    @pytest.mark.asyncio
    async def test_success(self, tools, mock_client, mock_ctx):
        mock_client.fetch_post_bulk.return_value = [
            {"shortcode": "SC1", "ok": True, "username": "user1", "is_verified": False,
             "post_type": "photo", "likes": 100, "comments": 5, "view_count": None,
             "play_count": None, "taken_at_str": "2024-01-01", "caption": "test"},
        ]
        fn = tools["instagram_post_bulk"]
        result = await fn(PostBulkInput(shortcodes=["SC1"]), mock_ctx)
        assert "SC1" in result
        mock_client.fetch_post_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception(self, tools, mock_client, mock_ctx):
        mock_client.fetch_post_bulk.side_effect = Exception("fail")
        fn = tools["instagram_post_bulk"]
        with pytest.raises(MockToolError):
            await fn(PostBulkInput(shortcodes=["SC1"]), mock_ctx)


class TestInstagramSimilarAccountsTool:
    @pytest.mark.asyncio
    async def test_success(self, tools, mock_client, mock_ctx):
        mock_client.fetch_similar_accounts.return_value = [
            {"username": "acc1", "full_name": "Acc", "is_verified": True,
             "is_private": False, "follower_count": 1000, "category": "Sports", "pk": "111"},
        ]
        fn = tools["instagram_similar_accounts"]
        result = await fn(SimilarAccountsInput(username="nike"), mock_ctx)
        assert "acc1" in result
        mock_client.fetch_similar_accounts.assert_called_once_with("nike", limit=20)

    @pytest.mark.asyncio
    async def test_no_auth(self, tools, mock_client, mock_ctx):
        mock_client.fetch_similar_accounts.return_value = None
        fn = tools["instagram_similar_accounts"]
        with pytest.raises(MockToolError) as exc_info:
            await fn(SimilarAccountsInput(username="nike"), mock_ctx)
        assert "auth" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_exception(self, tools, mock_client, mock_ctx):
        mock_client.fetch_similar_accounts.side_effect = Exception("fail")
        fn = tools["instagram_similar_accounts"]
        with pytest.raises(MockToolError):
            await fn(SimilarAccountsInput(username="nike"), mock_ctx)


class TestInstagramNicheTopTool:
    @pytest.mark.asyncio
    async def test_success(self, tools, mock_client, mock_ctx):
        posts = _make_hashtag_posts(9)
        mock_client.fetch_hashtag.return_value = {
            "tag": "fitness", "posts": posts,
            "has_more": False, "auth_used": True, "related_searches": [],
        }
        fn = tools["instagram_niche_top"]
        result = await fn(NicheTopInput(tag="fitness", top_n=3), mock_ctx)
        assert "fitness" in result
        assert "Top Accounts" in result

    @pytest.mark.asyncio
    async def test_sort_by_post_count(self, tools, mock_client, mock_ctx):
        posts = _make_hashtag_posts(6)
        mock_client.fetch_hashtag.return_value = {
            "tag": "x", "posts": posts, "has_more": False,
            "auth_used": True, "related_searches": [],
        }
        fn = tools["instagram_niche_top"]
        result = await fn(NicheTopInput(tag="x", sort_by="post_count", top_n=5), mock_ctx)
        assert "post count" in result

    @pytest.mark.asyncio
    async def test_not_found(self, tools, mock_client, mock_ctx):
        mock_client.fetch_hashtag.return_value = None
        fn = tools["instagram_niche_top"]
        with pytest.raises(MockToolError):
            await fn(NicheTopInput(tag="ghost"), mock_ctx)


class TestInstagramAccountReportTool:
    def _make_user(self):
        return {
            "pk": "123", "id": "123", "username": "nike",
            "full_name": "Nike", "biography": "Just do it",
            "edge_followed_by": {"count": 1000000},
            "edge_follow": {"count": 100},
            "is_verified": True, "is_business_account": True,
            "business_category_name": "Sports",
            "is_private": False, "is_joined_recently": False,
            "edge_owner_to_timeline_media": {"count": 500, "edges": []},
            "highlight_reel_count": 5,
            "external_url": "https://nike.com",
        }

    @pytest.mark.asyncio
    async def test_success(self, tools, mock_client, mock_ctx, mock_config):
        mock_client.fetch_user.return_value = self._make_user()
        mock_client.fetch_user_feed.return_value = {"items": []}
        fn = tools["instagram_account_report"]
        result = await fn(AccountReportInput(username="nike"), mock_ctx)
        assert "nike" in result.lower()
        assert "Account Report" in result

    @pytest.mark.asyncio
    async def test_user_not_found(self, tools, mock_client, mock_ctx):
        mock_client.fetch_user.return_value = None
        fn = tools["instagram_account_report"]
        with pytest.raises(MockToolError):
            await fn(AccountReportInput(username="ghost"), mock_ctx)

    @pytest.mark.asyncio
    async def test_no_collab(self, tools, mock_client, mock_ctx):
        mock_client.fetch_user.return_value = self._make_user()
        mock_client.fetch_user_feed.return_value = {"items": []}
        fn = tools["instagram_account_report"]
        result = await fn(
            AccountReportInput(username="nike", include_collab=False), mock_ctx
        )
        assert "Collaboration" not in result

    @pytest.mark.asyncio
    async def test_fetch_error(self, tools, mock_client, mock_ctx):
        mock_client.fetch_user.side_effect = Exception("network error")
        fn = tools["instagram_account_report"]
        with pytest.raises(MockToolError):
            await fn(AccountReportInput(username="x"), mock_ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT METHOD TESTS (fetch_post_bulk, fetch_similar_accounts)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFetchPostBulkClient:
    """Unit tests for InstagramClient.fetch_post_bulk"""

    def _make_client(self):
        import instagram_mcp.client as cli_mod
        cli_mod.CURL_CFFI_AVAILABLE = True
        from instagram_mcp.client import InstagramClient
        from instagram_mcp.config import MCPConfig

        cfg = MagicMock(spec=MCPConfig)
        cfg.ig_user_agent = "test-agent"
        cfg.ig_app_id = "936619743392459"
        cfg.cache_profile_ttl = 300
        cfg.max_retries = 3
        cfg.retry_base_delay = 0.0
        cfg.async_max_clients = 10

        cache = MagicMock()

        async def _gor(key, fn, ttl=None):
            return await fn()

        cache.get_or_fetch = AsyncMock(side_effect=_gor)
        pm = MagicMock()
        pm.get_best_proxy = AsyncMock(return_value=None)
        pm.report_failure = AsyncMock()
        pm.report_success = AsyncMock()
        rl = MagicMock()
        rl.acquire = AsyncMock()
        rl.on_rate_limited = AsyncMock()
        rl.on_success = AsyncMock()
        client = InstagramClient(cfg, pm, rl, cache, cookie_manager=None)
        return client

    @pytest.mark.asyncio
    async def test_all_succeed(self):
        from instagram_mcp.models import PostInfo, PostLocation
        client = self._make_client()

        fake_info = PostInfo(
            shortcode="SC1", username="user1", full_name="User One",
            is_verified=False, post_type="photo", taken_at_str="2024-01-01",
            likes=100, comments=5, post_url="https://www.instagram.com/p/SC1/",
            location=PostLocation(),
        )

        with patch.object(client, "fetch_post", AsyncMock(return_value="<html>")), \
             patch("instagram_mcp.client.InstagramClient.fetch_post", AsyncMock(return_value="<html>")):
            with patch("instagram_mcp.parser.parse_post_html", return_value=fake_info):
                results = await client.fetch_post_bulk(["SC1", "SC2"], max_concurrency=2)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        from instagram_mcp.exceptions import FetchError
        from instagram_mcp.models import PostInfo, PostLocation
        client = self._make_client()

        fake_info = PostInfo(
            shortcode="SC1", username="u", full_name="",
            is_verified=False, post_type="photo", taken_at_str="",
            likes=0, comments=0, post_url="",
            location=PostLocation(),
        )

        async def _fetch_post(sc):
            if sc == "SC1":
                return "<html>"
            raise FetchError("not found")

        with patch.object(client, "fetch_post", _fetch_post), \
             patch("instagram_mcp.parser.parse_post_html", return_value=fake_info):
            results = await client.fetch_post_bulk(["SC1", "SC2"])

        ok     = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        assert len(ok) == 1
        assert len(failed) == 1
        assert "not found" in failed[0]["error"]


class TestFetchSimilarAccountsClient:
    """Unit tests for InstagramClient.fetch_similar_accounts"""

    def _make_client(self, authenticated=True):
        import instagram_mcp.client as cli_mod
        cli_mod.CURL_CFFI_AVAILABLE = True
        from instagram_mcp.client import InstagramClient
        from instagram_mcp.config import MCPConfig

        cfg = MagicMock(spec=MCPConfig)
        cfg.ig_user_agent = "test-agent"
        cfg.ig_app_id = "936619743392459"
        cfg.cache_profile_ttl = 300
        cfg.max_retries = 3
        cfg.retry_base_delay = 0.0
        cfg.async_max_clients = 10

        cache = MagicMock()

        async def _gor(key, fn, ttl=None):
            return await fn()

        cache.get_or_fetch = AsyncMock(side_effect=_gor)
        pm = MagicMock()
        pm.get_best_proxy = AsyncMock(return_value=None)
        pm.report_failure = AsyncMock()
        pm.report_success = AsyncMock()
        rl = MagicMock()
        rl.acquire = AsyncMock()
        rl.on_rate_limited = AsyncMock()
        rl.on_success = AsyncMock()

        cm = MagicMock()
        cm.is_authenticated = authenticated
        cm.cookies = {"csrftoken": "testcsrf"}

        client = InstagramClient(cfg, pm, rl, cache, cookie_manager=cm if authenticated else None)
        return client

    @pytest.mark.asyncio
    async def test_no_auth_returns_none(self):
        client = self._make_client(authenticated=False)
        result = await client.fetch_similar_accounts("nike")
        assert result is None

    @pytest.mark.asyncio
    async def test_success(self):
        client = self._make_client(authenticated=True)

        fake_user = {"pk": "12345", "id": "12345"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "users": [
                {"username": "adidas", "full_name": "Adidas", "pk": "999",
                 "is_verified": True, "is_private": False, "follower_count": 500000,
                 "biography": "sporty", "profile_pic_url": "", "category_name": "Sports"},
            ]
        }
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_resp)

        with patch.object(client, "fetch_user", AsyncMock(return_value=fake_user)), \
             patch.object(client, "_get_auth_session", AsyncMock(return_value=mock_session)):
            result = await client.fetch_similar_accounts("nike", limit=5)

        assert result is not None
        assert len(result) == 1
        assert result[0]["username"] == "adidas"
        assert result[0]["follower_count"] == 500000

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        from instagram_mcp.exceptions import FetchError
        client = self._make_client(authenticated=True)
        with patch.object(client, "fetch_user", AsyncMock(return_value=None)):
            with pytest.raises(FetchError):
                await client.fetch_similar_accounts("ghost")
