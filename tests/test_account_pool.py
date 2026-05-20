import pytest
import json
from pathlib import Path
from instagram_mcp.account_pool import AccountPool
from instagram_mcp.delay import JitterAsyncSession, DelaySimulator

def test_account_pool_loading_and_rotation(tmp_path):
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()
    
    # Write mock cookie files
    cookie_content1 = [
        {"name": "sessionid", "value": "sess1", "domain": ".instagram.com", "path": "/"},
        {"name": "ds_user_id", "value": "111", "domain": ".instagram.com", "path": "/"}
    ]
    cookie_content2 = [
        {"name": "sessionid", "value": "sess2", "domain": ".instagram.com", "path": "/"},
        {"name": "ds_user_id", "value": "222", "domain": ".instagram.com", "path": "/"}
    ]
    
    file1 = accounts_dir / "user_one.json"
    file1.write_text(json.dumps(cookie_content1))
    
    file2 = accounts_dir / "user_two.json"
    file2.write_text(json.dumps(cookie_content2))
    
    pool = AccountPool(accounts_dir=str(accounts_dir))
    loaded = pool.load_accounts()
    
    assert loaded == 2
    assert "user_one" in pool.accounts
    assert "user_two" in pool.accounts
    
    # Rotation test
    # Since rotation runs in a loop inside get_next_account, calling it twice should return both accounts
    # Wait, get_next_account is an async method
    
@pytest.mark.asyncio
async def test_account_pool_rotation_async(tmp_path):
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()
    
    cookie_content1 = [{"name": "sessionid", "value": "sess1", "domain": ".instagram.com", "path": "/"}]
    cookie_content2 = [{"name": "sessionid", "value": "sess2", "domain": ".instagram.com", "path": "/"}]
    
    (accounts_dir / "user_one.json").write_text(json.dumps(cookie_content1))
    (accounts_dir / "user_two.json").write_text(json.dumps(cookie_content2))
    
    pool = AccountPool(accounts_dir=str(accounts_dir))
    pool.load_accounts()
    
    res1 = await pool.get_next_account()
    res2 = await pool.get_next_account()
    res3 = await pool.get_next_account()
    
    assert res1 is not None
    assert res2 is not None
    assert res3 is not None
    
    # Check that they rotate
    aliases = [res1[0], res2[0], res3[0]]
    assert "user_one" in aliases
    assert "user_two" in aliases
    assert aliases[0] == aliases[2] # Since there are only 2 accounts

@pytest.mark.asyncio
async def test_account_pool_health_marking(tmp_path):
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()
    
    cookie_content = [{"name": "sessionid", "value": "sess1", "domain": ".instagram.com", "path": "/"}]
    (accounts_dir / "user_one.json").write_text(json.dumps(cookie_content))
    
    pool = AccountPool(accounts_dir=str(accounts_dir))
    pool.load_accounts()
    
    # Mark as rate limited
    pool.mark_rate_limited("user_one", cooldown_seconds=1)
    
    # Attempt to retrieve account immediately should return None (since no active accounts left)
    res = await pool.get_next_account()
    assert res is None
    
    # Verify pool status shows rate_limited
    status = pool.get_pool_status()
    assert status["user_one"]["status"] == "rate_limited"
    
    # Wait for cooldown to expire
    import asyncio
    await asyncio.sleep(1.1)
    
    # Now it should be recovered and active
    res_after = await pool.get_next_account()
    assert res_after is not None
    assert res_after[0] == "user_one"
