import pytest
from instagram_mcp.exceptions import (
    InstagramMCPError,
    AuthError,
    UserNotFoundError,
    PostNotFoundError,
    RateLimitError,
    PrivateAccountError,
    FetchError,
    ProxyError,
    ConfigError,
    AccountSuspendedError,
    _mask_proxy_url,
)

def test_instagram_mcp_error():
    err = InstagramMCPError("base error")
    assert str(err) == "base error"
    assert err.error_type == "unknown_error"

def test_user_not_found_error():
    err1 = UserNotFoundError(username="testuser")
    assert "testuser" in str(err1)
    
    err2 = UserNotFoundError(message="custom message")
    assert str(err2) == "custom message"

def test_post_not_found_error():
    err1 = PostNotFoundError(shortcode="ABC")
    assert "ABC" in str(err1)
    
    err2 = PostNotFoundError()
    assert "Post not found" in str(err2)

    err3 = PostNotFoundError(message="custom message")
    assert str(err3) == "custom message"

def test_rate_limit_error():
    err1 = RateLimitError()
    assert "Rate limited" in str(err1)
    assert err1.retry_after is None
    
    err2 = RateLimitError(retry_after=10.5)
    assert "Retry after 10s" in str(err2)
    assert err2.retry_after == 10.5
    
    err3 = RateLimitError(message="custom message", retry_after=5)
    assert str(err3) == "custom message"

def test_private_account_error():
    err1 = PrivateAccountError(username="testuser")
    assert "testuser" in str(err1)
    
    err2 = PrivateAccountError(message="custom message")
    assert str(err2) == "custom message"

def test_fetch_error():
    err = FetchError("fetch error")
    assert str(err) == "fetch error"

def test_proxy_error():
    err1 = ProxyError(proxy_url="http://user:pass@127.0.0.1:8080")
    assert "***:***@127.0.0.1:8080" in str(err1)
    assert err1.proxy_url == "http://user:pass@127.0.0.1:8080"

    err2 = ProxyError()
    assert "Proxy error." in str(err2)
    
    err3 = ProxyError(message="custom message")
    assert str(err3) == "custom message"

def test_config_error():
    err = ConfigError("config error")
    assert str(err) == "config error"

def test_account_suspended_error():
    err1 = AccountSuspendedError(username="testuser")
    assert "testuser" in str(err1)
    
    err2 = AccountSuspendedError()
    assert "Account is suspended" in str(err2)
    
    err3 = AccountSuspendedError(message="custom message")
    assert str(err3) == "custom message"

def test_mask_proxy_url():
    # Valid url with auth
    assert _mask_proxy_url("http://user:pass@127.0.0.1:8080") == "http://***:***@127.0.0.1:8080"
    
    # Valid url with auth but no port
    assert _mask_proxy_url("http://user:pass@127.0.0.1") == "http://***:***@127.0.0.1"

    # Valid url without auth
    assert _mask_proxy_url("http://127.0.0.1:8080") == "<proxy>"
    
    # Empty string or invalid
    assert _mask_proxy_url("") == "<proxy>"
    
    # To cover exception block
    assert _mask_proxy_url(123) == "<proxy>"


def test_auth_error():
    err = AuthError()
    assert err.error_type == "auth_required"
    assert "cookies.json" in err.suggested_action
    assert isinstance(err, InstagramMCPError)

