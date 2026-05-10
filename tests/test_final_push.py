import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from instagram_mcp.formatter import (
    format_tagged_by_markdown, 
    format_reposts_markdown, 
    format_deep_feed_markdown,
    format_bulk_results_markdown,
    format_comments_markdown,
    format_profile_markdown,
    format_diagnostics_markdown
)
from instagram_mcp.agents import InfluencerVettingAgent, VettingResult
from instagram_mcp.models import InstagramProfile, FeedTagResult

@pytest.mark.asyncio
async def test_formatter_empty_states():
    p = InstagramProfile(username="user")
    assert "No tagged posts" in format_tagged_by_markdown(p, [])
    assert "No reposts" in format_reposts_markdown(p, [])
    
    ftr = FeedTagResult(posts=[], tags=[], posts_checked=0)
    res = format_deep_feed_markdown(p, ftr)
    assert "0" in res
    # Bulk results empty table
    res_bulk = format_bulk_results_markdown([])
    assert "Bulk Profile Results" in res_bulk

@pytest.mark.asyncio
async def test_analysis_agent_fetch_exception():
    mock_client = MagicMock()
    mock_config = MagicMock()
    agent = InfluencerVettingAgent(mock_client, mock_config)
    
    # Mocking _fetch which is defined in _BaseAgent
    with patch.object(agent, "_fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = Exception("Boom")
        res = await agent.run("user", goal="goal")
        assert res.verdict == "error"
        assert "Fetch failed: Boom" in res.errors

@pytest.mark.asyncio
async def test_analysis_agent_user_none():
    mock_client = MagicMock()
    mock_config = MagicMock()
    agent = InfluencerVettingAgent(mock_client, mock_config)
    
    with patch.object(agent, "_fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None
        res = await agent.run("user", goal="goal")
        assert res.verdict == "not_found"

def test_formatter_various_badges():
    p = InstagramProfile(username="u", is_verified=True, is_private=True, is_business=True, is_professional=True)
    md = format_profile_markdown(p)
    assert "✅" in md
    assert "🏢" in md
    assert "⭐" in md
    assert "🔒" in md

def test_formatter_diagnostics_coverage():
    from instagram_mcp.models import CacheStats, ProxyStatus
    cache_stats = CacheStats(enabled=True, total_entries=1, max_entries=100, hits=1, misses=1, evictions=0, hit_rate=0.5)
    proxy_status = [ProxyStatus(url_masked="http://p1", is_active=True, consecutive_fails=0, total_requests=1, total_success=1, success_rate=1.0, avg_latency_ms=100.0, cooldown_remaining_s=0)]
    proxy_summary = {"total": 1, "active": 1}
    rate_stats = {"rps": 0.5, "tokens": 10, "max_rps": 2.0}
    md = format_diagnostics_markdown(cache_stats, proxy_status, proxy_summary, rate_stats)
    assert "Cache" in md
    assert "Rate Limiter" in md
