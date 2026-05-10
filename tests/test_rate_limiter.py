import asyncio
import pytest
from instagram_mcp.rate_limiter import AdaptiveRateLimiter
import time

@pytest.mark.asyncio
async def test_acquire():
    limiter = AdaptiveRateLimiter(rate=10.0, burst=5, request_jitter=0.0)
    # Should acquire tokens immediately
    t0 = time.monotonic()
    waited = await limiter.acquire()
    assert waited == 0.0
    
    # Deplete tokens
    for _ in range(4):
        await limiter.acquire()

    # Next acquire should wait
    t1 = time.monotonic()
    waited = await limiter.acquire()
    assert waited > 0.0

@pytest.mark.asyncio
async def test_on_rate_limited_and_success():
    limiter = AdaptiveRateLimiter(rate=10.0, backoff_factor=0.5, recovery_factor=2.0)
    assert limiter.current_rate == 10.0
    
    await limiter.on_rate_limited()
    assert limiter.current_rate == 5.0
    assert limiter.stats["consecutive_429s"] == 1
    
    await limiter.on_success()
    # It recovers but doesn't exceed max_rate (2.5 * base by default)
    # Here, 5.0 * 2.0 = 10.0
    assert limiter.current_rate == 10.0
    assert limiter.stats["consecutive_429s"] == 0

@pytest.mark.asyncio
async def test_circuit_breaker():
    limiter = AdaptiveRateLimiter(
        rate=10.0, 
        circuit_breaker_threshold=2, 
        circuit_breaker_cooldown=0.1,
        min_rate=1.0
    )
    
    assert not limiter.is_circuit_open
    
    await limiter.on_rate_limited()
    assert not limiter.is_circuit_open
    
    t0 = time.monotonic()
    await limiter.on_rate_limited()
    t1 = time.monotonic()
    
    # It should have slept for ~0.1s
    assert t1 - t0 >= 0.1
    # is_circuit_open is immediately false because _consecutive_429s is reset to 0 in on_rate_limited
    assert not limiter.is_circuit_open

    # Check max_rate is halved (originally 25.0)
    assert limiter.stats["max_rps"] == 12.5

    # explicitly test is_circuit_open property
    limiter._consecutive_429s = 5
    assert limiter.is_circuit_open

@pytest.mark.asyncio
async def test_max_rate_restoration():
    limiter = AdaptiveRateLimiter(rate=10.0)
    # Force max_rate down
    limiter._max_rate = 5.0
    limiter._consecutive_successes = 19
    
    await limiter.on_success()
    # Now it hits 20 successes, max_rate should bump
    assert limiter.stats["max_rps"] == 7.5

@pytest.mark.asyncio
async def test_metrics_and_stats():
    limiter = AdaptiveRateLimiter(rate=10.0, burst=1)
    await limiter.acquire() # consumes token
    metrics = limiter.get_metrics()
    assert metrics["total_requests"] == 1
    assert metrics["total_waits"] == 0
    
    stats = limiter.stats
    assert stats["current_rps"] == 10.0
    assert stats["burst"] == 1
