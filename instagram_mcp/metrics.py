"""
Prometheus metrics module with no-op shim fallback.

When `prometheus_client` is not installed or the observability kill switch
(INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1) is active, every public metric object
becomes a lightweight no-op that accepts the same method calls without side effects.

Public API:
    REGISTRY, TOOL_CALLS, TOOL_DURATION, PROXY_REQUESTS, PROXY_LATENCY,
    PROXY_STATE, RATE_LIMITER_RPS, RATE_LIMITER_429S, CIRCUIT_BREAKER_OPENS,
    CACHE_OPERATIONS, ACCOUNT_POOL_STATE, is_enabled,
    start_endpoint, stop_endpoint, push_to_gateway
"""

from __future__ import annotations

import os
from typing import Optional

__all__ = [
    "REGISTRY",
    "TOOL_CALLS",
    "TOOL_DURATION",
    "PROXY_REQUESTS",
    "PROXY_LATENCY",
    "PROXY_STATE",
    "RATE_LIMITER_RPS",
    "RATE_LIMITER_429S",
    "CIRCUIT_BREAKER_OPENS",
    "CACHE_OPERATIONS",
    "ACCOUNT_POOL_STATE",
    "is_enabled",
    "start_endpoint",
    "stop_endpoint",
    "push_to_gateway",
]


# ── Kill switch ──────────────────────────────────────────────────────────────

def _kill_switch() -> bool:
    """Return True when the observability stack is globally disabled."""
    return os.environ.get("INSTAGRAM_MCP_OBSERVABILITY_DISABLED", "").lower() in ("1", "true")


# ── Optional dependency probe ────────────────────────────────────────────────

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False


# ── No-op shim ───────────────────────────────────────────────────────────────

class _NoOpMetric:
    """Drop-in replacement for Counter / Histogram / Gauge when metrics are disabled."""

    def labels(self, **kw):  # noqa: ARG002
        return self

    def inc(self, n=1):  # noqa: ARG002
        return None

    def observe(self, v):  # noqa: ARG002
        return None

    def set(self, v):  # noqa: ARG002
        return None


# ── Histogram bucket boundaries (shared) ─────────────────────────────────────

_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


# ── Metric declarations ──────────────────────────────────────────────────────

if _PROM_AVAILABLE and not _kill_switch():
    REGISTRY = CollectorRegistry()

    TOOL_CALLS = Counter(
        "instagram_mcp_tool_calls_total",
        "Total tool invocations",
        labelnames=["tool", "toolset", "auth_tier", "outcome"],
        registry=REGISTRY,
    )

    TOOL_DURATION = Histogram(
        "instagram_mcp_tool_duration_seconds",
        "Tool call wall-clock duration",
        labelnames=["tool"],
        buckets=_DURATION_BUCKETS,
        registry=REGISTRY,
    )

    PROXY_REQUESTS = Counter(
        "instagram_mcp_proxy_requests_total",
        "Total proxy HTTP requests",
        labelnames=["proxy_id", "outcome"],
        registry=REGISTRY,
    )

    PROXY_LATENCY = Histogram(
        "instagram_mcp_proxy_latency_seconds",
        "Proxy request latency",
        labelnames=["proxy_id"],
        buckets=_DURATION_BUCKETS,
        registry=REGISTRY,
    )

    PROXY_STATE = Gauge(
        "instagram_mcp_proxy_state",
        "Current proxy circuit breaker state (1=active, 0=inactive)",
        labelnames=["proxy_id", "state"],
        registry=REGISTRY,
    )

    RATE_LIMITER_RPS = Gauge(
        "instagram_mcp_rate_limiter_rps",
        "Current adaptive rate limiter RPS",
        labelnames=["scope"],
        registry=REGISTRY,
    )

    RATE_LIMITER_429S = Counter(
        "instagram_mcp_rate_limiter_429s_total",
        "Total 429 responses observed by rate limiter",
        registry=REGISTRY,
    )

    CIRCUIT_BREAKER_OPENS = Counter(
        "instagram_mcp_circuit_breaker_opens_total",
        "Total circuit breaker open transitions",
        labelnames=["scope"],
        registry=REGISTRY,
    )

    CACHE_OPERATIONS = Counter(
        "instagram_mcp_cache_operations_total",
        "Total cache operations",
        labelnames=["op", "result"],
        registry=REGISTRY,
    )

    ACCOUNT_POOL_STATE = Gauge(
        "instagram_mcp_account_pool_state",
        "Current account pool member state (1=active, 0=inactive)",
        labelnames=["alias", "state"],
        registry=REGISTRY,
    )

else:
    # No-op path: prometheus_client missing or kill switch active
    REGISTRY = None
    _noop = _NoOpMetric()
    TOOL_CALLS = _noop
    TOOL_DURATION = _noop
    PROXY_REQUESTS = _noop
    PROXY_LATENCY = _noop
    PROXY_STATE = _noop
    RATE_LIMITER_RPS = _noop
    RATE_LIMITER_429S = _noop
    CIRCUIT_BREAKER_OPENS = _noop
    CACHE_OPERATIONS = _noop
    ACCOUNT_POOL_STATE = _noop


# ── Public helpers ────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Return True when Prometheus metrics are actively collecting."""
    return _PROM_AVAILABLE and not _kill_switch()


# ── Endpoint stubs (implemented in task 1.2) ─────────────────────────────────

def start_endpoint(host: str = "0.0.0.0", port: int = 9090) -> None:
    """Start the HTTP /metrics endpoint. (Stub — implemented in task 1.2.)"""
    pass


def stop_endpoint() -> None:
    """Stop the HTTP /metrics endpoint. (Stub — implemented in task 1.2.)"""
    pass


def push_to_gateway(
    url: str,
    job: str = "instagram_mcp",
    instance: Optional[str] = None,
) -> None:
    """Push metrics to a Prometheus Pushgateway. (Stub — implemented in task 1.2.)"""
    pass
