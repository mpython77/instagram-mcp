"""Shared utilities for the client package."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse, urlunparse


def _mask_proxy(url: Optional[str]) -> str:
    """Mask credentials in a proxy URL for safe logging."""
    if not url:
        return "direct"
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = f"***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return url
