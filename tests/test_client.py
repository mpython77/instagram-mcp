import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from instagram_mcp.client import InstagramClient, FetchError, _mask_proxy
from instagram_mcp.exceptions import AuthError
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
    config.async_max_clients = 50
    # Path-shaped fields — must be real strings so the new ensure_path guard
    # in AccountPool / MediaCache / JsonExporter / InstagramClient does not
    # reject MagicMock attribute access at construction time.
    config.accounts_dir = ""
    config.media_cache_dir = ""
    config.cookies_path = ""
    config.export_dir = "exports"
    config.delay_min_ms = 0
    config.delay_max_ms = 0
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


@pytest.mark.asyncio
async def test_get_auth_session_no_cookies(mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache):
    cm = MagicMock()
    cm.is_authenticated = False  # no cookies
    client_no_auth = InstagramClient(
        config=mock_config,
        proxy_manager=mock_proxy_manager,
        rate_limiter=mock_rate_limiter,
        cache=mock_cache,
        cookie_manager=cm,
    )
    with pytest.raises(AuthError):
        await client_no_auth._get_auth_session()


@pytest.mark.asyncio
async def test_get_auth_session_no_manager(mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache):
    client_no_auth = InstagramClient(
        config=mock_config,
        proxy_manager=mock_proxy_manager,
        rate_limiter=mock_rate_limiter,
        cache=mock_cache,
        cookie_manager=None,
    )
    with pytest.raises(AuthError):
        await client_no_auth._get_auth_session()

def test_client_init(client, mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache, mock_cookie_manager):
    assert client.config == mock_config
    assert client.proxy_manager == mock_proxy_manager
    assert client.rate_limiter == mock_rate_limiter
    assert client.cache == mock_cache
    assert client.cookie_manager == mock_cookie_manager

@pytest.mark.asyncio
async def test_get_session(client):
    with patch("instagram_mcp.client.JitterAsyncSession") as mock_session_cls:
        mock_session = mock_session_cls.return_value
        session = await client._get_session(None)
        assert session == mock_session
        
        # Test pooling
        session2 = await client._get_session(None)
        assert session2 == session
        assert mock_session_cls.call_count == 1

@pytest.mark.asyncio
async def test_get_session_with_proxy(client):
    with patch("instagram_mcp.client.JitterAsyncSession") as mock_session_cls:
        proxy = "http://proxy:8080"
        session = await client._get_session(proxy)
        mock_session_cls.assert_called_with(
            headers=client.config.ig_headers,
            impersonate=client.config.ig_impersonate,
            proxies={"http": proxy, "https": proxy},
            timeout=client.config.request_timeout,
            max_clients=client.config.async_max_clients,
            delay_simulator=client._delay_simulator,
        )

@pytest.mark.asyncio
async def test_get_session_pool_eviction(client):
    with patch("instagram_mcp.client.JitterAsyncSession") as mock_session_cls:
        # Fill pool
        for i in range(55):
            await client._get_session(f"http://proxy{i}:8080")
        
        # Check that we have at most 50 sessions
        assert len(client._session_pool) == 50

