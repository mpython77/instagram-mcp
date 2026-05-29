"""Instagram client package - maintains backwards compatibility.

Usage:
    from instagram_mcp.client import InstagramClient
"""

from ._base import InstagramClient, _mask_proxy, _caption_insights, CURL_CFFI_AVAILABLE
from ..exceptions import FetchError, AuthError
from ..delay import JitterAsyncSession

__all__ = [
    "InstagramClient",
    "_mask_proxy",
    "_caption_insights",
    "CURL_CFFI_AVAILABLE",
    "FetchError",
    "AuthError",
    "JitterAsyncSession",
]
