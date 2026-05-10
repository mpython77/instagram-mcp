import asyncio
import time
import pytest
from unittest.mock import MagicMock
from instagram_mcp.proxy_manager import ProxyManager, _validate_proxy_url, _mask_proxy_url, _ProxyState

def test_validate_proxy_url():
    _validate_proxy_url("http://localhost:8080")
    _validate_proxy_url("https://user:pass@localhost:8080")
    _validate_proxy_url("socks5://user:pass@proxy.com")
    
    with pytest.raises(ValueError, match="must start with one of"):
        _validate_proxy_url("ftp://localhost")
    
    with pytest.raises(ValueError, match="must contain a host"):
        _validate_proxy_url("http://")
        
    with pytest.raises(ValueError, match="port .* must be in range"):
        _validate_proxy_url("http://localhost:99999")

def test_mask_proxy_url():
    assert _mask_proxy_url("http://user:pass@localhost:8080") == "http://***@localhost:8080"
    assert _mask_proxy_url("http://localhost:8080") == "http://localhost:8080"

def test_proxy_state_properties():
    ps = _ProxyState(url="http://p")
    assert ps.avg_latency == 999.0
    assert ps.success_rate == 0.5
    assert ps.score == 1.0
    
    ps.total_requests = 10
    ps.total_success = 8
    ps.total_latency = 4.0
    assert ps.avg_latency == 0.5
    assert ps.success_rate == 0.8
    assert abs(ps.score - 53.333) < 0.01

@pytest.mark.asyncio
async def test_proxy_manager_basic():
    pm = ProxyManager(["http://p1", "http://p2"])
    assert pm.has_proxies is True
    
    proxy = await pm.get_best_proxy()
    assert proxy in ["http://p1", "http://p2"]

@pytest.mark.asyncio
async def test_proxy_manager_add_remove():
    pm = ProxyManager()
    assert pm.has_proxies is False
    
    assert await pm.add_proxy("http://p1") is True
    assert await pm.add_proxy("http://p1") is False # duplicate
    assert await pm.add_proxy("   ") is False # empty
    
    assert pm.has_proxies is True
    
    assert await pm.remove_proxy("http://p1") is True
    assert await pm.remove_proxy("http://p1") is False # already removed
    assert pm.has_proxies is False

@pytest.mark.asyncio
async def test_proxy_manager_get_best_proxy():
    pm = ProxyManager(["http://p1", "http://p2"])
    await pm.report_success("http://p1", 0.5)
    best = await pm.get_best_proxy()
    assert best == "http://p1"
    
    best = await pm.get_best_proxy(exclude={"http://p1"})
    assert best == "http://p2"

@pytest.mark.asyncio
async def test_proxy_manager_failure():
    # max_fails=2, cooldown=1s => backoff_steps=1 => 1 * (2**1) = 2 seconds cooldown
    pm = ProxyManager(["http://p1"], max_fails=2, cooldown_seconds=1)
    
    await pm.report_failure("http://p1")
    best = await pm.get_best_proxy()
    assert best == "http://p1"
    
    await pm.report_failure("http://p1")
    best = await pm.get_best_proxy()
    assert best is None # p1 is now inactive
    
    assert pm.stats["total_fallbacks"] == 1
    assert pm.stats["active_proxies"] == 0

    await asyncio.sleep(2.1)
    best = await pm.get_best_proxy() # this triggers _reactivate_expired
    assert best == "http://p1"

@pytest.mark.asyncio
async def test_proxy_manager_auto_fallback_disabled():
    pm = ProxyManager(["http://p1"], max_fails=1, cooldown_seconds=10, auto_fallback=False)
    await pm.report_failure("http://p1")
    best = await pm.get_best_proxy()
    assert best is None
    assert pm.stats["total_fallbacks"] == 0

@pytest.mark.asyncio
async def test_proxy_manager_status_and_stats():
    pm = ProxyManager(["http://p1"])
    await pm.report_success("http://p1", 0.1)
    status = await pm.get_all_status()
    assert len(status) == 1
    assert status[0].url_masked == "http://p1"
    assert status[0].is_active is True
    assert status[0].success_rate == 1.0
    
    stats = pm.stats
    assert stats["total_proxies"] == 1
    assert stats["active_proxies"] == 1
    
    await pm.reset_all()
    status = await pm.get_all_status()
    assert status[0].total_success == 0

@pytest.mark.asyncio
async def test_proxy_manager_health_check_loop():
    # cooldown_seconds=1 => backoff 2 seconds
    # we'll test lines 137-140 (reactivate)
    pm = ProxyManager(["http://p1"], max_fails=1, cooldown_seconds=1, health_check_interval=1)
    await pm.report_failure("http://p1")
    assert (await pm.get_all_status())[0].is_active is False
    
    pm.start_health_checks()
    # It should become active after 2 seconds
    await asyncio.sleep(2.2)
    assert (await pm.get_all_status())[0].is_active is True
    
    # Trigger grace period (lines 151-153):
    # active, idle for health_check_interval, consecutive_fails > 0
    await pm.stop_health_checks()
    
    pm._max_fails = 2
    await pm.report_failure("http://p1") # fails=1, active
    # set last_used and last_fail_time back to trigger grace period
    async with pm._lock:
        p = pm._by_url["http://p1"]
        p.last_used = time.time() - 2.0
        p.last_fail_time = time.time() - 2.0
    
    pm.start_health_checks()
    await asyncio.sleep(1.2) # health check loop will apply grace period
    
    await pm.stop_health_checks()
    
    async with pm._lock:
        p = pm._by_url["http://p1"]
        assert p.consecutive_fails == 0

@pytest.mark.asyncio
async def test_proxy_manager_stop_health_checks_exception():
    pm = ProxyManager()
    pm.start_health_checks()
    # Mock the task to raise Exception when awaited
    task = pm._health_task
    # Actually, we can just cancel it and let it handle CancelledError inside _health_check_loop
    await pm.stop_health_checks()
    assert pm._health_task is None

    # Call it again to hit "if task is None"
    await pm.stop_health_checks()
    
    # Mock a done task to hit "if task.done()"
    mock_task = MagicMock()
    mock_task.done.return_value = True
    pm._health_task = mock_task
    await pm.stop_health_checks()

@pytest.mark.asyncio
async def test_proxy_manager_unknown_proxy():
    pm = ProxyManager(["http://p1"])
    await pm.report_success("http://unknown", 0.5) # Should return None early
    await pm.report_failure("http://unknown") # Should return None early
    status = await pm.get_all_status()
    assert status[0].total_requests == 0

@pytest.mark.asyncio
async def test_proxy_manager_all_excluded():
    pm = ProxyManager(["http://p1", "http://p2"])
    best = await pm.get_best_proxy(exclude={"http://p1", "http://p2"})
    assert best in ["http://p1", "http://p2"]

@pytest.mark.asyncio
async def test_proxy_manager_empty():
    pm = ProxyManager()
    assert await pm.get_best_proxy() is None
