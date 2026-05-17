"""
Smart proxy manager — fast rotation, with exclude, no waiting.

Proxy-first philosophy:
  - DIFFERENT proxy on each retry (exclude parameter)
  - No waiting — switching proxy resolves the issue immediately
  - Short cooldown by default — disabled proxies return quickly
  - Weighted scoring: success_rate / avg_latency
  - Fallback: all proxies down → direct connection (if enabled)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set

from .models import ProxyStatus

logger = logging.getLogger("instagram_mcp.proxy_manager")


class ProxyCircuitState(str, Enum):
    """Per-proxy circuit breaker state."""
    CLOSED = "closed"        # healthy
    OPEN = "open"            # cooling down — no requests routed here
    HALF_OPEN = "half_open"  # cooldown expired — single probe in flight

_VALID_SCHEMES = ("http://", "https://", "socks5://", "socks4://")


def _validate_proxy_url(url: str) -> None:
    """Validate a proxy URL, raising ValueError with a clear message if invalid."""
    if not any(url.startswith(scheme) for scheme in _VALID_SCHEMES):
        masked = _mask_proxy_url(url)
        raise ValueError(
            f"Invalid proxy URL {masked!r}: must start with one of "
            f"{', '.join(_VALID_SCHEMES)}"
        )
    # Extract the host part after scheme/userinfo
    rest = url.split("://", 1)[1]
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    host = rest.split("/")[0].split(":")[0]
    if not host:
        masked = _mask_proxy_url(url)
        raise ValueError(
            f"Invalid proxy URL {masked!r}: must contain a host after the scheme"
        )
    # Port validation
    if ":" in rest.split("/")[0]:
        port_str = rest.split("/")[0].split(":")[-1]
        if port_str.isdigit():
            port = int(port_str)
            if not (1 <= port <= 65535):
                masked = _mask_proxy_url(url)
                raise ValueError(
                    f"Invalid proxy URL {masked!r}: port {port} must be in range 1-65535"
                )


def _mask_proxy_url(url: str) -> str:
    """Hide user/password from proxy URL."""
    return re.sub(r"//[^@]+@", "//***@", url)


@dataclass(slots=True)
class _ProxyState:
    """Internal state of a single proxy."""
    url: str
    is_active: bool = True
    consecutive_fails: int = 0
    backoff_steps: int = 0          # exponential backoff counter (separate from consecutive_fails)
    total_requests: int = 0
    total_success: int = 0
    total_latency: float = 0.0
    last_fail_time: float = 0.0
    cooldown_until: float = 0.0
    last_used: float = 0.0
    # ── 3-state circuit breaker ─────────────────────────────────────────────
    cb_state: ProxyCircuitState = ProxyCircuitState.CLOSED
    cb_cooldown_seconds: float = 0.0   # current OPEN cooldown duration (doubles each OPEN→HALF→OPEN cycle)
    cb_half_open_in_flight: bool = False  # True while the probe request is running
    # ── Per-proxy bulkhead ──────────────────────────────────────────────────
    active_requests: int = 0
    max_concurrent: int = 30

    @property
    def avg_latency(self) -> float:
        if self.total_success == 0:
            return 999.0
        return self.total_latency / self.total_success

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.5  # neutral starting score
        return self.total_success / self.total_requests

    @property
    def score(self) -> float:
        """Higher is better. Used by `get_best_proxy`."""
        if self.total_requests == 0:
            return 1.0  # untried proxy gets a fair shot
        return max(0.01, (self.success_rate * 100) / (self.avg_latency + 1.0))


class ProxyManager:
    """Smart proxy rotation — always different proxy, no waiting."""

    def __init__(
        self,
        proxy_urls: Optional[List[str]] = None,
        max_fails: int = 5,
        cooldown_seconds: int = 30,
        max_cooldown_seconds: float = 300.0,
        auto_fallback: bool = True,
        health_check_interval: int = 30,
        cb_fail_threshold: int = 3,
        cb_open_cooldown: float = 30.0,
        cb_max_cooldown: float = 300.0,
        max_concurrent: int = 30,
    ) -> None:
        self._proxies: List[_ProxyState] = [
            _ProxyState(url=u, max_concurrent=max_concurrent)
            for u in (proxy_urls or [])
        ]
        # Index by URL for O(1) lookup in report_success/report_failure
        self._by_url: Dict[str, _ProxyState] = {p.url: p for p in self._proxies}
        self._max_fails: int = max_fails
        self._cooldown: int = cooldown_seconds
        self._max_cooldown: float = max_cooldown_seconds
        self._auto_fallback: bool = auto_fallback
        self._health_check_interval: int = health_check_interval
        # Circuit-breaker config
        self._cb_fail_threshold: int = cb_fail_threshold
        self._cb_open_cooldown: float = cb_open_cooldown
        self._cb_max_cooldown: float = cb_max_cooldown
        self._max_concurrent: int = max_concurrent
        # Counters
        self._cb_opens_total: int = 0
        self._cb_half_open_total: int = 0
        self._bulkhead_rejections: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._total_fallbacks: int = 0
        self._health_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)

    # ── Health checking ──────────────────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """Periodically reactivate proxies whose cooldowns expired and grace-reset stale ones."""
        try:
            while True:
                await asyncio.sleep(self._health_check_interval)
                now = time.time()
                async with self._lock:
                    for p in self._proxies:
                        # Move OPEN → HALF_OPEN once cooldown expires
                        if (
                            p.cb_state is ProxyCircuitState.OPEN
                            and now >= p.cooldown_until
                        ):
                            p.cb_state = ProxyCircuitState.HALF_OPEN
                            p.cb_half_open_in_flight = False
                            self._cb_half_open_total += 1
                            logger.info(
                                "Circuit breaker HALF_OPEN: %s (probe allowed)",
                                _mask_proxy_url(p.url),
                            )

                        if not p.is_active and now > p.cooldown_until:
                            p.is_active = True
                            p.consecutive_fails = 0
                            p.backoff_steps = 0
                            logger.info(
                                "Health check: proxy reactivated: %s",
                                _mask_proxy_url(p.url),
                            )
                        elif (
                            p.is_active
                            and (now - p.last_used) >= self._health_check_interval
                            and (now - p.last_fail_time) >= self._health_check_interval
                            and p.consecutive_fails > 0
                        ):
                            # Idle proxy: forgive past failures so it gets a fresh shot
                            p.consecutive_fails = 0
                            p.backoff_steps = max(0, p.backoff_steps - 1)
                            logger.debug(
                                "Health check grace period applied: %s",
                                _mask_proxy_url(p.url),
                            )
        except asyncio.CancelledError:
            logger.debug("Proxy health check loop cancelled")

    def start_health_checks(self) -> None:
        """Start the background health check task (idempotent)."""
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_check_loop())
            logger.info(
                "Proxy health check loop started (interval=%ds)",
                self._health_check_interval,
            )

    async def stop_health_checks(self) -> None:
        """Cancel the background health check task (graceful shutdown)."""
        task = self._health_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self._health_task = None

    # ── Internal: shared selection routine ───────────────────────────────────

    def _reactivate_expired(self, now: float) -> None:
        """Re-enable proxies whose cooldown has expired. Caller must hold _lock."""
        for p in self._proxies:
            # CB transition OPEN → HALF_OPEN when cooldown expires
            if (
                p.cb_state is ProxyCircuitState.OPEN
                and now >= p.cooldown_until
            ):
                p.cb_state = ProxyCircuitState.HALF_OPEN
                p.cb_half_open_in_flight = False
                self._cb_half_open_total += 1
                logger.info(
                    "Circuit breaker HALF_OPEN: %s",
                    _mask_proxy_url(p.url),
                )
            if not p.is_active and now > p.cooldown_until:
                p.is_active = True
                p.consecutive_fails = 0
                p.backoff_steps = 0
                logger.info("Proxy reactivated: %s", _mask_proxy_url(p.url))

    # ── Proxy selection ──────────────────────────────────────────────────────

    async def get_best_proxy(
        self, exclude: Optional[Set[str]] = None
    ) -> Optional[str]:
        """
        Return the highest-scoring proxy not in *exclude*.

        Score formula: (success_rate * 100) / (avg_latency + 1)
        Untried proxies get a neutral starting score of 1.0.

        Selection rules (3-state circuit breaker + bulkhead):
          - CLOSED proxies: always selectable (subject to bulkhead).
          - HALF_OPEN proxies: only one probe allowed at a time.
          - OPEN proxies: skipped until cooldown expires.
          - Bulkhead: skip proxies whose active_requests >= max_concurrent.

        Returns None when:
          - No proxies are configured (always direct), or
          - All active proxies are excluded AND auto_fallback is enabled, or
          - All proxies are in cooldown / saturated.
        """
        if not self._proxies:
            return None

        excl = exclude or set()

        async with self._lock:
            now = time.time()
            self._reactivate_expired(now)

            def _is_selectable(p: _ProxyState) -> bool:
                if not p.is_active:
                    return False
                if p.cb_state is ProxyCircuitState.OPEN:
                    return False
                if p.cb_state is ProxyCircuitState.HALF_OPEN and p.cb_half_open_in_flight:
                    return False
                if p.active_requests >= p.max_concurrent:
                    return False
                return True

            available = [
                p for p in self._proxies
                if _is_selectable(p) and p.url not in excl
            ]

            # If exclude exhausted the pool, fall back to any selectable proxy
            if not available:
                available = [p for p in self._proxies if _is_selectable(p)]

            if not available:
                # Track bulkhead-only rejections (selection blocked but proxies exist+active)
                if any(
                    p.is_active
                    and p.cb_state is not ProxyCircuitState.OPEN
                    and p.active_requests >= p.max_concurrent
                    for p in self._proxies
                ):
                    self._bulkhead_rejections += 1
                if self._auto_fallback:
                    self._total_fallbacks += 1
                    logger.warning(
                        "All proxies in cooldown — direct connection (fallback #%d)",
                        self._total_fallbacks,
                    )
                return None

            best = max(available, key=lambda p: p.score)
            best.last_used = now
            # Increment bulkhead counter; flag HALF_OPEN probe in flight
            best.active_requests += 1
            if best.cb_state is ProxyCircuitState.HALF_OPEN:
                best.cb_half_open_in_flight = True
            return best.url

    # Backwards-compatible alias
    get_proxy = get_best_proxy

    # ── Reporting ────────────────────────────────────────────────────────────

    async def report_success(self, proxy_url: str, latency: float) -> None:
        """Successful request — reset backoff counters; close circuit if it was half-open."""
        async with self._lock:
            p = self._by_url.get(proxy_url)
            if p is None:
                return
            # Release bulkhead slot
            if p.active_requests > 0:
                p.active_requests -= 1
            p.consecutive_fails = 0
            p.backoff_steps = 0
            p.total_requests += 1
            p.total_success += 1
            p.total_latency += latency

            # 3-state CB: HALF_OPEN probe succeeded → CLOSED, reset cooldown
            if p.cb_state is ProxyCircuitState.HALF_OPEN:
                p.cb_state = ProxyCircuitState.CLOSED
                p.cb_half_open_in_flight = False
                p.cb_cooldown_seconds = 0.0
                logger.info(
                    "Circuit breaker CLOSED (probe succeeded): %s",
                    _mask_proxy_url(p.url),
                )

    async def report_failure(self, proxy_url: str, error: str = "") -> None:
        """
        Failed request — drive the 3-state circuit breaker and exp-backoff cooldown.

        State transitions:
          - CLOSED + N consecutive fails (≥ cb_fail_threshold) → OPEN (cooldown)
          - HALF_OPEN probe fails → OPEN again, cooldown doubled (cap = cb_max_cooldown)
        """
        async with self._lock:
            p = self._by_url.get(proxy_url)
            if p is None:
                return
            # Release bulkhead slot
            if p.active_requests > 0:
                p.active_requests -= 1
            p.consecutive_fails += 1
            p.total_requests += 1
            now = time.time()
            p.last_fail_time = now

            # ── 3-state circuit breaker logic ───────────────────────────────
            if p.cb_state is ProxyCircuitState.HALF_OPEN:
                # Probe failed → OPEN with doubled cooldown
                p.cb_cooldown_seconds = min(
                    max(p.cb_cooldown_seconds, self._cb_open_cooldown) * 2.0,
                    self._cb_max_cooldown,
                )
                p.cb_state = ProxyCircuitState.OPEN
                p.cb_half_open_in_flight = False
                p.cooldown_until = now + p.cb_cooldown_seconds
                p.is_active = False
                self._cb_opens_total += 1
                logger.warning(
                    "Circuit breaker re-OPEN (probe failed): %s — cooldown %.0fs",
                    _mask_proxy_url(p.url),
                    p.cb_cooldown_seconds,
                )
            elif (
                p.cb_state is ProxyCircuitState.CLOSED
                and p.consecutive_fails >= self._cb_fail_threshold
            ):
                # Trip from CLOSED → OPEN
                p.cb_cooldown_seconds = self._cb_open_cooldown
                p.cb_state = ProxyCircuitState.OPEN
                p.cooldown_until = now + p.cb_cooldown_seconds
                p.is_active = False
                self._cb_opens_total += 1
                logger.warning(
                    "Circuit breaker OPEN (%dx consecutive fails): %s — cooldown %.0fs",
                    p.consecutive_fails,
                    _mask_proxy_url(p.url),
                    p.cb_cooldown_seconds,
                )

            # ── Legacy exp-backoff disablement (independent threshold) ──────
            if p.consecutive_fails >= self._max_fails:
                p.backoff_steps = min(p.backoff_steps + 1, 8)
                # Exponential backoff capped at max_cooldown
                backoff = min(
                    self._cooldown * (2 ** p.backoff_steps),
                    self._max_cooldown,
                )
                p.is_active = False
                # Keep the larger of the two cooldowns
                p.cooldown_until = max(p.cooldown_until, time.time() + backoff)
                logger.warning(
                    "Proxy disabled (%dx fails): %s — cooldown %.0fs",
                    p.consecutive_fails,
                    _mask_proxy_url(p.url),
                    backoff,
                )

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def add_proxy(self, url: str) -> bool:
        """Add new proxy (async-safe)."""
        url = url.strip()
        if not url:
            return False
        _validate_proxy_url(url)
        async with self._lock:
            if url in self._by_url:
                return False
            state = _ProxyState(url=url)
            self._proxies.append(state)
            self._by_url[url] = state
        logger.info("New proxy added: %s", _mask_proxy_url(url))
        return True

    async def remove_proxy(self, url: str) -> bool:
        """Remove proxy (async-safe)."""
        url = url.strip()
        async with self._lock:
            state = self._by_url.pop(url, None)
            if state is None:
                return False
            self._proxies.remove(state)
            logger.info("Proxy removed: %s", _mask_proxy_url(url))
            return True

    async def release_proxy(self, proxy_url: str) -> None:
        """Release a previously-acquired proxy slot without recording success/failure.

        Used when an attempt is aborted before completion (e.g. fatal error
        before a status code is known). Idempotent / safe on unknown URLs.
        """
        async with self._lock:
            p = self._by_url.get(proxy_url)
            if p is None:
                return
            if p.active_requests > 0:
                p.active_requests -= 1
            if p.cb_state is ProxyCircuitState.HALF_OPEN:
                p.cb_half_open_in_flight = False

    async def reset_all(self) -> None:
        """Reset all proxy state."""
        async with self._lock:
            for p in self._proxies:
                p.is_active = True
                p.consecutive_fails = 0
                p.backoff_steps = 0
                p.total_requests = 0
                p.total_success = 0
                p.total_latency = 0.0
                p.last_fail_time = 0.0
                p.cooldown_until = 0.0
                p.cb_state = ProxyCircuitState.CLOSED
                p.cb_cooldown_seconds = 0.0
                p.cb_half_open_in_flight = False
                p.active_requests = 0

    # ── Status / diagnostics ─────────────────────────────────────────────────

    async def get_all_status(self) -> List[ProxyStatus]:
        """Per-proxy status snapshot."""
        async with self._lock:
            now = time.time()
            return [
                ProxyStatus(
                    url_masked=_mask_proxy_url(p.url),
                    is_active=p.is_active,
                    consecutive_fails=p.consecutive_fails,
                    total_requests=p.total_requests,
                    total_success=p.total_success,
                    success_rate=round(p.success_rate, 3),
                    avg_latency_ms=round(p.avg_latency * 1000, 1),
                    cooldown_remaining_s=(
                        max(0, int(p.cooldown_until - now)) if not p.is_active else 0
                    ),
                )
                for p in self._proxies
            ]

    @property
    def stats(self) -> Dict[str, object]:
        """Approximate snapshot — no lock, for diagnostics only."""
        active = sum(1 for p in self._proxies if p.is_active)
        cb_open = sum(1 for p in self._proxies if p.cb_state is ProxyCircuitState.OPEN)
        cb_half_open = sum(
            1 for p in self._proxies if p.cb_state is ProxyCircuitState.HALF_OPEN
        )
        cb_closed = sum(
            1 for p in self._proxies if p.cb_state is ProxyCircuitState.CLOSED
        )
        return {
            "total_proxies": len(self._proxies),
            "active_proxies": active,
            "disabled_proxies": len(self._proxies) - active,
            "total_fallbacks": self._total_fallbacks,
            "auto_fallback_enabled": self._auto_fallback,
            # 3-state circuit breaker
            "cb_closed": cb_closed,
            "cb_open": cb_open,
            "cb_half_open": cb_half_open,
            "cb_opens_total": self._cb_opens_total,
            "cb_half_open_total": self._cb_half_open_total,
            "bulkhead_rejections": self._bulkhead_rejections,
        }
