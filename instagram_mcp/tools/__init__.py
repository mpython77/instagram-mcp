"""Tools package — orchestrator for the eight per-toolset registrars.

This module wires together every domain submodule (profile, analysis, content,
social_graph, dm, upload, automation, server). It does not declare any
``@mcp.tool`` decorations of its own; each submodule owns its tools and exposes
a registrar with the four-positional contract described in design Section 3.

Public API (re-exported from this module):
    register_tools  — orchestrator entry point used by ``create_mcp_server``
    sanitize_username
    _tool_error
    _exception_to_tool_error
    ToolDescriptor

Validates: Requirements 1.5, 1.6, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING

from . import (
    profile,
    analysis,
    content,
    social_graph,
    dm,
    upload,
    automation,
    audience,
    server as server_module,  # avoid shadowing the `mcp` server arg
)
from ._helpers import (
    AuthTier,
    ToolDescriptor,
    sanitize_username,
    _tool_error,
    _exception_to_tool_error,
)

# ---------------------------------------------------------------------------
# Backwards-compatibility re-exports
# ---------------------------------------------------------------------------
#
# The legacy `instagram_mcp/tools.py` re-exported a handful of helpers that
# tests and downstream callers may still import from `instagram_mcp.tools`.
# Re-export them here so existing test modules keep working without
# modification.
from .automation import BatchScrapeInput  # noqa: F401  (re-export)
from ..parser import (  # noqa: F401  (re-export)
    parse_post_html,
    parse_comments,
    parse_feed_items,
    parse_profile,
    parse_reels_edges,
    parse_repost_items,
    parse_tagged_tab_edges,
    shortcode_to_media_id,
    check_dead_account,
    check_dead_account_from_items,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger("instagram_mcp.tools")


# Canonical order in which submodules are invoked. The server submodule is
# placed last but is ALWAYS invoked regardless of MCPConfig.enabled_toolsets
# (Requirement 4.3).
CANONICAL_ORDER: tuple[str, ...] = (
    "profile",
    "analysis",
    "content",
    "social_graph",
    "dm",
    "upload",
    "automation",
    "audience",
    "server",
)

# Map canonical toolset name → (registrar function, module). Used by
# `register_tools` to dispatch in canonical order.
_REGISTRARS = {
    "profile":      profile.register_profile,
    "analysis":     analysis.register_analysis,
    "content":      content.register_content,
    "social_graph": social_graph.register_social_graph,
    "dm":           dm.register_dm,
    "upload":       upload.register_upload,
    "automation":   automation.register_automation,
    "audience":     audience.register_audience,
    "server":       server_module.register_server,
}

# Legacy alias — older configs may still set INSTAGRAM_MCP_TOOLSETS=batch.
LEGACY_ALIASES: dict[str, str] = {"batch": "automation"}


def _resolve_enabled_toolsets(config) -> set[str]:
    """Translate ``MCPConfig.enabled_toolsets`` into a set of canonical toolset
    names that should be invoked. Always includes ``server`` (Requirement 4.3).

    Rules:
      - Empty set or set containing "all" → every toolset.
      - Otherwise: only the listed toolsets, plus legacy aliases mapped via
        ``LEGACY_ALIASES`` (e.g. "batch" → "automation"), plus "server".
    """
    raw = set(getattr(config, "enabled_toolsets", set()) or set())
    if not raw or "all" in raw:
        return set(CANONICAL_ORDER)

    resolved: set[str] = set()
    for name in raw:
        canon = LEGACY_ALIASES.get(name, name)
        if canon in _REGISTRARS:
            resolved.add(canon)
    resolved.add("server")  # always invoked
    return resolved


def _log_inventory_summary(inventory: list[ToolDescriptor]) -> None:
    """Log INFO-level total count and per-toolset counts."""
    by_toolset: Counter[str] = Counter(d.toolset for d in inventory)
    by_tier: Counter[str] = Counter(d.auth_tier for d in inventory)
    logger.info(
        "instagram_mcp.tools: registered %d tools — toolsets=%s, tiers=%s",
        len(inventory),
        dict(sorted(by_toolset.items())),
        dict(sorted(by_tier.items())),
    )


def register_tools(mcp, client, config, exporter) -> None:
    """Register every enabled toolset against ``mcp``.

    The orchestrator:
      1. Resolves which toolsets to enable from ``config``.
      2. Invokes each registrar in canonical order.
      3. Collects every returned ``ToolDescriptor`` into a single list.
      4. Stores the list on ``mcp._instagram_tool_inventory`` (Requirement 3.3).
      5. Logs an INFO-level summary.
      6. If the ``server`` registrar fails, logs ERROR and continues in
         degraded mode (Requirement 4.4).

    Per Requirement 3.4 this function MUST NOT contain inline ``@mcp.tool``
    declarations.
    """
    enabled = _resolve_enabled_toolsets(config)
    inventory: list[ToolDescriptor] = []

    for toolset in CANONICAL_ORDER:
        if toolset not in enabled:
            continue
        registrar = _REGISTRARS[toolset]
        try:
            descriptors = registrar(mcp, client, config, exporter) or []
        except Exception as exc:  # noqa: BLE001
            if toolset == "server":
                logger.error(
                    "Server toolset registration failed: %s — continuing in degraded mode",
                    exc,
                    exc_info=True,
                )
                continue
            raise

        if not isinstance(descriptors, list):
            raise TypeError(
                f"register_{toolset} must return list[ToolDescriptor], got {type(descriptors).__name__}"
            )
        inventory.extend(descriptors)

    mcp._instagram_tool_inventory = inventory  # type: ignore[attr-defined]
    _log_inventory_summary(inventory)


__all__ = [
    "CANONICAL_ORDER",
    "LEGACY_ALIASES",
    "register_tools",
    "sanitize_username",
    "_tool_error",
    "_exception_to_tool_error",
    "ToolDescriptor",
    "AuthTier",
    # Backwards-compat re-exports (legacy tools.py surface)
    "BatchScrapeInput",
    "parse_post_html",
    "parse_comments",
    "parse_feed_items",
    "parse_profile",
    "parse_reels_edges",
    "parse_repost_items",
    "parse_tagged_tab_edges",
    "shortcode_to_media_id",
    "check_dead_account",
    "check_dead_account_from_items",
]
