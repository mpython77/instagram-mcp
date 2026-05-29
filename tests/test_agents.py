import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from instagram_mcp.agents import (
    InfluencerVettingAgent, AccountHealthAgent, CreatorDiscoveryAgent,
    BulkScoringAgent, ContentAuditAgent, _er_score, _followers_score,
    _activity_score, _quality_score, compute_account_score, compute_er,
    VettingResult, HealthReport, ScoredAccount, DiscoveredCreator, ContentAuditReport
)
from instagram_mcp.models import InstagramProfile, InstagramPost, DateRange

@pytest.fixture
def mock_client():
    client = MagicMock()
    client.fetch_user = AsyncMock()
    client.fetch_feed_items = AsyncMock(return_value=[])
    client.fetch_bulk = AsyncMock()
    return client

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.cache_profile_ttl = 300
    config.cache_feed_ttl = 600
    config.max_pagination_posts = 50
    return config

def test_scoring_helpers():
    assert _er_score(10) == 40.0
    assert _er_score(4.5) == 35.0
    assert _er_score(2) == 22.5
    assert _er_score(0.5) == 7.5

    assert _followers_score(10_000_000) == 30.0
    assert _followers_score(0) == 0.0
    assert _followers_score(100) == round(2/7 * 30, 1)

    assert _activity_score(5) == 20.0
    assert _activity_score(20) == 15.0
    assert _activity_score(50) == 8.0
    assert _activity_score(200) == 3.0
    assert _activity_score(500) == 0.0

    profile = InstagramProfile(
        username="test", user_id="123", followers=1000,
        is_verified=True, is_business=True, highlight_count=5, has_reels=True
    )
    assert _quality_score(profile) == 10.0

def test_compute_account_score():
    profile = InstagramProfile(username="test", user_id="123", followers=1000)
    score = compute_account_score(profile, er_pct=5.0, last_post_days=10)
    assert isinstance(score, float)

def test_compute_er():
    profile = InstagramProfile(username="test", user_id="123", followers=1000)
    posts = [
        InstagramPost(shortcode="p1", likes=50, comments=10),
        InstagramPost(shortcode="p2", likes=30, comments=10)
    ]
    # (60 + 40) / 2 / 1000 * 100 = 5.0
    assert compute_er(profile, posts) == 5.0
    assert compute_er(profile, []) == 0.0

@pytest.mark.asyncio
async def test_base_agent_paginate(mock_client, mock_config):
    from instagram_mcp.agents import _BaseAgent
    agent = _BaseAgent(mock_client, mock_config)

    now = int(time.time())
    mock_client.fetch_feed_items.return_value = [
        {"code": "p1", "taken_at": now - 86400, "like_count": 10, "comment_count": 2, "media_type": 1},
        {"code": "p2", "taken_at": now - 172800, "like_count": 20, "comment_count": 4, "media_type": 1},
    ]

    profile = InstagramProfile(username="test", user_id="123")
    items, effective_max = await agent._paginate(profile, max_posts=10, max_age_days=30)
    assert len(items) == 2
    assert effective_max == 10

@pytest.mark.asyncio
async def test_influencer_vetting_agent_run(mock_client, mock_config):
    agent = InfluencerVettingAgent(mock_client, mock_config)

    now = int(time.time())
    mock_client.fetch_user.return_value = {
        "id": "123",
        "username": "testuser",
        "edge_followed_by": {"count": 1000},
        "edge_owner_to_timeline_media": {"count": 10, "edges": []},
    }
    mock_client.fetch_feed_items.return_value = [
        {"code": "p1", "taken_at": now - 86400, "like_count": 10, "comment_count": 2, "media_type": 1},
    ]

    progress_mock = AsyncMock()
    result = await agent.run("testuser", progress_cb=progress_mock)
    assert result.found is True
    assert result.username == "testuser"
    assert progress_mock.call_count == 4

