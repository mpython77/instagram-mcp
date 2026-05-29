"""Tests for audience intelligence toolset."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from instagram_mcp.tools._helpers import ToolDescriptor
from instagram_mcp.tools.audience import register_audience, TOOLSET_NAME


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_mcp():
    """Mock FastMCP instance that captures tool registrations."""
    mcp = MagicMock()
    mcp.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: fn)
    return mcp


@pytest.fixture
def mock_client_authed():
    """Mock client with authentication."""
    client = MagicMock()
    client.cookie_manager = MagicMock()
    client.cookie_manager.is_authenticated = True
    client.fetch_followers = AsyncMock(return_value=[
        {"username": "real_user1", "media_count": 50, "following_count": 200, "follower_count": 500, "has_anonymous_profile_picture": False, "profile_pic_url": "http://pic.jpg"},
        {"username": "fake_bot1", "media_count": 0, "following_count": 7000, "follower_count": 10, "has_anonymous_profile_picture": True, "profile_pic_url": ""},
        {"username": "real_user2", "media_count": 30, "following_count": 150, "follower_count": 300, "has_anonymous_profile_picture": False, "profile_pic_url": "http://pic2.jpg"},
        {"username": "suspicious1", "media_count": 0, "following_count": 6000, "follower_count": 50, "has_anonymous_profile_picture": True, "profile_pic_url": ""},
        {"username": "normal_user", "media_count": 10, "following_count": 100, "follower_count": 200, "has_anonymous_profile_picture": False, "profile_pic_url": "http://pic3.jpg"},
    ])
    client.fetch_user = AsyncMock(return_value={"user": {"pk": "123", "username": "testuser", "full_name": "Test", "follower_count": 1000, "following_count": 500, "media_count": 100, "is_private": False, "biography": "test bio"}})
    client.fetch_feed_items = AsyncMock(return_value=[
        {"shortcode": "ABC1", "taken_at": 1716000000, "like_count": 100, "comment_count": 10},
        {"shortcode": "ABC2", "taken_at": 1715900000, "like_count": 150, "comment_count": 20},
        {"shortcode": "ABC3", "taken_at": 1715800000, "like_count": 80, "comment_count": 5},
        {"shortcode": "ABC4", "taken_at": 1715700000, "like_count": 50, "comment_count": 3},
    ])
    return client


@pytest.fixture
def mock_client_anon():
    """Mock client without authentication."""
    client = MagicMock()
    client.cookie_manager = MagicMock()
    client.cookie_manager.is_authenticated = False
    client.fetch_user = AsyncMock(return_value={"user": {"pk": "123", "username": "testuser", "full_name": "Test", "follower_count": 1000, "following_count": 500, "media_count": 100, "is_private": False, "biography": "test bio"}})
    client.fetch_feed_items = AsyncMock(return_value=[
        {"shortcode": "ABC1", "taken_at": 1716000000, "like_count": 100, "comment_count": 10},
        {"shortcode": "ABC2", "taken_at": 1715900000, "like_count": 150, "comment_count": 20},
        {"shortcode": "ABC3", "taken_at": 1715800000, "like_count": 80, "comment_count": 5},
    ])
    return client


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.cache_profile_ttl = 300
    cfg.max_pagination_posts = 200
    return cfg


@pytest.fixture
def mock_exporter():
    exp = MagicMock()
    exp.save = AsyncMock(return_value=None)
    return exp


# ─────────────────────────────────────────────────────────────────────────────
# Registration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRegisterAudience:
    def test_returns_list_of_tool_descriptors_authed(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """register_audience returns a list of ToolDescriptors when authed."""
        descriptors = register_audience(mock_mcp, mock_client_authed, mock_config, mock_exporter)
        assert isinstance(descriptors, list)
        assert all(isinstance(d, ToolDescriptor) for d in descriptors)

    def test_registers_3_tools_when_authed(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """All 3 tools are registered when authenticated."""
        descriptors = register_audience(mock_mcp, mock_client_authed, mock_config, mock_exporter)
        assert len(descriptors) == 3
        names = {d.name for d in descriptors}
        assert "instagram_fake_follower_check" in names
        assert "instagram_growth_velocity" in names
        assert "instagram_best_time_to_post" in names

    def test_registers_1_tool_when_anon(self, mock_mcp, mock_client_anon, mock_config, mock_exporter):
        """Only best_time_to_post is registered when not authenticated."""
        descriptors = register_audience(mock_mcp, mock_client_anon, mock_config, mock_exporter)
        assert len(descriptors) == 1
        assert descriptors[0].name == "instagram_best_time_to_post"
        assert descriptors[0].auth_tier == "anon"

    def test_toolset_name(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """All descriptors have the correct toolset name."""
        descriptors = register_audience(mock_mcp, mock_client_authed, mock_config, mock_exporter)
        for d in descriptors:
            assert d.toolset == "audience"


# ─────────────────────────────────────────────────────────────────────────────
# Fake follower check tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFakeFollowerCheck:
    @pytest.mark.asyncio
    async def test_fake_follower_check_runs(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """fake_follower_check tool executes and returns markdown."""
        register_audience(mock_mcp, mock_client_authed, mock_config, mock_exporter)

        # Get the registered function
        calls = mock_mcp.tool.call_args_list
        tool_fns = {}
        for call in calls:
            kwargs = call[1]
            name = kwargs.get("name", "")
            tool_fns[name] = None

        # Call the function directly via the decorator side effect
        from instagram_mcp.models import FakeFollowerCheckInput
        params = FakeFollowerCheckInput(username="testuser", sample_size=10)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        # Find the actual function by inspecting what was passed to mcp.tool
        # Since our mock returns the function itself, we need to capture it
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_authed, mock_config, mock_exporter)

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_fake_follower_check"](params, ctx)

        assert "Fake Follower Analysis" in result
        assert "@testuser" in result
        assert "Score:" in result

    @pytest.mark.asyncio
    async def test_fake_follower_detects_bots(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """Detects suspicious accounts in the follower list."""
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_authed, mock_config, mock_exporter)

        from instagram_mcp.models import FakeFollowerCheckInput
        params = FakeFollowerCheckInput(username="testuser", sample_size=10)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_fake_follower_check"](params, ctx)

        # Should detect the bot accounts
        assert "Zero-post accounts" in result
        assert "Mass-follow bots" in result

    @pytest.mark.asyncio
    async def test_fake_follower_exports_data(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """Exports results via the exporter."""
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_authed, mock_config, mock_exporter)

        from instagram_mcp.models import FakeFollowerCheckInput
        params = FakeFollowerCheckInput(username="testuser", sample_size=10)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        await fn_map["instagram_fake_follower_check"](params, ctx)

        mock_exporter.save.assert_called_once()
        call_args = mock_exporter.save.call_args
        assert call_args[0][0] == "fake_follower_check"
        assert call_args[0][1] == "testuser"


# ─────────────────────────────────────────────────────────────────────────────
# Growth velocity tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGrowthVelocity:
    @pytest.mark.asyncio
    async def test_growth_velocity_runs(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """growth_velocity tool executes and returns markdown."""
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_authed, mock_config, mock_exporter)

        from instagram_mcp.models import GrowthVelocityInput
        params = GrowthVelocityInput(username="testuser", days=30)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_growth_velocity"](params, ctx)

        assert "Growth Velocity" in result
        assert "@testuser" in result

    @pytest.mark.asyncio
    async def test_growth_velocity_no_posts(self, mock_mcp, mock_config, mock_exporter):
        """Returns informative message when no posts found."""
        client = MagicMock()
        client.cookie_manager = MagicMock()
        client.cookie_manager.is_authenticated = True
        client.fetch_user = AsyncMock(return_value={"user": {"pk": "123", "username": "testuser", "full_name": "Test", "follower_count": 1000, "following_count": 500, "media_count": 100, "is_private": False, "biography": "test"}})
        client.fetch_feed_items = AsyncMock(return_value=[])

        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, client, mock_config, mock_exporter)

        from instagram_mcp.models import GrowthVelocityInput
        params = GrowthVelocityInput(username="testuser", days=30)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_growth_velocity"](params, ctx)

        assert "No posts found" in result

    @pytest.mark.asyncio
    async def test_growth_velocity_exports_data(self, mock_mcp, mock_client_authed, mock_config, mock_exporter):
        """Exports results via the exporter."""
        import time as _time
        now = int(_time.time())
        # Set feed items with recent timestamps to ensure they fall within the window
        mock_client_authed.fetch_feed_items = AsyncMock(return_value=[
            {"shortcode": "ABC1", "taken_at": now - 86400, "like_count": 100, "comment_count": 10},
            {"shortcode": "ABC2", "taken_at": now - 172800, "like_count": 150, "comment_count": 20},
            {"shortcode": "ABC3", "taken_at": now - 259200, "like_count": 80, "comment_count": 5},
            {"shortcode": "ABC4", "taken_at": now - 345600, "like_count": 50, "comment_count": 3},
        ])

        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_authed, mock_config, mock_exporter)

        from instagram_mcp.models import GrowthVelocityInput
        params = GrowthVelocityInput(username="testuser", days=30)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        await fn_map["instagram_growth_velocity"](params, ctx)

        mock_exporter.save.assert_called_once()
        call_args = mock_exporter.save.call_args
        assert call_args[0][0] == "growth_velocity"


# ─────────────────────────────────────────────────────────────────────────────
# Best time to post tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBestTimeToPost:
    @pytest.mark.asyncio
    async def test_best_time_to_post_runs(self, mock_mcp, mock_client_anon, mock_config, mock_exporter):
        """best_time_to_post tool executes and returns markdown."""
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_anon, mock_config, mock_exporter)

        from instagram_mcp.models import BestTimeToPostInput
        params = BestTimeToPostInput(username="testuser", max_posts=20)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_best_time_to_post"](params, ctx)

        assert "Best Time to Post" in result
        assert "@testuser" in result

    @pytest.mark.asyncio
    async def test_best_time_shows_hours(self, mock_mcp, mock_client_anon, mock_config, mock_exporter):
        """Returns top hours information."""
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_anon, mock_config, mock_exporter)

        from instagram_mcp.models import BestTimeToPostInput
        params = BestTimeToPostInput(username="testuser", max_posts=20)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_best_time_to_post"](params, ctx)

        assert "Top Hours" in result
        assert "Best Days" in result

    @pytest.mark.asyncio
    async def test_best_time_no_posts(self, mock_mcp, mock_config, mock_exporter):
        """Returns informative message when no posts found."""
        client = MagicMock()
        client.cookie_manager = MagicMock()
        client.cookie_manager.is_authenticated = False
        client.fetch_user = AsyncMock(return_value={"user": {"pk": "123", "username": "testuser", "full_name": "Test", "follower_count": 1000, "following_count": 500, "media_count": 100, "is_private": False, "biography": "test"}})
        client.fetch_feed_items = AsyncMock(return_value=[])

        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, client, mock_config, mock_exporter)

        from instagram_mcp.models import BestTimeToPostInput
        params = BestTimeToPostInput(username="testuser", max_posts=20)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        result = await fn_map["instagram_best_time_to_post"](params, ctx)

        assert "No posts found" in result

    @pytest.mark.asyncio
    async def test_best_time_exports_data(self, mock_mcp, mock_client_anon, mock_config, mock_exporter):
        """Exports results via the exporter."""
        registered_fns = []
        mock_mcp_capture = MagicMock()
        mock_mcp_capture.tool = MagicMock(side_effect=lambda **kwargs: lambda fn: (registered_fns.append((kwargs.get("name"), fn)), fn)[1])

        register_audience(mock_mcp_capture, mock_client_anon, mock_config, mock_exporter)

        from instagram_mcp.models import BestTimeToPostInput
        params = BestTimeToPostInput(username="testuser", max_posts=20)
        ctx = MagicMock()
        ctx.info = AsyncMock()

        fn_map = {name: fn for name, fn in registered_fns}
        await fn_map["instagram_best_time_to_post"](params, ctx)

        mock_exporter.save.assert_called_once()
        call_args = mock_exporter.save.call_args
        assert call_args[0][0] == "best_time_to_post"
