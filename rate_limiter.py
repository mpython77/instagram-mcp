"""
Adaptive token-bucket rate limiter.

Features:
  - Token bucket: configurable RPS and burst
  - Adaptive: slows down on 429, speeds up on success
  - Asymmetric backoff: moderate drop, faster recovery
  - Circuit breaker: opens after consecutive 429s, sleeps once, halves max_rate
    (max_rate is gradually restored after sustained successes)
  - Jitter: random sleep offset to avoid thundering herd
  - Metrics: request/wait/429 counters
  - Async: works with asyncio
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

logger = logging.getLogger("instagram_mcp.rate_limiter")


class AdaptiveRateLimiter:
    """
    Adaptive token-bucket rate limiter.

    Public API:
      - acquire()              — get one token (waits if necessary)
      - on_rate_limited()      — slow down on 429
      - on_success()           — gradually speed up on success
      - is_circuit_open        — True when circuit breaker has tripped recently
      - stats / get_metrics()  — diagnostics
    """

    __slots__ = (
        "_rate", "_base_rate", "_min_rate", "_max_rate", "_max_rate_ceiling",
        "_burst", "_tokens", "_last_refill",
        "_lock",
        "_backoff_factor", "_recovery_factor",
        "_circuit_breaker_threshold", "_circuit_breaker_cooldown",
        "_request_jitter",
        "_backoff_count", "_in_backoff",
        "_consecutive_429s", "_consecutive_successes",
        "_total_requests", "_total_429s", "_total_waits", "_total_wait_time",
    )

    def __init__(
        self,
        rate: float = 2.0,
        burst: int = 5,
        min_rate: float = 0.3,
        backoff_factor: float = 0.7,
        recovery_factor: float = 1.15,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown: float = 60.0,
        request_jitter: float = 0.1,
    ):
        self._rate = rate
        self._base_rate = rate
        self._min_rate = min_rate
        self._max_rate = rate * 2.5
        self._max_rate_ceiling = rate * 2.5  # absolute upper bound — restored by `on_success`
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

        self._backoff_factor = backoff_factor
        self._recovery_factor = recovery_factor
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_cooldown = circuit_breaker_cooldown
        self._request_jitter = request_jitter

        self._backoff_count: int = 0
        self._in_backoff: bool = False

        self._consecutive_429s: int = 0
        self._consecutive_successes: int = 0

        self._total_requests: int = 0
        self._total_429s: int = 0
        self._total_waits: int = 0
        self._total_wait_time: float = 0.0

    def _refill(self) -> None:
        """Refill tokens based on elapsed time. Caller must hold the lock."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self) -> float:
        """Acquire one token. Waits until a token is available."""
        waited = 0.0
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._total_requests += 1
                    return waited
                # Compute exact wait based on current rate / token deficit
                deficit = 1.0 - self._tokens
                rate = max(self._rate, 0.1)
                sleep_time = deficit / rate

            jitter = random.uniform(0, self._request_jitter)
            total_sleep = sleep_time + jitter
            await asyncio.sleep(total_sleep)
            waited += total_sleep
            self._total_waits += 1
            self._total_wait_time += total_sleep

    async def on_rate_limited(self) -> None:
        """429 response — apply backoff and (possibly) trip the circuit breaker."""
        do_cooldown = False
        cooldown = 0.0
        async with self._lock:
            old_rate = self._rate
            self._rate = max(self._min_rate, self._rate * self._backoff_factor)
            self._backoff_count += 1
            self._in_backoff = True
            self._consecutive_429s += 1
            self._consecutive_successes = 0
            self._total_429s += 1
            logger.warning(
                "Rate limited! Rate decreased: %.2f → %.2f rps "
                "(backoff #%d, consecutive_429s=%d)",
                old_rate, self._rate, self._backoff_count, self._consecutive_429s,
            )

            if self._consecutive_429s >= self._circuit_breaker_threshold:
                logger.error(
                    "Circuit breaker opened after %d consecutive 429s — "
                    "sleeping %.0fs then halving max_rate",
                    self._consecutive_429s, self._circuit_breaker_cooldown,
                )
                cooldown = self._circuit_breaker_cooldown
                self._consecutive_429s = 0
                # Halve max_rate — but never below min_rate
                self._max_rate = max(self._min_rate, self._max_rate * 0.5)
                do_cooldown = True

        if do_cooldown:
            await asyncio.sleep(cooldown)

    async def on_success(self) -> None:
        """Successful request — accelerate recovery; gradually restore max_rate."""
        async with self._lock:
            self._consecutive_429s = 0
            self._consecutive_successes += 1

            # Gradually restore max_rate ceiling after sustained successes
            # (50 successes in a row → bump max_rate back toward absolute ceiling)
            if (
                self._consecutive_successes > 0
                and self._consecutive_successes % 20 == 0
                and self._max_rate < self._max_rate_ceiling
            ):
                self._max_rate = min(self._max_rate_ceiling, self._max_rate * 1.5)
                logger.info(
                    "max_rate restored toward ceiling: %.2f rps",
                    self._max_rate,
                )

            self._rate = min(self._max_rate, self._rate * self._recovery_factor)
            if self._in_backoff and self._rate >= self._base_rate * 0.9:
                self._in_backoff = False
                self._backoff_count = 0

    @property
    def is_circuit_open(self) -> bool:
        return self._consecutive_429s >= self._circuit_breaker_threshold

    @property
    def current_rate(self) -> float:
        return self._rate

    def get_metrics(self) -> dict:
        return {
            "total_requests": self._total_requests,
            "total_429s": self._total_429s,
            "total_waits": self._total_waits,
            "total_wait_time": round(self._total_wait_time, 3),
        }

    @property
    def stats(self) -> dict:
        return {
            "current_rps": round(self._rate, 2),
            "base_rps": self._base_rate,
            "min_rps": self._min_rate,
            "max_rps": round(self._max_rate, 2),
            "burst": self._burst,
            "tokens_available": round(self._tokens, 1),  # approximate — no lock
            "in_backoff": self._in_backoff,
            "backoff_count": self._backoff_count,
            "consecutive_429s": self._consecutive_429s,
            **self.get_metrics(),
        }
