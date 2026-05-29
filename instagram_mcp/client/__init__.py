"""Instagram client package - maintains backwards compatibility.

Usage:
    from instagram_mcp.client import InstagramClient
"""

from ._base import InstagramClient, CURL_CFFI_AVAILABLE
from ._content import _caption_insights
from ._utils import _mask_proxy
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
