import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from instagram_mcp.client import InstagramClient, FetchError, _mask_proxy
from instagram_mcp.config import MCPConfig
from instagram_mcp.models import DateRange

# Mock CURL_CFFI_AVAILABLE to bypass the check in __init__
import instagram_mcp.client
instagram_mcp.client.CURL_CFFI_AVAILABLE = True

@pytest.fixture
def mock_config():
    config = MagicMock(spec=MCPConfig)
    config.ig_headers = {"User-Agent": "test"}
    config.ig_impersonate = "chrome110"
    config.request_timeout = 30
    config.max_retries = 3
    config.ig_endpoint = "https://i.instagram.com/api/v1/users/web_profile_info/?username={}"
    config.ig_graphql_endpoint = "https://www.instagram.com/graphql/query"
    config.ig_graphql_doc_id = "123"
    config.pagination_page_size = 12
    config.cache_profile_ttl = 300
    config.cache_feed_ttl = 600
    config.cache_tagged_ttl = 600
    config.cache_reposts_ttl = 600
    config.cache_reels_ttl = 600
    config.cache_comments_ttl = 600
    return config

@pytest.fixture
def mock_proxy_manager():
    pm = MagicMock()
    pm.get_best_proxy = AsyncMock(return_value=None)
    pm.report_failure = AsyncMock()
    pm.report_success = AsyncMock()
    return pm

@pytest.fixture
def mock_rate_limiter():
    rl = MagicMock()
    rl.acquire = AsyncMock()
    rl.on_rate_limited = AsyncMock()
    rl.on_success = AsyncMock()
    return rl

@pytest.fixture
def mock_cache():
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    
    async def mock_get_or_fetch(key, fetch_func, ttl=None):
        return await fetch_func()
    
    cache.get_or_fetch = AsyncMock(side_effect=mock_get_or_fetch)
    return cache

@pytest.fixture
def mock_cookie_manager():
    cm = MagicMock()
    cm.is_authenticated = True
    cm.cookies = {"csrftoken": "test_token"}
    cm.ensure_csrf_tokens = AsyncMock(return_value=("fb_dtsg", "lsd"))
    return cm

@pytest.fixture
def client(mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache, mock_cookie_manager):
    return InstagramClient(
        config=mock_config,
        proxy_manager=mock_proxy_manager,
        rate_limiter=mock_rate_limiter,
        cache=mock_cache,
        cookie_manager=mock_cookie_manager
    )

def test_mask_proxy():
    assert _mask_proxy(None) == "direct"
    assert _mask_proxy("http://user:pass@host:8080") == "http://***@host:8080"
    assert _mask_proxy("http://host:8080") == "http://host:8080"
    assert _mask_proxy("invalid-url") == "invalid-url"

def test_client_init(client, mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache, mock_cookie_manager):
    assert client.config == mock_config
    assert client.proxy_manager == mock_proxy_manager
    assert client.rate_limiter == mock_rate_limiter
    assert client.cache == mock_cache
    assert client.cookie_manager == mock_cookie_manager

@pytest.mark.asyncio
async def test_get_session(client):
    with patch("instagram_mcp.client.AsyncSession") as mock_session_cls:
        mock_session = mock_session_cls.return_value
        session = await client._get_session(None)
        assert session == mock_session
        
        # Test pooling
        session2 = await client._get_session(None)
        assert session2 == session
        assert mock_session_cls.call_count == 1

@pytest.mark.asyncio
async def test_get_session_with_proxy(client):
    with patch("instagram_mcp.client.AsyncSession") as mock_session_cls:
        proxy = "http://proxy:8080"
        session = await client._get_session(proxy)
        mock_session_cls.assert_called_with(
            headers=client.config.ig_headers,
            impersonate=client.config.ig_impersonate,
            proxies={"http": proxy, "https": proxy},
            timeout=client.config.request_timeout
        )

@pytest.mark.asyncio
async def test_get_session_pool_eviction(client):
    with patch("instagram_mcp.client.AsyncSession") as mock_session_cls:
        # Fill pool
        for i in range(55):
            await client._get_session(f"http://proxy{i}:8080")
        
        # Check that we have at most 50 sessions
        assert len(client._session_pool) == 50

