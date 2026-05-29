"""Tests for composable request pipeline middleware."""

import asyncio

import pytest

from instagram_mcp.middleware import (
    FingerprintMiddleware,
    MiddlewarePipeline,
    ResponseValidationMiddleware,
    ShadowBanMiddleware,
    TimingJitterMiddleware,
)
from instagram_mcp.fingerprint import FingerprintRotator
from instagram_mcp.shadow_ban import ShadowBanDetector


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestMiddlewarePipeline:
    """Tests for MiddlewarePipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_executes_in_order(self):
        """Middleware should execute in the order they are added."""
        order = []

        class TrackingMiddleware:
            def __init__(self, name):
                self.name = name

            async def process(self, context, call_next):
                order.append(f"before_{self.name}")
                result = await call_next(context)
                order.append(f"after_{self.name}")
                return result

        async def handler(ctx):
            order.append("handler")
            return {"status": "ok"}

        pipeline = MiddlewarePipeline([
            TrackingMiddleware("first"),
            TrackingMiddleware("second"),
            TrackingMiddleware("third"),
        ])

        context = {"handler": handler}
        result = await pipeline.execute(context)

        assert result == {"status": "ok"}
        assert order == [
            "before_first",
            "before_second",
            "before_third",
            "handler",
            "after_third",
            "after_second",
            "after_first",
        ]

    @pytest.mark.asyncio
    async def test_fingerprint_middleware_adds_headers(self):
        """FingerprintMiddleware should add headers to context."""
        rotator = FingerprintRotator(seed=42)
        middleware = FingerprintMiddleware(rotator)

        async def handler(ctx):
            return ctx

        pipeline = MiddlewarePipeline([middleware])
        context = {"handler": handler, "headers": {}}
        result = await pipeline.execute(context)

        assert "Accept-Language" in result["headers"]
        assert "Sec-CH-UA-Platform" in result["headers"]
        assert "Sec-CH-UA-Mobile" in result["headers"]
        assert "impersonate" in result

    @pytest.mark.asyncio
    async def test_shadow_ban_middleware_detects_empty(self):
        """ShadowBanMiddleware should detect empty responses."""
        detector = ShadowBanDetector(threshold=1)
        middleware = ShadowBanMiddleware(detector)

        async def handler(ctx):
            return {"data": None}

        pipeline = MiddlewarePipeline([middleware])
        context = {"handler": handler, "proxy_url": "http://proxy:8080"}
        result = await pipeline.execute(context)

        assert result.get("shadow_ban_suspected") is True

    @pytest.mark.asyncio
    async def test_response_validation_detects_redirect(self):
        """ResponseValidationMiddleware should detect login redirects."""
        middleware = ResponseValidationMiddleware()

        async def handler(ctx):
            return {"status_code": 302, "location": "https://instagram.com/accounts/login/"}

        pipeline = MiddlewarePipeline([middleware])
        context = {"handler": handler}
        result = await pipeline.execute(context)

        assert result.get("redirect_detected") is True

    @pytest.mark.asyncio
    async def test_timing_jitter_middleware(self):
        """TimingJitterMiddleware should call delay simulator."""
        call_count = {"sleep": 0}

        class FakeDelay:
            async def sleep_jitter(self):
                call_count["sleep"] += 1

        middleware = TimingJitterMiddleware(FakeDelay())

        async def handler(ctx):
            return {"done": True}

        pipeline = MiddlewarePipeline([middleware])
        context = {"handler": handler}
        result = await pipeline.execute(context)

        assert call_count["sleep"] == 1
        assert result == {"done": True}

    @pytest.mark.asyncio
    async def test_pipeline_requires_handler(self):
        """Pipeline should raise ValueError if no handler in context."""
        pipeline = MiddlewarePipeline([])
        with pytest.raises(ValueError, match="handler"):
            await pipeline.execute({})
