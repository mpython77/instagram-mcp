"""Metrics and observability - track request counts, durations, errors, cache stats."""
from __future__ import annotations

import logging
import time
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger("instagram_mcp.metrics")


class MetricsCollector:
    """In-memory metrics collector for instagram-mcp tools."""

    _instance: Optional["MetricsCollector"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._request_counts: Dict[str, int] = defaultdict(int)
        self._request_durations: Dict[str, List[float]] = defaultdict(list)
        self._error_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._started_at: float = time.time()

    @classmethod
    def get_instance(cls) -> "MetricsCollector":
        """Get or create the singleton instance (thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._instance_lock:
            cls._instance = None

    def record_request(
        self, tool_name: str, duration_s: float, error: Optional[str] = None
    ) -> None:
        """Record a tool request execution."""
        with self._lock:
            self._request_counts[tool_name] += 1
            self._request_durations[tool_name].append(duration_s)
            # Keep only last 1000 durations per tool to bound memory
            if len(self._request_durations[tool_name]) > 1000:
                self._request_durations[tool_name] = self._request_durations[
                    tool_name
                ][-1000:]
            if error:
                self._error_counts[tool_name][error] += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def get_metrics(self) -> Dict[str, Any]:
        """Return all metrics as a dict."""
        with self._lock:
            tool_metrics = {}
            for tool_name in sorted(self._request_counts.keys()):
                durations = self._request_durations.get(tool_name, [])
                sorted_d = sorted(durations)
                count = self._request_counts[tool_name]
                tool_metrics[tool_name] = {
                    "count": count,
                    "avg_duration_s": (
                        round(sum(durations) / len(durations), 3)
                        if durations
                        else 0
                    ),
                    "min_duration_s": (
                        round(sorted_d[0], 3) if sorted_d else 0
                    ),
                    "max_duration_s": (
                        round(sorted_d[-1], 3) if sorted_d else 0
                    ),
                    "p95_duration_s": (
                        round(sorted_d[int(len(sorted_d) * 0.95)], 3)
                        if sorted_d
                        else 0
                    ),
                    "errors": dict(self._error_counts.get(tool_name, {})),
                }

            total_requests = sum(self._request_counts.values())
            total_errors = sum(
                sum(errs.values()) for errs in self._error_counts.values()
            )
            cache_total = self._cache_hits + self._cache_misses

            return {
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "total_requests": total_requests,
                "total_errors": total_errors,
                "error_rate": (
                    round(total_errors / total_requests, 3)
                    if total_requests
                    else 0
                ),
                "cache": {
                    "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "hit_rate": (
                        round(self._cache_hits / cache_total, 3)
                        if cache_total
                        else 0
                    ),
                },
                "tools": tool_metrics,
            }

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._request_counts.clear()
            self._request_durations.clear()
            self._error_counts.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self._started_at = time.time()


@asynccontextmanager
async def track_tool(tool_name: str):
    """Async context manager to track tool execution time."""
    collector = MetricsCollector.get_instance()
    start = time.perf_counter()
    error = None
    try:
        yield
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        duration = time.perf_counter() - start
        collector.record_request(tool_name, duration, error)