@pytest.mark.asyncio
async def test_invalidate_session(client):
    with patch("instagram_mcp.client.AsyncSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value = mock_session
        proxy = "http://proxy:8080"
        await client._get_session(proxy)
        assert proxy in client._session_pool
        
        await client._invalidate_session(proxy)
        assert proxy not in client._session_pool
        mock_session.close.assert_called_once()

@pytest.mark.asyncio
async def test_close(client):
    with patch("instagram_mcp.client.AsyncSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value = mock_session
        await client._get_session(None)
        await client._get_auth_session()
        
        await client.close()
        assert client._closed
        assert mock_session.close.call_count >= 2

@pytest.mark.asyncio
async def test_close_already_closed(client):
    await client.close()
    await client.close() # Should not raise

@pytest.mark.asyncio
async def test_get_session_closed(client):
    await client.close()
    with pytest.raises(FetchError, match="Client is closed"):
        await client._get_session(None)

@pytest.mark.asyncio
async def test_get_auth_session_closed(client):
    await client.close()
    with pytest.raises(FetchError, match="Client is closed"):
        await client._get_auth_session()

def test_close_sessions_sync(client):
    with patch("asyncio.get_running_loop") as mock_get_loop:
        mock_loop = MagicMock()
        scheduled = []
        def _capture(coro):
            scheduled.append(coro)
            return MagicMock()
        mock_loop.create_task.side_effect = _capture
        mock_get_loop.return_value = mock_loop
        client.close_sessions()
        mock_loop.create_task.assert_called_once()
        # Close the coroutine so the test doesn't leak a "never awaited" warning
        for coro in scheduled:
            coro.close()

def test_close_sessions_sync_no_loop(client):
    with patch("asyncio.get_running_loop", side_effect=RuntimeError):
        client.close_sessions()
        assert client._closed

@pytest.mark.asyncio
async def test_with_proxy_retry_success(client):
    client.proxy_manager.get_best_proxy.return_value = "proxy1"
    mock_op = AsyncMock(return_value={"ok": True, "status_code": 200, "data": "test"})
    result = await client._with_proxy_retry("test_op", mock_op)
    assert result["data"] == "test"
    assert mock_op.call_count == 1
    client.proxy_manager.report_success.assert_called_once()
    client.rate_limiter.on_success.assert_called_once()

@pytest.mark.asyncio
async def test_with_proxy_retry_fail_then_success(client):
    client.proxy_manager.get_best_proxy.side_effect = ["proxy1", "proxy2"]
    mock_op = AsyncMock(side_effect=[
        Exception("Connection failed"),
        {"ok": True, "status_code": 200, "data": "test"}
    ])
    
    result = await client._with_proxy_retry("test_op", mock_op)
    assert result["data"] == "test"
    assert mock_op.call_count == 2
    client.proxy_manager.report_failure.assert_called_once()
    client.proxy_manager.report_success.assert_called_once()

@pytest.mark.asyncio
async def test_with_proxy_retry_429(client):
    client.proxy_manager.get_best_proxy.side_effect = ["proxy1", "proxy2", "proxy3"]
    mock_op = AsyncMock(return_value={"ok": False, "status_code": 429})
    
    with pytest.raises(FetchError, match="last status=429"):
        await client._with_proxy_retry("test_op", mock_op)
    
    assert mock_op.call_count == 3
    assert client.rate_limiter.on_rate_limited.call_count == 3

@pytest.mark.asyncio
async def test_with_proxy_retry_not_ok(client):
    mock_op = AsyncMock(return_value={"ok": False, "status_code": 500})
    
    with pytest.raises(FetchError, match="last status=500"):
        await client._with_proxy_retry("test_op", mock_op)
    
    assert mock_op.call_count == 3

@pytest.mark.asyncio
async def test_with_proxy_retry_fatal_fetch_error(client):
    mock_op = AsyncMock(side_effect=FetchError("Fatal"))
    with pytest.raises(FetchError, match="Fatal"):
        await client._with_proxy_retry("test_op", mock_op)
    assert mock_op.call_count == 1

@pytest.mark.asyncio
async def test_fetch_user_success(client):
    user_data = {"id": "123", "username": "testuser"}
    client._with_proxy_retry = AsyncMock(return_value={"ok": True, "found": True, "user": user_data})
    
    result = await client.fetch_user("testuser")
    assert result == user_data
    client.rate_limiter.acquire.assert_called_once()

@pytest.mark.asyncio
async def test_fetch_user_not_found(client):
    client._with_proxy_retry = AsyncMock(return_value={"ok": True, "found": False, "user": None})
    
    result = await client.fetch_user("testuser")
    assert result is None

@pytest.mark.asyncio
async def test_fetch_profile_attempt_200(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"user": {"id": "123"}}}
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_profile_attempt("testuser", None)
        assert result["ok"] is True
        assert result["found"] is True
        assert result["user"] == {"id": "123"}

@pytest.mark.asyncio
async def test_fetch_profile_attempt_404(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_profile_attempt("testuser", None)
        assert result["ok"] is True
        assert result["found"] is False
        assert result["status_code"] == 404

@pytest.mark.asyncio
async def test_fetch_profile_attempt_non_json(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("Invalid JSON")
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_profile_attempt("testuser", None)
        assert result["ok"] is False

@pytest.mark.asyncio
async def test_fetch_profile_attempt_empty_shell(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"user": None}}
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_profile_attempt("testuser", None)
        assert result["ok"] is True
        assert result["found"] is False

@pytest.mark.asyncio
async def test_fetch_graphql_attempt_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "user": {
                "edge_owner_to_timeline_media": {
                    "edges": [{"node": {"id": "p1"}}],
                    "page_info": {"has_next_page": True, "end_cursor": "c1"}
                }
            }
        }
    }
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_graphql_attempt("testuser", 12, "cursor", None)
        assert result["ok"] is True
        assert len(result["edges"]) == 1
        assert result["end_cursor"] == "c1"
        assert result["has_next_page"] is True

