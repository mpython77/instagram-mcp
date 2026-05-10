import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from instagram_mcp.cache import SmartCache, _CacheEntry
from instagram_mcp.models import CacheStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cache(max_entries=500, enabled=True, default_ttl=300) -> SmartCache:
    """Return a fresh SmartCache with the given parameters."""
    return SmartCache(max_entries=max_entries, enabled=enabled, default_ttl=default_ttl)


# ---------------------------------------------------------------------------
# get() — basic cases
# ---------------------------------------------------------------------------

async def test_get_empty_returns_none():
    cache = make_cache()
    result = await cache.get("missing_key")
    assert result is None


async def test_get_expired_returns_none_and_removes_entry():
    cache = make_cache()
    # Store with very short TTL in the past
    async with cache._lock:
        cache._store["old_key"] = _CacheEntry(
            value="stale",
            expires_at=time.time() - 1.0,   # already expired
            created_at=time.time() - 2.0,
        )
    result = await cache.get("old_key")
    assert result is None
    # Entry must have been removed
    assert "old_key" not in cache._store


async def test_get_valid_entry_returns_value():
    cache = make_cache()
    await cache.set("key1", "value1", ttl=60)
    result = await cache.get("key1")
    assert result == "value1"


async def test_get_valid_entry_bumps_lru_order():
    """After get(), the accessed entry should be at the end (most-recently used)."""
    cache = make_cache(max_entries=3)
    await cache.set("a", 1, ttl=60)
    await cache.set("b", 2, ttl=60)
    await cache.set("c", 3, ttl=60)

    # Access "a" so it moves to the end
    await cache.get("a")

    keys = list(cache._store.keys())
    assert keys[-1] == "a"   # "a" is now the most-recently-used entry


async def test_get_increments_hits():
    cache = make_cache()
    await cache.set("k", "v", ttl=60)
    assert cache._hits == 0
    await cache.get("k")
    assert cache._hits == 1


async def test_get_increments_misses_on_miss():
    cache = make_cache()
    await cache.get("no_such_key")
    assert cache._misses == 1


async def test_get_increments_misses_on_expired():
    cache = make_cache()
    async with cache._lock:
        cache._store["x"] = _CacheEntry(
            value="old", expires_at=time.time() - 1, created_at=time.time() - 2
        )
    await cache.get("x")
    assert cache._misses == 1


async def test_get_disabled_returns_none():
    cache = make_cache(enabled=False)
    # Even if we manually insert something, get() returns None when disabled
    cache._store["k"] = _CacheEntry(value="v", expires_at=time.time() + 100, created_at=time.time())
    result = await cache.get("k")
    assert result is None


# ---------------------------------------------------------------------------
# set() — storing values
# ---------------------------------------------------------------------------

async def test_set_stores_value_with_correct_ttl():
    cache = make_cache()
    before = time.time()
    await cache.set("mykey", "myval", ttl=120)
    entry = cache._store["mykey"]
    assert entry.value == "myval"
    assert entry.expires_at >= before + 119   # allow a tiny clock delta
    assert entry.expires_at <= before + 121


async def test_set_disabled_does_not_store():
    cache = make_cache(enabled=False)
    await cache.set("k", "v", ttl=60)
    assert "k" not in cache._store


async def test_set_full_cache_evicts_lru():
    cache = make_cache(max_entries=2)
    await cache.set("first", 1, ttl=60)
    await cache.set("second", 2, ttl=60)
    # Cache is now full; adding a third entry should evict "first"
    await cache.set("third", 3, ttl=60)
    assert "first" not in cache._store
    assert "second" in cache._store
    assert "third" in cache._store
    assert cache._evictions == 1


async def test_set_full_cache_multiple_evictions():
    cache = make_cache(max_entries=1)
    await cache.set("a", 1, ttl=60)
    await cache.set("b", 2, ttl=60)   # evicts "a"
    await cache.set("c", 3, ttl=60)   # evicts "b"
    assert cache._evictions == 2
    assert list(cache._store.keys()) == ["c"]


async def test_set_updating_existing_key_removes_old_first():
    """Updating an existing key must delete the old entry before inserting the new one."""
    cache = make_cache(max_entries=2)
    await cache.set("a", 1, ttl=60)
    await cache.set("b", 2, ttl=60)
    # Updating "a" must NOT trigger an eviction (it removes and re-adds "a")
    await cache.set("a", 99, ttl=60)
    assert cache._evictions == 0
    assert cache._store["a"].value == 99
    assert "b" in cache._store