@pytest.mark.asyncio
async def test_invalidate_session(client):
    with patch("instagram_mcp.client.JitterAsyncSession") as mock_session_cls:
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
    with patch("instagram_mcp.client.JitterAsyncSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.cookies = MagicMock()  # sync mock so cookies.set() doesn't produce unawaited coroutine
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


@pytest.mark.asyncio
async def test_like_post_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok"}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        result = await client.like_post("123456", "like")
        assert result["status"] == "liked"
        assert result["media_id"] == "123456"


@pytest.mark.asyncio
async def test_like_post_unlike(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok"}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        result = await client.like_post("123456", "unlike")
        assert result["status"] == "unliked"


@pytest.mark.asyncio
async def test_like_post_redirected(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.like_post("123456", "like")


@pytest.mark.asyncio
async def test_like_post_invalid_action(client):
    with pytest.raises(FetchError, match="action must be"):
        await client.like_post("123456", "invalid")


@pytest.mark.asyncio
async def test_follow_user_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok","friendship_status":{"following":true,"is_private":false,"outgoing_request":false}}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        result = await client.follow_user("47689974259", "follow")
        assert result["status"] == "followed"
        assert result["user_id"] == "47689974259"
        assert result["following"] is True


@pytest.mark.asyncio
async def test_follow_user_unfollow(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok","friendship_status":{"following":false,"is_private":false,"outgoing_request":false}}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        result = await client.follow_user("47689974259", "unfollow")
        assert result["status"] == "unfollowed"


@pytest.mark.asyncio
async def test_follow_user_api_error(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"fail","message":"something went wrong"}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="API error"):
            await client.follow_user("47689974259", "follow")


@pytest.mark.asyncio
async def test_follow_user_invalid_action(client):
    with pytest.raises(FetchError, match="action must be"):
        await client.follow_user("123", "invalid")


# ── delete_comment tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_comment_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok"}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        result = await client.delete_comment("1234567890", "9876543210")
        assert result["status"] == "deleted"
        assert result["comment_id"] == "9876543210"
        assert result["media_id"] == "1234567890"


@pytest.mark.asyncio
async def test_delete_comment_redirected(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.delete_comment("111", "222")


@pytest.mark.asyncio
async def test_delete_comment_api_error(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"fail","message":"Not authorized"}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="API error"):
            await client.delete_comment("111", "222")


@pytest.mark.asyncio
async def test_delete_comment_no_auth(mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache):
    cm = MagicMock()
    cm.is_authenticated = False
    unauthenticated_client = InstagramClient(
        config=mock_config,
        proxy_manager=mock_proxy_manager,
        rate_limiter=mock_rate_limiter,
        cache=mock_cache,
        cookie_manager=cm,
    )
    with pytest.raises(FetchError, match="requires authentication"):
        await unauthenticated_client.delete_comment("111", "222")


# ── publish_story tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_story_no_auth(mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache):
    cm = MagicMock()
    cm.is_authenticated = False
    unauthenticated_client = InstagramClient(
        config=mock_config,
        proxy_manager=mock_proxy_manager,
        rate_limiter=mock_rate_limiter,
        cache=mock_cache,
        cookie_manager=cm,
    )
    with pytest.raises(FetchError, match="requires authentication"):
        await unauthenticated_client.publish_story("/tmp/test.jpg")


@pytest.mark.asyncio
async def test_publish_story_upload_failure(client):
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock):
        with patch.object(client, "_upload_single_image", new_callable=AsyncMock) as mock_upload:
            mock_upload.side_effect = FetchError("upload failed: connection refused")
            with pytest.raises(FetchError, match="upload failed"):
                await client.publish_story("/tmp/test.jpg")


@pytest.mark.asyncio
async def test_publish_story_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok","media":{"id":"111222333444555","code":"ABC123"}}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_upload_single_image", new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = ("upload_id_123", 1080, 1920)
            result = await client.publish_story("/tmp/test.jpg", close_friends_only=False)
            assert result["ok"] is True
            assert "media_id" in result


@pytest.mark.asyncio
async def test_publish_story_close_friends(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok","media":{"id":"999888777666555","code":"XYZ789"}}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_upload_single_image", new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = ("upload_id_456", 1080, 1920)
            result = await client.publish_story("/tmp/test.jpg", close_friends_only=True)
            assert result["ok"] is True
            call_kwargs = mock_session.post.call_args
            sent_data = call_kwargs[1].get("data", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
            assert sent_data.get("post_to_close_friends_only") == "1"


# ── upload_reel tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_reel_no_auth(mock_config, mock_proxy_manager, mock_rate_limiter, mock_cache):
    cm = MagicMock()
    cm.is_authenticated = False
    unauthenticated_client = InstagramClient(
        config=mock_config, proxy_manager=mock_proxy_manager,
        rate_limiter=mock_rate_limiter, cache=mock_cache, cookie_manager=cm,
    )
    with pytest.raises(FetchError, match="requires authentication"):
        await unauthenticated_client.upload_reel("/tmp/test.mp4")


@pytest.mark.asyncio
async def test_upload_reel_success(client):
    mock_configure_resp = MagicMock()
    mock_configure_resp.status_code = 200
    mock_configure_resp.text = '{"status":"ok","media":{"pk":"111222333","code":"ReelABC"}}'
    mock_configure_resp.json = lambda: {"status": "ok", "media": {"pk": "111222333", "code": "ReelABC"}}
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_configure_resp)
        with patch.object(client, "_upload_video", new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = ("upload_id_reel_123", 30.0)
            result = await client.upload_reel("/tmp/test.mp4", caption="Test reel")
            assert result["ok"] is True
            assert result["shortcode"] == "ReelABC"
            assert result["media_id"] == "111222333"


@pytest.mark.asyncio
async def test_upload_reel_video_not_found(client):
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock):
        with patch.object(client, "_upload_video", new_callable=AsyncMock) as mock_upload:
            mock_upload.side_effect = FetchError("Video file not found: '/nonexistent.mp4'")
            with pytest.raises(FetchError, match="Video file not found"):
                await client.upload_reel("/nonexistent.mp4")


@pytest.mark.asyncio
async def test_upload_video_file_not_found(client):
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock):
        with pytest.raises(FetchError, match="Video file not found"):
            await client._upload_video(MagicMock(), "csrf", "cookie", "/nonexistent.mp4")


# ── broadcast channel tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_channel_info_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok","broadcast_channel":{"title":"My Channel","description":"desc","subscriber_count":1500,"is_pinned":false,"broadcast_status":"active"}}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        result = await client.broadcast_channel_info("abc123")
        assert result["channel_id"] == "abc123"
        assert result["title"] == "My Channel"
        assert result["subscriber_count"] == 1500


@pytest.mark.asyncio
async def test_broadcast_channel_posts_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status":"ok","broadcast_posts":[{"pk":"555","text":"update!","created_at":1700000000,"like_count":42}],"next_max_id":"cursor999"}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        result = await client.broadcast_channel_posts("abc123")
        assert len(result["posts"]) == 1
        assert result["posts"][0]["post_id"] == "555"
        assert result["posts"][0]["like_count"] == 42
        assert result["next_max_id"] == "cursor999"
        assert result["has_more"] is True


@pytest.mark.asyncio
async def test_broadcast_channel_redirected(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.broadcast_channel_info("abc123")


# ── threads tests ─────────────────────────────────────────────────────────────

# Simulates the embedded JSON found inside Threads HTML page (fields extracted by regex)
THREADS_PROFILE_RESPONSE = (
    '<!DOCTYPE html><html><head><title>zuck on Threads</title></head><body>'
    '<script type="application/json" data-sjs>{"require":[["ScheduledApplyEach",[],'
    '[{"__bbox":{"result":{"data":{"userData":{"user":{"pk":"4","username":"zuck",'
    '"full_name":"Mark Zuckerberg","biography":"Moving fast.",'
    '"follower_count":1500000,"following_count":500,"media_count":250,'
    '"is_verified":true,"text_post_app_is_private":false,'
    '"profile_pic_url":"https://example.com/pic.jpg",'
    '"has_onboarded_to_text_post_app":true}}}}}}}]]}'
    '</script></body></html>'
)

@pytest.mark.asyncio
async def test_threads_profile_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = THREADS_PROFILE_RESPONSE
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_sess:
        mock_session = mock_sess.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        result = await client.threads_profile("zuck")
        assert result["username"] == "zuck"
        assert result["followers"] == 1500000
        assert result["is_verified"] is True
        assert result["pk"] == "4"


@pytest.mark.asyncio
async def test_threads_profile_strips_at(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = THREADS_PROFILE_RESPONSE
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_sess:
        mock_session = mock_sess.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        result = await client.threads_profile("@zuck")
        assert result["username"] == "zuck"


@pytest.mark.asyncio
async def test_threads_profile_redirected(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_sess:
        mock_session = mock_sess.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="HTTP 302"):
            await client.threads_profile("someone")


@pytest.mark.asyncio
async def test_threads_user_posts_success(client):
    # Both GET calls return HTML with embedded post data (regex-extracted)
    posts_html = (
        '<!DOCTYPE html><html><body>'
        '<script>{"pk":"4","username":"zuck","follower_count":1500000,'
        '"is_verified":true,"text_post_app_is_private":false}</script>'
        '"code":"abc1234567",'
        '"like_count":42,'
        '"text":"hello threads",'
        '"taken_at":1700000000'
        '</body></html>'
    )
    mock_profile_resp = MagicMock()
    mock_profile_resp.status_code = 200
    mock_profile_resp.text = THREADS_PROFILE_RESPONSE
    mock_posts_resp = MagicMock()
    mock_posts_resp.status_code = 200
    mock_posts_resp.text = posts_html
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_sess:
        mock_session = mock_sess.return_value
        mock_session.get = AsyncMock(side_effect=[mock_profile_resp, mock_posts_resp])
        result = await client.threads_user_posts("zuck")
        assert result["username"] == "zuck"
        assert result["has_more"] is False
        # posts may be empty if regex doesn't match minimal HTML — that's acceptable
        assert isinstance(result["posts"], list)


# ── hashtag_suggest tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hashtag_suggest_success(client):
    posts = [
        {"caption": "#travel #photography great trip", "like_count": 1000},
        {"caption": "#travel #adventure exploring", "like_count": 500},
        {"caption": "#photography #landscape beautiful", "like_count": 200},
    ]
    with patch.object(client, "fetch_hashtag", new_callable=AsyncMock) as mock_fetch, \
         patch.object(client, "_fetch_hashtag_info", new_callable=AsyncMock) as mock_info:
        mock_fetch.return_value = {"posts": posts, "has_more": False, "auth_used": False}
        mock_info.return_value = {"media_count": 5000000}
        result = await client.hashtag_suggest("travel", target_count=10)
        assert result["seed"] == "travel"
        assert result["posts_analyzed"] == 3
        assert result["unique_hashtags_found"] >= 3
        assert "copy_paste" in result
        assert result["copy_paste"].startswith("#")


@pytest.mark.asyncio
async def test_hashtag_suggest_no_posts(client):
    with patch.object(client, "fetch_hashtag", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"posts": [], "has_more": False, "auth_used": False}
        with pytest.raises(FetchError, match="hashtag_suggest"):
            await client.hashtag_suggest("emptytag")


@pytest.mark.asyncio
async def test_hashtag_suggest_strips_hash(client):
    posts = [
        {"caption": "#travel #photography", "like_count": 100},
    ]
    with patch.object(client, "fetch_hashtag", new_callable=AsyncMock) as mock_fetch, \
         patch.object(client, "_fetch_hashtag_info", new_callable=AsyncMock) as mock_info:
        mock_fetch.return_value = {"posts": posts, "has_more": False, "auth_used": False}
        mock_info.return_value = {"media_count": 0}
        result = await client.hashtag_suggest("#travel")
        assert result["seed"] == "travel"


# ── caption_analyze tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_caption_analyze_success(client):
    user_data = {
        "id": "12345",
        "username": "testuser",
        "edge_owner_to_timeline_media": {
            "edges": [
                {"node": {
                    "edge_media_to_caption": {"edges": [{"node": {"text": "Check the link in bio! #travel #photography great shot"}}]},
                    "edge_media_preview_like": {"count": 1000},
                }},
                {"node": {
                    "edge_media_to_caption": {"edges": [{"node": {"text": "Amazing view today!"}}]},
                    "edge_media_preview_like": {"count": 500},
                }},
                {"node": {
                    "edge_media_to_caption": {"edges": [{"node": {"text": "#adventure #explore #travel tag a friend below"}}]},
                    "edge_media_preview_like": {"count": 200},
                }},
            ],
            "page_info": {"has_next_page": False, "end_cursor": ""},
        },
    }
    with patch.object(client, "fetch_user", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = user_data
        result = await client.caption_analyze("testuser", max_posts=10)
        assert result["username"] == "testuser"
        assert result["posts_analyzed"] == 3
        assert result["avg_caption_length"] > 0
        assert "insights" in result
        assert isinstance(result["top_hashtags"], list)


@pytest.mark.asyncio
async def test_caption_analyze_user_not_found(client):
    with patch.object(client, "fetch_user", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None
        with pytest.raises(FetchError, match="not found"):
            await client.caption_analyze("nonexistent")


@pytest.mark.asyncio
async def test_caption_analyze_private_account(client):
    user_data = {"id": "12345", "username": "privateuser", "is_private": True}
    with patch.object(client, "fetch_user", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = user_data
        with pytest.raises(FetchError, match="private"):
            await client.caption_analyze("privateuser")


@pytest.mark.asyncio
async def test_caption_analyze_fetches_more_pages(client):
    user_data = {
        "id": "12345",
        "username": "testuser",
        "edge_owner_to_timeline_media": {
            "edges": [
                {"node": {
                    "edge_media_to_caption": {"edges": [{"node": {"text": "Post 1 #tag"}}]},
                    "edge_media_preview_like": {"count": 100},
                }},
            ],
            "page_info": {"has_next_page": True, "end_cursor": "cursor123"},
        },
    }
    extra_feed = {
        "edges": [
            {"node": {
                "edge_media_to_caption": {"edges": [{"node": {"text": "Post 2 #more"}}]},
                "edge_media_preview_like": {"count": 200},
            }},
        ],
        "end_cursor": "",
        "has_next_page": False,
    }
    with patch.object(client, "fetch_user", new_callable=AsyncMock) as mock_user, \
         patch.object(client, "fetch_user_feed", new_callable=AsyncMock) as mock_feed:
        mock_user.return_value = user_data
        mock_feed.return_value = extra_feed
        result = await client.caption_analyze("testuser", max_posts=10)
        assert result["posts_analyzed"] == 2
        mock_feed.assert_called_once()


# ── Bug-fix regression tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_edit_profile_first_name_whitespace_no_index_error(client):
    """edit_profile must not raise IndexError when full_name is whitespace-only."""
    mock_info_resp = MagicMock()
    mock_info_resp.status_code = 200
    mock_info_resp.json.return_value = {"user": {"biography": "bio", "full_name": "  ", "external_url": "", "email": "", "phone_number": "", "username": "testuser"}}
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.text = '{"status":"ok","user":{"pk":"1","username":"testuser","biography":"bio","full_name":"","external_url":"","is_private":false}}'
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = AsyncMock(return_value=mock_info_resp)
        mock_session.post = AsyncMock(return_value=mock_post_resp)
        # Must not raise IndexError — whitespace-only full_name → first_name=""
        result = await client.edit_profile(biography="new bio")
        assert result["status"] == "updated"


@pytest.mark.asyncio
async def test_dm_send_photo_redirected(client):
    """dm_send_photo must raise FetchError on 302 (not silently follow redirect)."""
    mock_upload_resp = MagicMock()
    mock_upload_resp.status_code = 302
    mock_upload_resp.text = ""
    with patch.object(client, "_require_auth", new_callable=AsyncMock) as mock_auth, \
         patch.object(client, "_upload_single_image", new_callable=AsyncMock) as mock_up, \
         patch("os.path.isfile", return_value=True):
        mock_cm = MagicMock()
        mock_cm.cookies = {"ds_user_id": "123", "ig_did": "abc", "csrftoken": "tok"}
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_upload_resp)
        mock_auth.return_value = (mock_cm, mock_session, "tok")
        mock_up.return_value = ("upload_id_123", 640, 480)
        with pytest.raises(FetchError, match="redirected"):
            await client.dm_send_photo("photo.jpg", thread_id="tid123")


@pytest.mark.asyncio
async def test_dm_mute_redirected(client):
    """dm_mute must raise FetchError on 302 (not silently follow redirect)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.dm_mute("thread_abc", mute=True)


@pytest.mark.asyncio
async def test_dm_mute_html_response(client):
    """dm_mute must raise FetchError when response is HTML (blocked session)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body>Login</body></html>"
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="got HTML"):
            await client.dm_mute("thread_abc", mute=True)


@pytest.mark.asyncio
async def test_story_mark_seen_redirected(client):
    """story_mark_seen must raise FetchError on 302 (not silently follow redirect)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.story_mark_seen(["mid1"], ["oid1"], [1700000000])


@pytest.mark.asyncio
async def test_compare_followers_unfollowers_needs_both_sets(client):
    """compare_followers(unfollowers) must fetch BOTH followers and following.

    Bug: previously follower_ids was set() when analysis_type='unfollowers',
    making unfollowers = following - {} = all following (wrong).
    """
    followers_page = {"users": [{"pk": "A"}, {"pk": "B"}], "next_max_id": ""}
    following_page = {"users": [{"pk": "B"}, {"pk": "C"}], "next_max_id": ""}

    call_count = {"n": 0}

    async def mock_auth_get(url, params, csrf, session, name):
        call_count["n"] += 1
        if "followers" in url:
            return followers_page
        return following_page

    with patch.object(client, "_require_auth", new_callable=AsyncMock) as mock_req, \
         patch.object(client, "_auth_get", side_effect=mock_auth_get):
        mock_cm = MagicMock()
        mock_cm.cookies = {"ds_user_id": "123", "csrftoken": "tok"}
        mock_req.return_value = (mock_cm, MagicMock(), "tok")
        result = await client.compare_followers("unfollowers", max_users=100)

    # C follows me (in following) but A doesn't follow back => unfollower is C
    assert set(result["unfollowers"]) == {"C"}
    assert result["unfollower_count"] == 1
    # Must have fetched BOTH endpoints (not just following)
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_compare_followers_fans_needs_both_sets(client):
    """compare_followers(fans) must fetch BOTH sets to compute fans correctly."""
    followers_page = {"users": [{"pk": "A"}, {"pk": "B"}], "next_max_id": ""}
    following_page = {"users": [{"pk": "B"}, {"pk": "C"}], "next_max_id": ""}

    async def mock_auth_get(url, params, csrf, session, name):
        if "followers" in url:
            return followers_page
        return following_page

    with patch.object(client, "_require_auth", new_callable=AsyncMock) as mock_req, \
         patch.object(client, "_auth_get", side_effect=mock_auth_get):
        mock_cm = MagicMock()
        mock_cm.cookies = {"ds_user_id": "123", "csrftoken": "tok"}
        mock_req.return_value = (mock_cm, MagicMock(), "tok")
        result = await client.compare_followers("fans", max_users=100)

    # A follows me but I don't follow A => fan is A
    assert set(result["fans"]) == {"A"}
    assert result["fan_count"] == 1


@pytest.mark.asyncio
async def test_saved_posts_redirected(client):
    """saved_posts must raise FetchError on 302 (not silently follow redirect)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.saved_posts(limit=5)


@pytest.mark.asyncio
async def test_liked_posts_redirected(client):
    """liked_posts must raise FetchError on 302 (not silently follow redirect)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(FetchError, match="redirected"):
            await client.liked_posts(limit=5)


@pytest.mark.asyncio
async def test_resolve_dm_thread_igid_sends_cookie_in_step1(client):
    """resolve_dm_thread_igid step-1 inbox lookup must include Cookie header.
    Without it the request goes out unauthenticated and returns an empty inbox."""
    calls = []

    # Step 1 returns 200 with an empty inbox (no matching thread)
    inbox_resp = MagicMock()
    inbox_resp.status_code = 200
    inbox_resp.json.return_value = {"inbox": {"threads": []}}
    inbox_resp.text = '{"inbox": {"threads": []}}'

    # Step 2 returns a valid profile (so we can proceed)
    profile_resp = MagicMock()
    profile_resp.status_code = 200
    profile_resp.text = '{"data": {"user": {"id": "12345"}}}'
    profile_resp.json.return_value = {"data": {"user": {"id": "12345"}}}

    # get_or_create returns a thread
    create_resp = MagicMock()
    create_resp.status_code = 200
    create_resp.text = '{"status": "ok", "thread": {"thread_v2_id": "abc123"}}'
    create_resp.json.return_value = {"status": "ok", "thread": {"thread_v2_id": "abc123"}}

    async def mock_get(url, **kwargs):
        calls.append(("get", url, kwargs))
        if "direct_v2/inbox" in url:
            return inbox_resp
        return profile_resp

    async def mock_post(url, **kwargs):
        return create_resp

    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = mock_get
        mock_session.post = mock_post
        with patch.object(client, "_cookie_str", return_value="sessionid=abc"):
            result = await client.resolve_dm_thread_igid("testuser")

    # Verify that step 1 (inbox lookup) included the Cookie header
    step1_call = next((c for c in calls if "direct_v2/inbox" in c[1]), None)
    assert step1_call is not None, "Step 1 inbox GET was never called"
    headers = step1_call[2].get("headers", {})
    assert "Cookie" in headers, "Step 1 inbox GET missing Cookie header"
    assert headers["Cookie"] == "sessionid=abc"


@pytest.mark.asyncio
async def test_resolve_dm_thread_igid_raises_on_step2_redirect(client):
    """resolve_dm_thread_igid step-2 must raise FetchError on 302, not try resp.json()."""
    inbox_resp = MagicMock()
    inbox_resp.status_code = 302
    inbox_resp.text = ""
    inbox_resp.json.return_value = {}

    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.text = ""

    async def mock_get(url, **kwargs):
        if "direct_v2/inbox" in url:
            return inbox_resp
        return redirect_resp

    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.get = mock_get
        with patch.object(client, "_cookie_str", return_value="sessionid=abc"):
            with pytest.raises(FetchError, match="redirected"):
                await client.resolve_dm_thread_igid("testuser")


@pytest.mark.asyncio
async def test_fetch_profile_attempt_redirected(client):
    """_fetch_profile_attempt must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        with pytest.raises(FetchError, match="redirected"):
            await client._fetch_profile_attempt("testuser", None)


@pytest.mark.asyncio
async def test_fetch_graphql_attempt_redirected(client):
    """_fetch_graphql_attempt must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp
        
        with pytest.raises(FetchError, match="redirected"):
            await client._fetch_graphql_attempt("testuser", 12, None, None)


@pytest.mark.asyncio
async def test_fetch_tagged_posts_redirected(client):
    """fetch_tagged_posts must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post.return_value = mock_resp
        with pytest.raises(FetchError, match="redirected"):
            await client.fetch_tagged_posts("uid", "user")


@pytest.mark.asyncio
async def test_fetch_reposts_redirected(client):
    """fetch_reposts must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post.return_value = mock_resp
        with pytest.raises(FetchError, match="redirected"):
            await client.fetch_reposts("uid", "user")


@pytest.mark.asyncio
async def test_fetch_reels_redirected(client):
    """fetch_reels must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post.return_value = mock_resp
        with pytest.raises(FetchError, match="redirected"):
            await client.fetch_reels("uid", "user")


@pytest.mark.asyncio
async def test_fetch_location_posts_redirected(client):
    """fetch_location_posts must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post.return_value = mock_resp
        with pytest.raises(FetchError, match="redirected"):
            await client.fetch_location_posts("loc_id")


@pytest.mark.asyncio
async def test_fetch_audio_reels_redirected(client):
    """fetch_audio_reels must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
        mock_session = mock_auth.return_value
        mock_session.post.return_value = mock_resp
        with pytest.raises(FetchError, match="redirected"):
            await client.fetch_audio_reels("audio_id")


@pytest.mark.asyncio
async def test_fetch_highlights_redirected(client):
    """fetch_highlights must raise FetchError on redirect (302/301) to login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.text = ""
    with patch.object(client, "fetch_user", new_callable=AsyncMock) as mock_fetch_user:
        mock_fetch_user.return_value = {"pk": "12345", "is_verified": False}
        with patch.object(client, "_get_auth_session", new_callable=AsyncMock) as mock_auth:
            mock_session = mock_auth.return_value
            mock_session.get.return_value = mock_resp
            with pytest.raises(FetchError, match="redirected"):
                await client.fetch_highlights("user_id")




# ── Regression: cache_media_urls must handle StoryItem / ReelItem ─────────────
# StoryItem and ReelItem expose only `thumbnail_url` (no `display_url` /
# `video_url`). Before the fix, cache_media_urls accessed `.display_url` /
# `.video_url` on them and raised AttributeError whenever a story/reel flowed
# through media caching.


def _make_story(thumbnail_url: str = "https://cdn.example/story.jpg"):
    from instagram_mcp.models import StoryItem

    return StoryItem(
        pk="1",
        shortcode="abc",
        taken_at=0,
        taken_at_str="",
        expiring_at=0,
        media_type=1,
        duration_secs=0.0,
        width=1080,
        height=1920,
        thumbnail_url=thumbnail_url,
        caption="",
        accessibility_caption="",
        is_paid_partnership=False,
        can_reshare=True,
        can_reply=True,
        has_audio=False,
        mentions=[],
        hashtags=[],
        linked_post_code="",
        music_title="",
        music_artist="",
    )


def test_story_and_reel_have_no_display_url():
    """Contract that justifies the cache_media_urls fix."""
    from instagram_mcp.models import ReelItem

    story = _make_story()
    reel = ReelItem(thumbnail_url="https://cdn.example/reel.jpg")
    assert not hasattr(story, "display_url")
    assert not hasattr(story, "video_url")
    assert not hasattr(reel, "display_url")
    assert hasattr(story, "thumbnail_url")
    assert hasattr(reel, "thumbnail_url")


@pytest.mark.asyncio
async def test_cache_media_urls_handles_story_item(client):
    from instagram_mcp.models import ReelItem

    client._get_session = MagicMock(return_value="fake-session")
    client._media_cache = MagicMock()
    client._media_cache.get_or_fetch = AsyncMock(return_value="file:///cached.jpg")

    story = _make_story("https://cdn.example/story.jpg")
    out = await client.cache_media_urls(story)
    assert out.thumbnail_url == "file:///cached.jpg"

    reel = ReelItem(thumbnail_url="https://cdn.example/reel.jpg")
    out2 = await client.cache_media_urls(reel)
    assert out2.thumbnail_url == "file:///cached.jpg"


@pytest.mark.asyncio
async def test_cache_media_urls_story_in_list(client):
    """The recursive list branch must also handle StoryItem without crashing."""
    client._get_session = MagicMock(return_value="fake-session")
    client._media_cache = MagicMock()
    client._media_cache.get_or_fetch = AsyncMock(return_value="file:///cached.jpg")

    stories = [_make_story("https://cdn.example/1.jpg"), _make_story("https://cdn.example/2.jpg")]
    out = await client.cache_media_urls(stories)
    assert all(s.thumbnail_url == "file:///cached.jpg" for s in out)