@pytest.mark.asyncio
async def test_influencer_vetting_agent_not_found(mock_client, mock_config):
    agent = InfluencerVettingAgent(mock_client, mock_config)
    mock_client.fetch_user.return_value = None

    result = await agent.run("nonexistent")
    assert result.found is False
    assert result.verdict == "not_found"

@pytest.mark.asyncio
async def test_account_health_agent_run(mock_client, mock_config):
    agent = AccountHealthAgent(mock_client, mock_config)
    mock_client.fetch_user.return_value = {
        "id": "123",
        "username": "testuser",
        "edge_followed_by": {"count": 500},
        "edge_owner_to_timeline_media": {"count": 5, "edges": []},
    }

    result = await agent.run("testuser")
    assert result.found is True
    assert result.status in ("active", "dead")

@pytest.mark.asyncio
async def test_creator_discovery_agent_run(mock_client, mock_config):
    agent = CreatorDiscoveryAgent(mock_client, mock_config)

    now = int(time.time())
    mock_client.fetch_feed_items.return_value = [
        {
            "code": "p1",
            "taken_at": now - 86400,
            "media_type": 1,
            "usertags": {"in": [{"user": {"username": "discovered1"}}]},
        }
    ]
    mock_client.fetch_user.side_effect = [
        {
            "id": "123", "username": "seed",
            "edge_followed_by": {"count": 5000},
            "edge_owner_to_timeline_media": {"count": 20, "edges": []},
        },
        {
            "id": "456", "username": "discovered1",
            "edge_followed_by": {"count": 2000},
            "edge_owner_to_timeline_media": {"count": 10, "edges": [
                {"node": {"taken_at_timestamp": now - 86400}}
            ]},
        }
    ]

    result = await agent.run("seed")
    assert len(result) == 1
    assert result[0].username == "discovered1"

@pytest.mark.asyncio
async def test_bulk_scoring_agent_run(mock_client, mock_config):
    agent = BulkScoringAgent(mock_client, mock_config)

    mock_client.fetch_bulk.return_value = [
        {"username": "user1", "found": True, "user": {"id": "1", "username": "user1", "edge_followed_by": {"count": 100}, "edge_owner_to_timeline_media": {"count": 5, "edges": []}}},
        {"username": "user2", "found": False}
    ]

    result = await agent.run(["user1", "user2"])
    assert len(result) == 2
    assert result[0].username == "user1"
    assert result[0].rank == 1

@pytest.mark.asyncio
async def test_content_audit_agent_run(mock_client, mock_config):
    agent = ContentAuditAgent(mock_client, mock_config)

    now = int(time.time())
    mock_client.fetch_user.return_value = {
        "id": "123", "username": "testuser",
        "edge_followed_by": {"count": 1000},
        "edge_owner_to_timeline_media": {"count": 1, "edges": []},
    }
    mock_client.fetch_feed_items.return_value = [
        {"code": "p1", "taken_at": now - 86400, "like_count": 10, "comment_count": 2, "media_type": 1},
    ]

    result = await agent.run("testuser")
    assert result.found is True
    assert result.posts_analyzed == 1



@pytest.mark.asyncio
async def test_influencer_vetting_survives_pagination_error(mock_client, mock_config):
    """Regression: if the engagement step raises and is swallowed, run() must
    still compute a verdict instead of crashing with UnboundLocalError on
    is_dead / last_post_days (Step 4 reads them)."""
    agent = InfluencerVettingAgent(mock_client, mock_config)
    mock_client.fetch_user.return_value = {
        "id": "123",
        "username": "testuser",
        "edge_followed_by": {"count": 1000},
        "edge_owner_to_timeline_media": {"count": 10, "edges": []},
    }
    # Force the engagement pagination to blow up.
    mock_client.fetch_feed_items.side_effect = Exception("network down")

    result = await agent.run("testuser")
    assert result.found is True
    assert result.verdict in ("recommended", "conditional", "not_recommended", "dead")
    assert any("Engagement analysis failed" in e for e in result.errors)
