"""
Smart TTL cache — coroutine-safe, LRU eviction, with diagnostics.

Features:
  - TTL-based: each entry has its own expiration time
  - LRU eviction: oldest evicted when max entries reached
  - Coroutine-safe: via asyncio.Lock
  - Single-flight: coalesces concurrent fetches for the same key
  - Stats: hit rate, miss rate, eviction count, coalesce count
  - Can be disabled: enabled=False
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from .models import CacheStats

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: float
    created_at: float


class SmartCache:
    """Coroutine-safe TTL cache with LRU eviction + single-flight."""

    __slots__ = (
        "_store", "_lock", "_max_entries", "_default_ttl", "_enabled",
        "_inflight",
        "_hits", "_misses", "_evictions", "_coalesced",
    )

    def __init__(self, max_entries: int = 500, enabled: bool = True, default_ttl: int = 300):
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._enabled = enabled
        # Single-flight: in-flight futures keyed by cache key
        self._inflight: Dict[str, "asyncio.Future[Any]"] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._coalesced = 0

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache. Returns None on miss."""
        if not self._enabled:
            return None

        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            if time.time() > entry.expires_at:
                del self._store[key]
                self._misses += 1
                logger.debug("Cache evicted: %s (reason: ttl_expired)", key)
                return None

            # LRU bump
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Write to cache. TTL in seconds."""
        if not self._enabled:
            return

        async with self._lock:
            now = time.time()
            if key in self._store:
                del self._store[key]

            while len(self._store) >= self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                self._evictions += 1
                logger.debug("Cache evicted: %s (reason: lru)", evicted_key)

            self._store[key] = _CacheEntry(
                value=value,
                expires_at=now + ttl,
                created_at=now,
            )

    async def invalidate(self, key: str) -> bool:
        if not self._enabled:
            return False
        async with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    async def invalidate_prefix(self, prefix: str) -> int:
        if not self._enabled:
            return 0
        async with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    async def clear(self) -> int:
        async with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    async def stats(self) -> CacheStats:
        async with self._lock:
            hits = self._hits
            misses = self._misses
            evictions = self._evictions
            total_entries = len(self._store)

        total = hits + misses
        return CacheStats(
            enabled=self._enabled,
            total_entries=total_entries,
            max_entries=self._max_entries,
            hits=hits,
            misses=misses,
            evictions=evictions,
            hit_rate=round(hits / total, 3) if total > 0 else 0.0,
        )

    def stats_sync(self) -> CacheStats:
        """Non-blocking snapshot — values may be slightly inconsistent.

        Note: all values are approximate; reads are performed without holding
        the lock, so counters and entry counts may not reflect a consistent
        point-in-time state.
        """
        hits = self._hits
        misses = self._misses
        total = hits + misses
        return CacheStats(
            enabled=self._enabled,
            total_entries=len(self._store),
            max_entries=self._max_entries,
            hits=hits,
            misses=misses,
            evictions=self._evictions,
            hit_rate=round(hits / total, 3) if total > 0 else 0.0,
        )

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def coalesced(self) -> int:
        """Number of fetches that were coalesced via single-flight."""
        return self._coalesced

    # ────────────────────────────────────────────────────────────────────────
    # Single-flight: coalesce concurrent fetches for the same key
    # ────────────────────────────────────────────────────────────────────────

    async def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], Awaitable[Any]],
        ttl: Optional[int] = None,
    ) -> Any:
        """
        Cache lookup with single-flight semantics.

        If the key is already cached (and unexpired), returns the cached value.
        If a fetch is already in progress for this key, waits on that fetch
        instead of issuing a duplicate request. Otherwise issues the fetch,
        stores the result, and returns it.

        `fetch_fn` must be a zero-arg coroutine function.
        """
        if not self._enabled:
            return await fetch_fn()

        cached = await self.get(key)
        if cached is not None:
            return cached

        # Check / register inflight under the lock to avoid lost updates
        is_owner = False
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                self._coalesced += 1
                fut = existing
            else:
                fut = asyncio.get_running_loop().create_future()
                self._inflight[key] = fut
                is_owner = True

        if not is_owner:
            return await fut  # follower: wait for the in-flight fetch

        # Owner: perform the fetch outside the lock
        try:
            value = await fetch_fn()
        except BaseException as exc:
            async with self._lock:
                self._inflight.pop(key, None)
            if not fut.done():
                fut.set_exception(exc)
            raise

        if value is not None:
            effective_ttl = int(ttl) if ttl is not None else self._default_ttl
            await self.set(key, value, effective_ttl)

        if not fut.done():
            fut.set_result(value)
        async with self._lock:
            self._inflight.pop(key, None)
        return value

    async def warm(
        self,
        key: str,
        fetch_fn: Callable,
        ttl: Optional[int] = None,
    ) -> Any:
        """Pre-populate the cache for *key*. Sync or async fetch_fn supported."""
        cached = await self.get(key)
        if cached is not None:
            return cached

        if inspect.iscoroutinefunction(fetch_fn):
            value = await fetch_fn()
        else:
            value = fetch_fn()

        effective_ttl = int(ttl) if ttl is not None else self._default_ttl
        await self.set(key, value, effective_ttl)
        return value

    async def cleanup_expired(self) -> int:
        async with self._lock:
            now = time.time()
            expired = [k for k, v in self._store.items() if now > v.expires_at]
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug("Cache cleanup: removed %d expired entries", len(expired))
            return len(expired)
