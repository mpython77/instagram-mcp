"""Composable request pipeline middleware.

This module provides utilities for integration into the request pipeline.
It defines the MiddlewarePipeline and built-in middleware implementations
(ShadowBanMiddleware, CookieHealthMiddleware, FingerprintMiddleware,
RateLimitMiddleware) that can be composed into a processing chain.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, List

logger = logging.getLogger("instagram_mcp.middleware")


class Middleware(ABC):
    """Abstract base class for request pipeline middleware."""

    @abstractmethod
    async def process(self, context: dict, call_next: Callable) -> Any:
        """Process the request context, optionally calling next middleware."""
        ...


class FingerprintMiddleware(Middleware):
    """Applies fingerprint rotation headers to request context."""

    def __init__(self, rotator: Any) -> None:
        self._rotator = rotator

    async def process(self, context: dict, call_next: Callable) -> Any:
        fingerprint = self._rotator.get_fingerprint()
        headers = context.get("headers", {})
        headers.update(fingerprint["headers"])
        context["headers"] = headers
        context["impersonate"] = fingerprint["impersonate"]
        return await call_next(context)


class TimingJitterMiddleware(Middleware):
    """Applies delay before passing to next middleware."""

    def __init__(self, delay_simulator: Any) -> None:
        self._delay = delay_simulator

    async def process(self, context: dict, call_next: Callable) -> Any:
        if self._delay:
            await self._delay.sleep_jitter()
        return await call_next(context)


class ResponseValidationMiddleware(Middleware):
    """After response, checks for login redirects and challenge pages."""

    async def process(self, context: dict, call_next: Callable) -> Any:
        response = await call_next(context)
        # Check if response indicates a redirect to login/challenge
        if isinstance(response, dict):
            status = response.get("status_code", 200)
            location = response.get("location", "").lower()
            if status == 302 and ("login" in location or "challenge" in location):
                logger.warning(
                    "Login/challenge redirect detected: %s", location
                )
                response["redirect_detected"] = True
        return response


class ShadowBanMiddleware(Middleware):
    """After response, checks for empty data patterns using ShadowBanDetector."""

    def __init__(self, detector: Any) -> None:
        self._detector = detector

    async def process(self, context: dict, call_next: Callable) -> Any:
        response = await call_next(context)
        proxy_url = context.get("proxy_url", "direct")
        # Extract response data for shadow-ban check
        data = response if not isinstance(response, dict) else response.get("data", response)
        if self._detector.check_response(proxy_url, data):
            if isinstance(response, dict):
                response["shadow_ban_suspected"] = True
        return response


class MiddlewarePipeline:
    """Chains middlewares and executes them in order.

    The last middleware calls context["handler"] as the final handler.
    """

    def __init__(self, middlewares: List[Middleware]) -> None:
        self._middlewares = middlewares

    async def execute(self, context: dict) -> Any:
        """Execute the middleware pipeline."""
        handler = context.get("handler")
        if handler is None:
            raise ValueError("context must contain a 'handler' callable")

        async def build_chain(index: int, ctx: dict) -> Any:
            if index >= len(self._middlewares):
                # End of chain - call the actual handler
                return await handler(ctx)
            middleware = self._middlewares[index]
            return await middleware.process(
                ctx, lambda c: build_chain(index + 1, c)
            )

        return await build_chain(0, context)
