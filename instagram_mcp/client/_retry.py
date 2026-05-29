"""Retry logic mixin for InstagramClient."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from ..exceptions import FetchError
from ._utils import _mask_proxy

logger = logging.getLogger("instagram_mcp.client")


class RetryMixin:
    """Centralised proxy-retry logic."""

    async def _with_proxy_retry(
        self,
        op_name: str,
        single_attempt: Callable[[Optional[str]], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        Run *single_attempt* up to `max_retries` times, swapping proxies between
        attempts. *single_attempt* receives the chosen proxy URL (or None for
        direct) and must return a dict with at least:
          - "ok": bool
          - "status_code": int

        On 429 → swap proxy, no waiting (and tell rate limiter).
        On other failures → swap proxy.
        """
        tried: Set[str] = set()
        last_error_msg = f"{op_name}: all {self._config.max_retries} retries failed"
        last_status = 0

        for attempt in range(self._config.max_retries):
            proxy_url = await self._proxy_manager.get_best_proxy(exclude=tried)
            if proxy_url:
                tried.add(proxy_url)

            start = time.monotonic()
            try:
                result = await single_attempt(proxy_url)
            except FetchError:
                raise  # configuration / fatal — don't retry
            except Exception as exc:
                e_type = type(exc).__name__
                if proxy_url:
                    await self._proxy_manager.report_failure(proxy_url, str(exc))
                # Network/TLS errors → drop the session so we don't reuse a poisoned one
                if any(
                    needle in e_type
                    for needle in ("Connection", "Timeout", "SSL", "Tls")
                ):
                    await self._invalidate_session(proxy_url)
                last_error_msg = f"{e_type}: {exc} [proxy: {_mask_proxy(proxy_url)}]"
                logger.debug(
                    "%s attempt %d failed: %s",
                    op_name, attempt + 1, last_error_msg,
                )
                continue

            latency = time.monotonic() - start
            status = int(result.get("status_code", 0))
            last_status = status

            if status == 429:
                if proxy_url:
                    await self._proxy_manager.report_failure(proxy_url, "429")
                await self._rate_limiter.on_rate_limited()
                logger.debug("%s 429 — swapping proxy (attempt %d)", op_name, attempt + 1)
                continue

            if not result.get("ok"):
                if proxy_url:
                    await self._proxy_manager.report_failure(proxy_url, f"HTTP {status}")
                last_error_msg = f"HTTP {status}"
                logger.debug(
                    "%s HTTP %d — swapping proxy (attempt %d)",
                    op_name, status, attempt + 1,
                )
                continue

            # Success
            if proxy_url:
                await self._proxy_manager.report_success(proxy_url, latency)
            await self._rate_limiter.on_success()
            return result

        # All retries exhausted
        raise FetchError(
            f"{op_name} — tried {self._config.max_retries} proxies, "
            f"last status={last_status}: {last_error_msg}"
        )

    # ── Profile fetch (web_profile_info) ─────────────────────────────────────