@pytest.mark.asyncio
async def test_fetch_graphql_attempt_new_api(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "xdt_api__v1__feed__user_timeline_graphql_connection": {
                "edges": [{"node": {"id": "p1"}}],
                "page_info": {"has_next_page": False, "end_cursor": ""}
            }
        }
    }
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_graphql_attempt("testuser", 12, "cursor", None)
        assert result["ok"] is True
        assert len(result["edges"]) == 1

@pytest.mark.asyncio
async def test_fetch_graphql_attempt_error(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"errors": [{"message": "fail"}]}
    
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        result = await client._fetch_graphql_attempt("testuser", 12, "cursor", None)
        assert result["ok"] is False

@pytest.mark.asyncio
async def test_fetch_user_feed_pagination(client):
    # Page 1 from cache
    page1 = {
        "ok": True,
        "edges": [{"node": {"id": "p1", "taken_at": time.time()}}],
        "end_cursor": "c2",
        "has_next_page": True
    }
    # Page 2 from network
    page2 = {
        "ok": True,
        "edges": [{"node": {"id": "p2", "taken_at": time.time()}}],
        "end_cursor": "",
        "has_next_page": False
    }
    
    client.cache.get.side_effect = [page1, None]
    client._fetch_single_feed_page = AsyncMock(return_value=page2)
    
    result = await client.fetch_user_feed("uid", "testuser", "c1", max_posts=10)
    assert len(result["edges"]) == 2
    assert result["pages_fetched"] == 2
    assert result["has_more"] is False

@pytest.mark.asyncio
async def test_fetch_user_feed_age_limit(client):
    now = time.time()
    page1 = {
        "ok": True,
        "edges": [
            {"node": {"id": "new", "taken_at": now}},
            {"node": {"id": "old", "taken_at": now - 1000000}} # ~11 days old
        ],
        "end_cursor": "c2",
        "has_next_page": True
    }
    client.cache.get.return_value = page1
    
    # max_age_days = 5
    result = await client.fetch_user_feed("uid", "testuser", "c1", max_posts=10, max_age_days=5)
    assert len(result["edges"]) == 1
    assert result["edges"][0]["node"]["id"] == "new"

@pytest.mark.asyncio
async def test_fetch_user_feed_date_range(client):
    now_ts = time.time()
    dr = DateRange(since=now_ts - 500, until=now_ts + 500)
    page1 = {
        "ok": True,
        "edges": [
            {"node": {"id": "too_new", "taken_at": now_ts + 1000}},
            {"node": {"id": "ok", "taken_at": now_ts}},
            {"node": {"id": "too_old", "taken_at": now_ts - 1000}}
        ],
        "end_cursor": "",
        "has_next_page": False
    }
    client.cache.get.return_value = page1
    
    result = await client.fetch_user_feed("uid", "testuser", "c1", max_posts=10, date_range=dr)
    assert len(result["edges"]) == 1
    assert result["edges"][0]["node"]["id"] == "ok"

@pytest.mark.asyncio
async def test_fetch_bulk(client):
    async def mock_fetch_user(u, ttl):
        if u == "fail":
            raise Exception("error")
        return {"id": u}
        
    client.fetch_user = AsyncMock(side_effect=mock_fetch_user)
    
    usernames = ["user1", "user2", "user1", "fail"]
    results = await client.fetch_bulk(usernames)
    
    assert len(results) == 4
    assert results[0]["found"] is True
    assert results[3]["found"] is False
    assert results[3]["error"] == "error"

@pytest.mark.asyncio
async def test_fetch_tagged_posts_no_auth(client):
    client._cookie_manager.is_authenticated = False
    with pytest.raises(FetchError, match="requires authentication"):
        await client.fetch_tagged_posts("uid", "user")

@pytest.mark.asyncio
async def test_fetch_tagged_posts_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "xdt_api__v1__usertags__user_id__feed_connection": {
                "edges": [{"node": {"id": "t1"}}],
                "page_info": {"has_next_page": True, "end_cursor": "c1"}
            }
        }
    }
    
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_get_auth:
        mock_session = mock_get_auth.return_value
        mock_session.post.return_value = mock_resp
        
        result = await client.fetch_tagged_posts("uid", "user")
        assert len(result["edges"]) == 1
        assert result["end_cursor"] == "c1"

