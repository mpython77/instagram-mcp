"""Tests for instagram_mcp.metrics module."""
from __future__ import annotations

import asyncio
import time

import pytest

from instagram_mcp.metrics import MetricsCollector, track_tool


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the MetricsCollector singleton before and after each test."""
    MetricsCollector.reset_instance()
    yield
    MetricsCollector.reset_instance()


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_singleton_pattern(self):
        """get_instance returns same instance on repeated calls."""
        a = MetricsCollector.get_instance()
        b = MetricsCollector.get_instance()
        assert a is b

    def test_reset_instance(self):
        """reset_instance creates a fresh instance on next call."""
        a = MetricsCollector.get_instance()
        MetricsCollector.reset_instance()
        b = MetricsCollector.get_instance()
        assert a is not b

    def test_get_metrics_empty(self):
        """Empty collector returns zero counts."""
        collector = MetricsCollector.get_instance()
        metrics = collector.get_metrics()
        assert metrics["total_requests"] == 0
        assert metrics["total_errors"] == 0
        assert metrics["error_rate"] == 0
        assert metrics["cache"]["hits"] == 0
        assert metrics["cache"]["misses"] == 0
        assert metrics["cache"]["hit_rate"] == 0
        assert metrics["tools"] == {}
        assert "uptime_seconds" in metrics

    def test_record_request(self):
        """Recording a request increments count and stores duration."""
        collector = MetricsCollector.get_instance()
        collector.record_request("instagram_profile", 0.5)
        collector.record_request("instagram_profile", 1.0)
        collector.record_request("instagram_feed", 0.3)

        metrics = collector.get_metrics()
        assert metrics["total_requests"] == 3
        assert metrics["tools"]["instagram_profile"]["count"] == 2
        assert metrics["tools"]["instagram_feed"]["count"] == 1
        assert metrics["tools"]["instagram_profile"]["avg_duration_s"] == 0.75

    def test_record_with_error(self):
        """Recording a request with error tracks the error type."""
        collector = MetricsCollector.get_instance()
        collector.record_request("instagram_profile", 0.1, error="TimeoutError")
        collector.record_request("instagram_profile", 0.2, error="TimeoutError")
        collector.record_request("instagram_profile", 0.3, error="ValueError")
        collector.record_request("instagram_profile", 0.4)

        metrics = collector.get_metrics()
        assert metrics["total_requests"] == 4
        assert metrics["total_errors"] == 3
        assert metrics["error_rate"] == 0.75
        tool_metrics = metrics["tools"]["instagram_profile"]
        assert tool_metrics["errors"]["TimeoutError"] == 2
        assert tool_metrics["errors"]["ValueError"] == 1

    def test_get_metrics_with_data(self):
        """Metrics include min, max, avg, p95 durations."""
        collector = MetricsCollector.get_instance()
        # Add 20 requests with predictable durations
        for i in range(20):
            collector.record_request("test_tool", float(i) / 10.0)

        metrics = collector.get_metrics()
        tool = metrics["tools"]["test_tool"]
        assert tool["count"] == 20
        assert tool["min_duration_s"] == 0.0
        assert tool["max_duration_s"] == 1.9
        assert tool["p95_duration_s"] > 0

    def test_cache_hit_miss_tracking(self):
        """Cache hits and misses are tracked correctly."""
        collector = MetricsCollector.get_instance()
        collector.record_cache_hit()
        collector.record_cache_hit()
        collector.record_cache_hit()
        collector.record_cache_miss()

        metrics = collector.get_metrics()
        assert metrics["cache"]["hits"] == 3
        assert metrics["cache"]["misses"] == 1
        assert metrics["cache"]["hit_rate"] == 0.75

    def test_reset(self):
        """Reset clears all collected data."""
        collector = MetricsCollector.get_instance()
        collector.record_request("tool_a", 1.0)
        collector.record_cache_hit()
        collector.record_cache_miss()

        collector.reset()

        metrics = collector.get_metrics()
        assert metrics["total_requests"] == 0
        assert metrics["cache"]["hits"] == 0
        assert metrics["cache"]["misses"] == 0
        assert metrics["tools"] == {}

    def test_duration_cap_at_1000(self):
        """Durations list is capped at 1000 entries per tool."""
        collector = MetricsCollector.get_instance()
        for i in range(1100):
            collector.record_request("busy_tool", 0.01)

        metrics = collector.get_metrics()
        assert metrics["tools"]["busy_tool"]["count"] == 1100
        # Internal list should be capped
        assert len(collector._request_durations["busy_tool"]) == 1000


class TestTrackTool:
    """Tests for the track_tool async context manager."""

    @pytest.mark.asyncio
    async def test_track_tool_context_manager(self):
        """track_tool records duration on successful execution."""
        collector = MetricsCollector.get_instance()

        async with track_tool("my_tool"):
            await asyncio.sleep(0.01)

        metrics = collector.get_metrics()
        assert metrics["tools"]["my_tool"]["count"] == 1
        assert metrics["tools"]["my_tool"]["avg_duration_s"] >= 0.01
        assert metrics["tools"]["my_tool"]["errors"] == {}

    @pytest.mark.asyncio
    async def test_track_tool_records_error(self):
        """track_tool records error type when exception is raised."""
        collector = MetricsCollector.get_instance()

        with pytest.raises(ValueError):
            async with track_tool("failing_tool"):
                raise ValueError("test error")

        metrics = collector.get_metrics()
        assert metrics["tools"]["failing_tool"]["count"] == 1
        assert metrics["tools"]["failing_tool"]["errors"]["ValueError"] == 1

    @pytest.mark.asyncio
    async def test_track_tool_propagates_exception(self):
        """track_tool re-raises the original exception."""
        with pytest.raises(RuntimeError, match="original"):
            async with track_tool("err_tool"):
                raise RuntimeError("original")
