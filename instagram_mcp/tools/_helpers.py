"""Shared helpers for the tool submodules.

Ported from the legacy ``instagram_mcp/tools.py`` without behavioural change.
This module is the single source of truth for ``sanitize_username``,
``_tool_error``, ``_exception_to_tool_error``, ``_paginate_feed`` and the
``ToolDescriptor`` shape used by every per-toolset registrar.

The legacy ``instagram_mcp/tools.py`` is kept alongside this package until
task 8.2 retires it; do not remove or modify it from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel

from ..exceptions import InstagramMCPError

if TYPE_CHECKING:
    from ..client import InstagramClient
    from ..config import MCPConfig

    # ``ErrorType`` is introduced by the parallel task 1.2. Importing it under
    # ``TYPE_CHECKING`` keeps this module importable today (where the symbol may
    # not yet exist) while still letting static type checkers narrow
    # ``_tool_error``'s ``error_type`` parameter once the literal lands.
    from ..exceptions import ErrorType  # noqa: F401


# ---------------------------------------------------------------------------
# Public typing primitives
# ---------------------------------------------------------------------------

AuthTier = Literal["anon", "auth", "auto"]
"""Authentication tier of a registered tool.

* ``"anon"``  — works without cookies (🌐).
* ``"auth"``  — requires a valid Instagram session (🔐).
* ``"auto"``  — works anonymously, upgrades when cookies are present (🌐/🔐).
"""


@dataclass(frozen=True)
class ToolDescriptor:
    """Static description of a registered MCP tool.

    Returned by every ``register_<toolset>`` function so the orchestrator,
    annotation auditor and server-instructions builder can introspect the
    registry without re-importing ``FastMCP`` internals.
    """

    name: str
    toolset: str
    auth_tier: AuthTier
    annotations: dict[str, Any]
    input_model: type[BaseModel]
    description_first_line: str = ""


# ---------------------------------------------------------------------------
# Username sanitisation (ported verbatim from tools.py)
# ---------------------------------------------------------------------------

def sanitize_username(raw: str) -> str:
    """Strip whitespace, remove '@', lowercase. Raises ValueError if empty."""
    cleaned = raw.strip().lstrip("@").lower()
    if not cleaned:
        raise ValueError("Username cannot be empty")
    return cleaned


# ---------------------------------------------------------------------------
# ToolError factories (ported verbatim from tools.py)
# ---------------------------------------------------------------------------

def _tool_error(
    msg: str,
    error_type: "ErrorType | str" = "error",
    suggested_action: str = "",
) -> ToolError:
    """Build a ToolError with LLM-friendly structured message.

    ``error_type`` is typed against the ``ErrorType`` literal from
    ``instagram_mcp.exceptions`` once task 1.2 lands; until then static type
    checkers fall back to ``str``.
    """
    parts = [f"❌ **Error** ({error_type}): {msg}"]
    if suggested_action:
        parts.append(f"💡 **Action**: {suggested_action}")
    return ToolError("\n".join(parts))


def _exception_to_tool_error(e: Exception) -> ToolError:
    """Convert any exception to a structured ToolError."""
    if isinstance(e, InstagramMCPError):
        return _tool_error(str(e), e.error_type, e.suggested_action)
    return _tool_error(
        str(e),
        "unexpected_error",
        "Unexpected error. Check server logs or try again.",
    )


# ---------------------------------------------------------------------------
# Shared pagination helper (ported verbatim from tools.py)
# ---------------------------------------------------------------------------

async def _paginate_feed(
    client: "InstagramClient",
    config: "MCPConfig",
    profile,
    max_posts: int,
    max_age_days: int,
    date_range,
    ctx: Context,
) -> tuple:
    """Fetch feed items via v1/feed/user with max_id pagination.

    Reports per-page progress to ``ctx``. Returns ``(items, effective_max)``.
    """
    effective_max = min(max_posts, config.max_pagination_posts)
    since_ts = date_range.since if date_range else None

    await ctx.report_progress(
        0.0,
        float(effective_max),
        message=f"Starting: up to {effective_max} posts...",
    )

    async def _on_page(page_num: int, fetched: int, target: int) -> None:
        pct = min(fetched / target * 100, 100) if target else 0
        msg = f"Page {page_num}: {fetched}/{target} posts ({pct:.0f}%)"
        await ctx.report_progress(float(fetched), float(effective_max), message=msg)
        if page_num > 1:
            await ctx.debug(f"Feed page {page_num} fetched — {fetched} posts so far")

    items = await client.fetch_feed_items(
        user_id=profile.user_id,
        max_posts=effective_max,
        since_timestamp=since_ts,
        page_cb=_on_page,
    )
    await ctx.report_progress(
        float(len(items)),
        float(effective_max),
        message=f"Done: {len(items)} posts fetched",
    )
    return items, effective_max


__all__ = [
    "AuthTier",
    "ToolDescriptor",
    "sanitize_username",
    "_tool_error",
    "_exception_to_tool_error",
    "_paginate_feed",
]