# ---------------------------------------------------------------------------
# invalidate()
# ---------------------------------------------------------------------------

async def test_invalidate_returns_true_on_hit():
    cache = make_cache()
    await cache.set("key", "val", ttl=60)
    result = await cache.invalidate("key")
    assert result is True
    assert "key" not in cache._store


async def test_invalidate_returns_false_on_miss():
    cache = make_cache()
    result = await cache.invalidate("non_existent")
    assert result is False


async def test_invalidate_disabled_returns_false():
    cache = make_cache(enabled=False)
    # Even if something is manually in the store, disabled invalidate() → False
    cache._store["k"] = _CacheEntry(value="v", expires_at=time.time() + 60, created_at=time.time())
    result = await cache.invalidate("k")
    assert result is False


# ---------------------------------------------------------------------------
# invalidate_prefix()
# ---------------------------------------------------------------------------

async def test_invalidate_prefix_removes_matching_keys():
    cache = make_cache()
    await cache.set("user:1:profile", "p1", ttl=60)
    await cache.set("user:2:profile", "p2", ttl=60)
    await cache.set("post:100", "post_data", ttl=60)

    count = await cache.invalidate_prefix("user:")
    assert count == 2
    assert "user:1:profile" not in cache._store
    assert "user:2:profile" not in cache._store
    assert "post:100" in cache._store


async def test_invalidate_prefix_no_match_returns_zero():
    cache = make_cache()
    await cache.set("other:key", "v", ttl=60)
    count = await cache.invalidate_prefix("user:")
    assert count == 0


async def test_invalidate_prefix_disabled_returns_zero():
    cache = make_cache(enabled=False)
    count = await cache.invalidate_prefix("anything:")
    assert count == 0


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

async def test_clear_removes_all_entries_and_returns_count():
    cache = make_cache()
    await cache.set("a", 1, ttl=60)
    await cache.set("b", 2, ttl=60)
    await cache.set("c", 3, ttl=60)
    count = await cache.clear()
    assert count == 3
    assert len(cache._store) == 0


async def test_clear_empty_cache_returns_zero():
    cache = make_cache()
    count = await cache.clear()
    assert count == 0


# ---------------------------------------------------------------------------
# stats() and stats_sync()
# ---------------------------------------------------------------------------

async def test_stats_hit_rate_zero_when_no_requests():
    cache = make_cache()
    s = await cache.stats()
    assert isinstance(s, CacheStats)
    assert s.hit_rate == 0.0
    assert s.hits == 0
    assert s.misses == 0


async def test_stats_correct_values():
    cache = make_cache()
    await cache.set("k1", "v", ttl=60)
    await cache.set("k2", "v", ttl=60)
    await cache.get("k1")   # hit
    await cache.get("k1")   # hit
    await cache.get("miss") # miss

    s = await cache.stats()
    assert s.hits == 2
    assert s.misses == 1
    assert s.hit_rate == round(2 / 3, 3)
    assert s.total_entries == 2
    assert s.max_entries == 500
    assert s.enabled is True


async def test_stats_evictions():
    cache = make_cache(max_entries=1)
    await cache.set("a", 1, ttl=60)
    await cache.set("b", 2, ttl=60)   # triggers eviction
    s = await cache.stats()
    assert s.evictions == 1


async def test_stats_sync_returns_approximate_stats():
    cache = make_cache()
    await cache.set("x", 42, ttl=60)
    await cache.get("x")      # hit
    await cache.get("y")      # miss

    s = cache.stats_sync()
    assert isinstance(s, CacheStats)
    assert s.hits == 1
    assert s.misses == 1
    assert s.total_entries == 1
    assert s.hit_rate == 0.5


async def test_stats_sync_no_requests_zero_hit_rate():
    cache = make_cache()
    s = cache.stats_sync()
    assert s.hit_rate == 0.0


# ---------------------------------------------------------------------------
# hit_rate property
# ---------------------------------------------------------------------------

async def test_hit_rate_zero_on_empty():
    cache = make_cache()
    assert cache.hit_rate == 0.0


async def test_hit_rate_calculated_correctly():
    cache = make_cache()
    await cache.set("k", "v", ttl=60)
    await cache.get("k")    # hit
    await cache.get("k")    # hit
    await cache.get("no")   # miss
    assert cache.hit_rate == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# coalesced property
