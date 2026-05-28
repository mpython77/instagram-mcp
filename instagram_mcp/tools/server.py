"""Server toolset — diagnostics and cache management.

Always-on submodule: invoked by the orchestrator regardless of
``MCPConfig.enabled_toolsets`` (Requirement 4.3). Hosts a single tool,
``instagram_server``, which is anonymous (🌐) — diagnostics, cache
flush and cookie reload do not require an authenticated Instagram
session. The body below is ported verbatim from the legacy
``instagram_mcp/tools.py`` (lines 1187-1281); only the closure host
changed (per-toolset ``register_server`` instead of the monolithic
``register_tools``). Logic, error handling, progress reporting and
diagnostics formatting are unchanged.

Validates: Requirements 1.2, 2.1–2.5, 4.3, 5.1–5.3, 8.1, 8.3.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context

from ..formatter import format_diagnostics_markdown
from ..models import ServerInput
from ._helpers import (
    ToolDescriptor,
    _tool_error,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger("instagram_mcp.tools.server")

TOOLSET_NAME = "server"


# Annotation dict — passed verbatim to both ``@mcp.tool(annotations=...)`` and
# the matching ``ToolDescriptor`` so the audit can verify parity. The
# ``instagram_server`` tool is read-only with respect to Instagram (it never
# touches the platform); ``clear_cache`` / ``clear_user`` only mutate local
# in-process state and ``reload_cookies`` re-reads an existing file. It is not
# enumerated in the audit's DESTRUCTIVE_TOOLS set, so ``readOnlyHint=True`` is
# the correct declaration for this server-management tool.
_SERVER_ANNOTATIONS: dict = {
    "title": "Instagram MCP Server Management",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def register_server(
    mcp: "FastMCP",
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register the server toolset.

    Always invoked by the orchestrator regardless of
    ``MCPConfig.enabled_toolsets`` (Requirement 4.3). Returns the list of
    :class:`ToolDescriptor` entries describing each registered tool so the
    orchestrator can assemble ``mcp._instagram_tool_inventory`` and run the
    annotation audit.
    """
    descriptors: list[ToolDescriptor] = []

    @mcp.tool(name="instagram_server", annotations=_SERVER_ANNOTATIONS)
    async def instagram_server(params: ServerInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — server management, no Instagram session needed.

        Server diagnostics and cache management for the Instagram MCP server.

        Actions:
        - 'status' (default) — shows cache hit rate, entries used/max, hits,
          misses, evictions; proxy health (active count, per-proxy latency and
          success rate, cooldown status); rate limiter state (current RPS,
          burst tokens, circuit breaker).
        - 'clear_cache' — flushes all cached data. Next requests will hit the
          Instagram API and repopulate the cache.
        - 'clear_user' — flushes cache for a single username only (requires
          username parameter). Useful after an account changes its bio, username,
          or posts.

        Args:
            params: action ('status' | 'clear_cache' | 'clear_user'),
                    username (required for 'clear_user', ignored otherwise)
        """
        action = (params.action or "status").strip().lower()

        if action == "status":
            await ctx.info("Collecting server diagnostics...")
            try:
                cache_stats = await client.cache.stats()
                proxy_statuses = await client.proxy_manager.get_all_status()
                proxy_summary = client.proxy_manager.stats
                rate_stats = client.rate_limiter.stats
                return format_diagnostics_markdown(cache_stats, proxy_statuses, proxy_summary, rate_stats)
            except Exception as e:
                raise _tool_error(f"Failed to collect diagnostics: {e}", "unexpected_error")

        elif action == "clear_cache":
            try:
                count = await client.cache.clear()
                await ctx.info(f"Full cache flush: {count} entries removed")
                return f"✅ All cache cleared ({count} entries removed). Next requests will fetch fresh data."
            except Exception as e:
                raise _tool_error(f"Cache clear failed: {e}", "unexpected_error")

        elif action == "clear_user":
            username_raw = (params.username or "").strip().lstrip("@").lower()
            if not username_raw:
                raise _tool_error(
                    "action='clear_user' requires a username.",
                    "validation_error",
                    "Set the username parameter to the Instagram username to clear.",
                )
            try:
                count = await client.cache.invalidate_prefix(f"user:{username_raw}")
                await ctx.info(f"Cache cleared for @{username_raw}: {count} entries removed")
                return f"✅ Cache cleared for @{username_raw} ({count} entries removed)."
            except Exception as e:
                raise _tool_error(f"Cache clear failed for @{username_raw}: {e}", "unexpected_error")

        elif action == "reload_cookies":
            try:
                cm = client.cookie_manager
                if cm is None:
                    return "⚠️ No CookieManager attached to this server instance."
                ok = cm.load()
                # Reset cached auth session so it's recreated with fresh cookies
                async with client._auth_session_lock:
                    if client._auth_session is not None:
                        await client._auth_session.close()
                        client._auth_session = None
                if ok:
                    return "✅ Cookies reloaded successfully — sessionid found, authenticated."
                else:
                    return "⚠️ Cookies reloaded but no valid sessionid found — check your cookies file."
            except Exception as e:
                raise _tool_error(f"Cookie reload failed: {e}", "unexpected_error")

        else:
            raise _tool_error(
                f"Unknown action: '{params.action}'. Valid actions: 'status', 'clear_cache', 'clear_user', 'reload_cookies'.",
                "validation_error",
                "Set action to one of: 'status', 'clear_cache', 'clear_user', 'reload_cookies'.",
            )

    descriptors.append(
        ToolDescriptor(
            name="instagram_server",
            toolset=TOOLSET_NAME,
            auth_tier="anon",
            annotations=_SERVER_ANNOTATIONS,
            input_model=ServerInput,
            description_first_line="🌐 NO LOGIN REQUIRED — server management, no Instagram session needed.",
        )
    )

    return descriptors


__all__ = ["TOOLSET_NAME", "register_server"]