@pytest.mark.asyncio
async def test_fetch_tagged_posts_paginated(client):
    # page_size will be 10 (min(12, 10))
    page1 = {"edges": [{"id": f"t{i}"} for i in range(10)], "has_next_page": True, "end_cursor": "c2"}
    page2 = {"edges": [{"id": "t10"}], "has_next_page": False, "end_cursor": ""}
    client.fetch_tagged_posts = AsyncMock(side_effect=[page1, page2])
    
    result = await client.fetch_tagged_posts_paginated("uid", "user", max_posts=11)
    assert len(result["edges"]) == 11
    assert result["pages_fetched"] == 2

@pytest.mark.asyncio
async def test_fetch_reposts_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "fetch__XDTUserDict": {
                "user_reposts_timeline": {
                    "repost_grid_items": [{"media": {"id": "r1"}}],
                    "repost_next_max_id": "m1",
                    "repost_more_available": True
                }
            }
        }
    }
    
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_get_auth:
        mock_session = mock_get_auth.return_value
        mock_session.post.return_value = mock_resp
        
        result = await client.fetch_reposts("uid", "user")
        assert len(result["items"]) == 1
        assert result["next_max_id"] == "m1"

@pytest.mark.asyncio
async def test_fetch_reposts_paginated(client):
    # page_size will be 10
    page1 = {"items": [{"id": f"r{i}"} for i in range(10)], "has_more": True, "next_max_id": "m2"}
    page2 = {"items": [{"id": "r10"}], "has_more": False, "next_max_id": ""}
    client.fetch_reposts = AsyncMock(side_effect=[page1, page2])
    
    result = await client.fetch_reposts_paginated("uid", "user", max_posts=11)
    assert len(result["items"]) == 11

@pytest.mark.asyncio
async def test_fetch_reels_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "xdt_api__v1__clips__user__connection_v2": {
                "edges": [{"node": {"media": {"id": "rl1"}}}],
                "page_info": {"has_next_page": True, "end_cursor": "c1"}
            }
        }
    }
    
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_get_auth:
        mock_session = mock_get_auth.return_value
        mock_session.post.return_value = mock_resp
        
        result = await client.fetch_reels("uid", "user")
        assert len(result["edges"]) == 1

@pytest.mark.asyncio
async def test_fetch_reels_paginated(client):
    # page_size will be 10
    page1 = {"edges": [{"id": f"rl{i}"} for i in range(10)], "has_next_page": True, "end_cursor": "c2"}
    page2 = {"edges": [{"id": "rl10"}], "has_next_page": False, "end_cursor": ""}
    client.fetch_reels = AsyncMock(side_effect=[page1, page2])
    
    result = await client.fetch_reels_paginated("uid", "user", max_reels=11)
    assert len(result["edges"]) == 11

@pytest.mark.asyncio
async def test_fetch_post_success(client):
    client._with_proxy_retry = AsyncMock(return_value={"html": "<html>taken_at ... large enough ...</html>"})
    
    result = await client.fetch_post("shortcode")
    assert "taken_at" in result

@pytest.mark.asyncio
async def test_fetch_post_attempt_404(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        with pytest.raises(FetchError, match="not found"):
            await client._fetch_post_attempt("shortcode", None)

@pytest.mark.asyncio
async def test_fetch_post_attempt_small_html(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "short"
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        result = await client._fetch_post_attempt("shortcode", None)
        assert result["ok"] is False

@pytest.mark.asyncio
async def test_fetch_comments_success(client):
    client._with_proxy_retry = AsyncMock(return_value={
        "ok": True, "comments": [{"id": "c1"}], "has_more": False
    })
    
    result = await client.fetch_comments("mid")
    assert len(result["comments"]) == 1

@pytest.mark.asyncio
async def test_fetch_comments_paginated(client):
    page1 = {"comments": [{"id": "c1"}], "has_more": True, "next_min_id": "m2", "comment_count": 10}
    page2 = {"comments": [{"id": "c2"}], "has_more": False, "next_min_id": ""}
    client.fetch_comments = AsyncMock(side_effect=[page1, page2])
    
    result = await client.fetch_comments_paginated("mid", max_comments=10)
    assert len(result["comments"]) == 2

@pytest.mark.asyncio
async def test_fetch_comments_attempt_404(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        from instagram_mcp.exceptions import UserNotFoundError
        with pytest.raises(UserNotFoundError):
            await client._fetch_comments_attempt("mid", {}, None)

@pytest.mark.asyncio
async def test_fetch_comments_attempt_403(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        from instagram_mcp.exceptions import PrivateAccountError
        with pytest.raises(PrivateAccountError):
            await client._fetch_comments_attempt("mid", {}, None)
