"""
Custom exception types — specific type for each error.

For LLM, each exception contains:
  - error_type (str): Machine identifier
  - suggested_action (str): Recommendation for next step for LLM
"""

from __future__ import annotations

from typing import Optional


class InstagramMCPError(Exception):
    """Base class for all Instagram MCP errors."""

    error_type: str = "unknown_error"
    suggested_action: str = "Try again or check configuration."

    def __init__(self, message: str = "", **kwargs):
        self.message = message
        super().__init__(message)


class UserNotFoundError(InstagramMCPError):
    """Username not found (404) or deleted."""

    error_type = "not_found"
    suggested_action = (
        "Verify the username is correct. "
        "The account may have been deleted or the username changed."
    )

    def __init__(self, username: str = "", message: str = "", **kwargs):
        self.username = username
        if not message:
            message = (
                f"User '@{username}' not found. "
                "The account may be deleted, renamed, or temporarily unavailable."
            )
        super().__init__(message, **kwargs)


class PostNotFoundError(InstagramMCPError):
    """Post shortcode not found (404) or deleted."""

    error_type = "post_not_found"
    suggested_action = (
        "Verify the post shortcode or URL is correct. "
        "The post may have been deleted or the account may be private."
    )

    def __init__(self, shortcode: str = "", message: str = "", **kwargs):
        self.shortcode = shortcode
        if not message:
            s = f" '{shortcode}'" if shortcode else ""
            message = (
                f"Post{s} not found. "
                "It may have been deleted or the account is private."
            )
        super().__init__(message, **kwargs)


class RateLimitError(InstagramMCPError):
    """Instagram rate limit (429) — after all retries are exhausted."""

    error_type = "rate_limited"
    suggested_action = (
        "Wait 1-2 minutes before retrying. "
        "If using bulk operations, reduce concurrency. "
        "Consider adding proxy URLs for better throughput."
    )

    def __init__(
        self,
        message: str = "",
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        self.retry_after = retry_after
        if not message:
            if retry_after is not None:
                message = f"Rate limited. Retry after {retry_after:.0f}s."
            else:
                message = "Rate limited. Wait before retrying."
        super().__init__(message, **kwargs)


class PrivateAccountError(InstagramMCPError):
    """Private account — feed data is not visible."""

    error_type = "private_account"
    suggested_action = (
        "This account is private. Only basic profile info is available. "
        "Feed tags and post details cannot be extracted."
    )

    def __init__(self, username: str = "", message: str = "", **kwargs):
        self.username = username
        if not message:
            message = (
                f"Account '@{username}' is private. "
                "Cannot access feed without following."
            )
        super().__init__(message, **kwargs)


class AuthError(InstagramMCPError):
    """Authentication required — no valid cookies loaded."""

    error_type = "auth_required"
    suggested_action = (
        "This tool requires an authenticated Instagram session. "
        "Export your cookies from a logged-in Instagram browser session "
        "and save them as cookies.json in the project directory. "
        "See README → Authentication for setup instructions."
    )


class FetchError(InstagramMCPError):
    """Network / timeout / general fetch error."""

    error_type = "fetch_error"
    suggested_action = (
        "Check network connectivity. "
        "If using a proxy, verify it is working. "
        "Try again in a few seconds."
    )


class ProxyError(InstagramMCPError):
    """Proxy related error — all proxies down/invalid."""

    error_type = "proxy_error"
    suggested_action = (
        "All configured proxies are currently unavailable. "
        "The system will fall back to direct connection if enabled. "
        "Check proxy URLs and their availability."
    )

    def __init__(self, proxy_url: str = "", message: str = "", **kwargs):
        self.proxy_url = proxy_url
        if not message:
            if proxy_url:
                # Mask credentials in the URL for safe logging
                masked = _mask_proxy_url(proxy_url)
                message = f"Proxy error (proxy: {masked}). All configured proxies are unavailable."
            else:
                message = "Proxy error. All configured proxies are unavailable."
        super().__init__(message, **kwargs)


class ConfigError(InstagramMCPError):
    """Configuration error."""

    error_type = "config_error"
    suggested_action = (
        "Check environment variables and configuration. "
        "Refer to README for proper setup."
    )


class AccountSuspendedError(InstagramMCPError):
    """Instagram returned account suspension indicators."""

    error_type = "account_suspended"
    suggested_action = (
        "The account appears to be suspended by Instagram. "
        "No further requests can be made for this account until it is reinstated."
    )

    def __init__(self, username: str = "", message: str = "", **kwargs):
        self.username = username
        if not message:
            suffix = f" '@{username}'" if username else ""
            message = (
                f"Account{suffix} is suspended. "
                "Instagram has flagged this account as unavailable."
            )
        super().__init__(message, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_proxy_url(url: str) -> str:
    """Replace user:password in a proxy URL with '***:***' for safe display."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = f"***:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            masked = urlunparse(parsed._replace(netloc=netloc))
            return masked
    except Exception:
        pass
    return "<proxy>"