# ---------------------------------------------------------------------------

async def test_coalesced_property_initial_zero():
    cache = make_cache()
    assert cache.coalesced == 0


async def test_coalesced_property_after_single_flight():
    """After concurrent get_or_fetch calls coalesce, coalesced should be > 0."""
    cache = make_cache()
    event = asyncio.Event()

    async def slow_fetch():
        await event.wait()
        return "result"

    # Start two concurrent fetches — second should coalesce onto first
    t1 = asyncio.create_task(cache.get_or_fetch("k", slow_fetch))
    # Give the event loop a tick so t1 registers in _inflight
    await asyncio.sleep(0)
    t2 = asyncio.create_task(cache.get_or_fetch("k", slow_fetch))
    await asyncio.sleep(0)

    event.set()
    results = await asyncio.gather(t1, t2)
    assert results == ["result", "result"]
    assert cache.coalesced == 1


# ---------------------------------------------------------------------------
# get_or_fetch()
# ---------------------------------------------------------------------------

async def test_get_or_fetch_disabled_calls_fetch_every_time():
    cache = make_cache(enabled=False)
    call_count = 0

    async def fetch_fn():
        nonlocal call_count
        call_count += 1
        return "data"

    r1 = await cache.get_or_fetch("k", fetch_fn)
    r2 = await cache.get_or_fetch("k", fetch_fn)
    assert r1 == "data"
    assert r2 == "data"
    assert call_count == 2


async def test_get_or_fetch_returns_cached_value_on_second_call():
    cache = make_cache()
    call_count = 0

    async def fetch_fn():
        nonlocal call_count
        call_count += 1
        return "fetched"

    r1 = await cache.get_or_fetch("k", fetch_fn, ttl=60)
    r2 = await cache.get_or_fetch("k", fetch_fn, ttl=60)
    assert r1 == "fetched"
    assert r2 == "fetched"
    assert call_count == 1   # fetch_fn was called only once


async def test_get_or_fetch_uses_default_ttl_when_ttl_none():
    cache = make_cache(default_ttl=999)
    await cache.get_or_fetch("k", AsyncMock(return_value="v"))
    entry = cache._store["k"]
    # expires_at should be approximately now + 999
    assert entry.expires_at >= time.time() + 990


async def test_get_or_fetch_uses_explicit_ttl():
    cache = make_cache(default_ttl=300)
    await cache.get_or_fetch("k", AsyncMock(return_value="v"), ttl=42)
    entry = cache._store["k"]
    assert entry.expires_at <= time.time() + 43
    assert entry.expires_at >= time.time() + 40


async def test_get_or_fetch_single_flight_coalesces_concurrent_calls():
    """Multiple concurrent calls for the same key must issue only one fetch."""
    cache = make_cache()
    fetch_count = 0
    event = asyncio.Event()

    async def slow_fetch():
        nonlocal fetch_count
        fetch_count += 1
        await event.wait()
        return "shared_result"

    # Launch 5 concurrent fetches for the same key
    tasks = [asyncio.create_task(cache.get_or_fetch("k", slow_fetch)) for _ in range(5)]
    # Yield control so all tasks enter get_or_fetch
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    event.set()
    results = await asyncio.gather(*tasks)

    assert fetch_count == 1        # Only one real fetch happened
    assert all(r == "shared_result" for r in results)
    assert cache.coalesced == 4    # 4 followers coalesced onto the owner


async def test_get_or_fetch_exception_propagates_to_all_waiters():
    """If fetch_fn raises, all waiting coroutines receive the same exception."""
    cache = make_cache()
    event = asyncio.Event()

    async def failing_fetch():
        await event.wait()
        raise ValueError("fetch failed")

    tasks = [asyncio.create_task(cache.get_or_fetch("k", failing_fetch)) for _ in range(3)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    event.set()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(r, (ValueError, BaseException)) for r in results)
    # The inflight entry must have been cleaned up
    assert "k" not in cache._inflight


async def test_get_or_fetch_does_not_cache_none_values():
    """When fetch_fn returns None, the result is NOT stored in the cache."""
    cache = make_cache()

    async def fetch_none():
        return None

    result = await cache.get_or_fetch("k", fetch_none, ttl=60)
    assert result is None
    # None must NOT be in the store (so next call triggers another fetch)
    assert "k" not in cache._store


