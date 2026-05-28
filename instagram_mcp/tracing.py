"""
instagram_mcp.tracing — OpenTelemetry distributed tracing with no-op fallback.

When the opentelemetry SDK packages are installed and OTEL_EXPORTER_OTLP_ENDPOINT
is set, this module configures a TracerProvider with BatchSpanProcessor and
OTLPSpanExporter. Otherwise, all public functions degrade to no-ops so that
call sites never need to guard imports.

Kill switch: INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1 disables everything.
"""

from __future__ import annotations

import contextlib
import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)


# ─── Kill switch ──────────────────────────────────────────────────────────────

def _kill_switch() -> bool:
    """Return True when the global observability kill switch is active."""
    return os.environ.get("INSTAGRAM_MCP_OBSERVABILITY_DISABLED", "").lower() in ("1", "true")


# ─── Optional OpenTelemetry imports ───────────────────────────────────────────

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBasedTraceIdRatio,
        TraceIdRatioBased,
        ParentBased,
    )
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


# ─── Module state ─────────────────────────────────────────────────────────────

_enabled: bool = False
_provider: Any = None  # TracerProvider | None
_tracer: Any = None  # trace.Tracer | None


# ─── Sampler resolution ───────────────────────────────────────────────────────

def _resolve_sampler() -> Any:
    """Resolve the OTel sampler from OTEL_TRACES_SAMPLER env var."""
    sampler_name = os.environ.get("OTEL_TRACES_SAMPLER", "parentbased_always_on").lower()

    sampler_map = {
        "always_on": ALWAYS_ON,
        "always_off": ALWAYS_OFF,
        "parentbased_always_on": ParentBased(ALWAYS_ON),
        "parentbased_always_off": ParentBased(ALWAYS_OFF),
    }

    if sampler_name in sampler_map:
        return sampler_map[sampler_name]

    # Try ratio-based samplers
    if sampler_name.startswith("traceidratio"):
        ratio = float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))
        return TraceIdRatioBased(ratio)
    if sampler_name.startswith("parentbased_traceidratio"):
        ratio = float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))
        return ParentBasedTraceIdRatio(ratio)

    # Fallback to parentbased_always_on
    logger.warning(
        "Unrecognised OTEL_TRACES_SAMPLER=%r, falling back to parentbased_always_on",
        sampler_name,
    )
    return ParentBased(ALWAYS_ON)


# ─── Public API ───────────────────────────────────────────────────────────────

def configure_tracer() -> None:
    """
    Configure the OpenTelemetry TracerProvider (idempotent).

    Reads env vars:
      - OTEL_EXPORTER_OTLP_ENDPOINT: required to enable tracing
      - OTEL_SERVICE_NAME: service name resource (default "instagram-mcp")
      - OTEL_TRACES_SAMPLER: sampler name (default "parentbased_always_on")

    When the endpoint is unset, deps are missing, or the kill switch is on,
    tracing stays disabled and all operations are no-ops.
    """
    global _enabled, _provider, _tracer

    if _kill_switch():
        logger.info("Tracing disabled: observability kill switch is active")
        _enabled = False
        return

    if not _OTEL_AVAILABLE:
        logger.info("Tracing disabled: opentelemetry packages not installed")
        _enabled = False
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        logger.info("Tracing disabled: OTEL_EXPORTER_OTLP_ENDPOINT not set")
        _enabled = False
        return

    # Avoid re-configuration
    if _enabled and _provider is not None:
        return

    # Import version at configure time to avoid circular imports
    from instagram_mcp import __version__

    service_name = os.environ.get("OTEL_SERVICE_NAME", "instagram-mcp")

    resource = Resource.create({
        "service.name": service_name,
        "service.version": __version__,
    })

    sampler = _resolve_sampler()

    _provider = TracerProvider(
        resource=resource,
        sampler=sampler,
    )
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)

    _tracer = trace.get_tracer("instagram_mcp", __version__)
    _enabled = True

    logger.info(
        "Tracing configured: endpoint=%s, service=%s, sampler=%s",
        endpoint,
        service_name,
        os.environ.get("OTEL_TRACES_SAMPLER", "parentbased_always_on"),
    )


def get_tracer() -> Any:
    """
    Return the configured tracer, or a no-op tracer when tracing is disabled.

    Returns:
        opentelemetry.trace.Tracer (real or no-op)
    """
    if _enabled and _tracer is not None:
        return _tracer

    if _OTEL_AVAILABLE:
        # Return the OTel API no-op tracer
        return trace.get_tracer("instagram_mcp")

    # When OTel is not installed, return a minimal no-op object
    return _NoOpTracer()


@contextmanager
def start_span(
    name: str,
    kind: Any = None,
    **attributes: Any,
) -> Generator[Any, None, None]:
    """
    Context manager that starts a span when tracing is enabled.

    When disabled, yields a no-op context (None) via contextlib.nullcontext.

    Args:
        name: Span name (e.g. "tool.instagram_profile")
        kind: SpanKind (default INTERNAL when OTel available)
        **attributes: Key-value span attributes
    """
    if not _enabled or _tracer is None:
        yield None
        return

    if kind is None:
        kind = trace.SpanKind.INTERNAL

    with _tracer.start_as_current_span(name, kind=kind, attributes=attributes) as span:
        yield span


def shutdown() -> None:
    """Flush pending spans and shut down the tracer provider."""
    global _enabled, _provider, _tracer

    if _provider is not None and hasattr(_provider, "shutdown"):
        try:
            _provider.shutdown()
        except Exception as exc:
            logger.warning("Error shutting down tracer provider: %s", exc)

    _enabled = False
    _provider = None
    _tracer = None


def is_enabled() -> bool:
    """Return True when tracing is actively configured and operational."""
    return _enabled


# ─── No-op fallback when OTel is not installed ────────────────────────────────

class _NoOpTracer:
    """Minimal no-op tracer for when opentelemetry is not installed at all."""

    def start_span(self, name: str, **kwargs: Any) -> "_NoOpSpan":
        return _NoOpSpan()

    def start_as_current_span(self, name: str, **kwargs: Any) -> contextlib.nullcontext:
        return contextlib.nullcontext(_NoOpSpan())


class _NoOpSpan:
    """Minimal no-op span."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Any = None) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# ─── Module exports ───────────────────────────────────────────────────────────

__all__ = [
    "configure_tracer",
    "get_tracer",
    "start_span",
    "shutdown",
    "is_enabled",
]