async def test_get_or_fetch_none_not_cached_triggers_second_fetch():
    """Because None is not cached, a second get_or_fetch must call fetch_fn again."""
    cache = make_cache()
    call_count = 0

    async def fetch_none():
        nonlocal call_count
        call_count += 1
        return None

    await cache.get_or_fetch("k", fetch_none)
    await cache.get_or_fetch("k", fetch_none)
    assert call_count == 2


# ---------------------------------------------------------------------------
# warm()
# ---------------------------------------------------------------------------

async def test_warm_with_sync_function_populates_cache():
    cache = make_cache()

    def sync_fetch():
        return "sync_value"

    result = await cache.warm("k", sync_fetch, ttl=60)
    assert result == "sync_value"
    assert cache._store["k"].value == "sync_value"


async def test_warm_with_async_function_populates_cache():
    cache = make_cache()

    async def async_fetch():
        return "async_value"

    result = await cache.warm("k", async_fetch, ttl=60)
    assert result == "async_value"
    assert cache._store["k"].value == "async_value"


async def test_warm_returns_cached_value_if_already_present():
    cache = make_cache()
    await cache.set("k", "cached", ttl=60)

    call_count = 0

    async def new_fetch():
        nonlocal call_count
        call_count += 1
        return "new_value"

    result = await cache.warm("k", new_fetch, ttl=60)
    assert result == "cached"   # returns the existing value
    assert call_count == 0      # fetch_fn was never called


async def test_warm_uses_default_ttl_when_not_specified():
    cache = make_cache(default_ttl=123)

    def fetch():
        return "v"

    await cache.warm("k", fetch)
    entry = cache._store["k"]
    assert entry.expires_at >= time.time() + 120


async def test_warm_sync_populates_with_explicit_ttl():
    cache = make_cache()

    def fetch():
        return "hello"

    await cache.warm("k", fetch, ttl=500)
    entry = cache._store["k"]
    assert entry.expires_at >= time.time() + 490


# ---------------------------------------------------------------------------
# cleanup_expired()
# ---------------------------------------------------------------------------

async def test_cleanup_expired_removes_only_expired_entries():
    cache = make_cache()
    # Insert one expired and one valid entry directly
    async with cache._lock:
        cache._store["expired"] = _CacheEntry(
            value="old", expires_at=time.time() - 1, created_at=time.time() - 2
        )
        cache._store["valid"] = _CacheEntry(
            value="new", expires_at=time.time() + 100, created_at=time.time()
        )

    removed = await cache.cleanup_expired()
    assert removed == 1
    assert "expired" not in cache._store
    assert "valid" in cache._store


async def test_cleanup_expired_returns_zero_when_nothing_expired():
    cache = make_cache()
    await cache.set("a", 1, ttl=60)
    await cache.set("b", 2, ttl=60)
    removed = await cache.cleanup_expired()
    assert removed == 0
    assert len(cache._store) == 2


async def test_cleanup_expired_removes_multiple_expired():
    cache = make_cache()
    async with cache._lock:
        for i in range(5):
            cache._store[f"exp_{i}"] = _CacheEntry(
                value=i, expires_at=time.time() - 1, created_at=time.time() - 2
            )
        cache._store["keep"] = _CacheEntry(
            value="x", expires_at=time.time() + 60, created_at=time.time()
        )
    removed = await cache.cleanup_expired()
    assert removed == 5
    assert "keep" in cache._store


async def test_cleanup_expired_empty_cache_returns_zero():
    cache = make_cache()
    removed = await cache.cleanup_expired()
    assert removed == 0


# ---------------------------------------------------------------------------
# Concurrency / stress
# ---------------------------------------------------------------------------

async def test_concurrent_set_and_get_are_safe():
    """No assertion errors or lost updates under concurrent writes."""
    cache = make_cache(max_entries=10)

    async def writer(i):
        await cache.set(f"key_{i}", i, ttl=60)

    async def reader(i):
        return await cache.get(f"key_{i}")

    writers = [asyncio.create_task(writer(i)) for i in range(20)]
    await asyncio.gather(*writers)

    readers = [asyncio.create_task(reader(i)) for i in range(20)]
    results = await asyncio.gather(*readers)
    # All results are either the stored int or None (evicted)
    for r in results:
        assert r is None or isinstance(r, int)
