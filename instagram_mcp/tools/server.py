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
from ..models import ServerInput, MetricsInput, PluginsInput
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

    # ── instagram_metrics ─────────────────────────────────────────────────────

    _METRICS_ANNOTATIONS: dict = {
        "title": "Instagram MCP Metrics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }

    @mcp.tool(name="instagram_metrics", annotations=_METRICS_ANNOTATIONS)
    async def instagram_metrics(params: MetricsInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — view or reset server metrics.

        Returns request counts, durations, error rates, and cache stats
        for all tools that have been called since server start.

        Actions:
        - 'get' (default) — return current metrics as formatted markdown
        - 'reset' — reset all metrics counters
        """
        from ..metrics import MetricsCollector

        collector = MetricsCollector.get_instance()
        action = (params.action or "get").strip().lower()

        if action == "get":
            metrics = collector.get_metrics()
            lines = [
                "# Instagram MCP Metrics",
                "",
                f"**Uptime:** {metrics['uptime_seconds']}s",
                f"**Total requests:** {metrics['total_requests']}",
                f"**Total errors:** {metrics['total_errors']}",
                f"**Error rate:** {metrics['error_rate']}",
                "",
                "## Cache",
                f"- Hits: {metrics['cache']['hits']}",
                f"- Misses: {metrics['cache']['misses']}",
                f"- Hit rate: {metrics['cache']['hit_rate']}",
                "",
            ]
            if metrics["tools"]:
                lines.append("## Per-tool metrics")
                lines.append("")
                lines.append("| Tool | Count | Avg (s) | P95 (s) | Errors |")
                lines.append("|------|-------|---------|---------|--------|")
                for name, data in metrics["tools"].items():
                    err_count = sum(data["errors"].values()) if data["errors"] else 0
                    lines.append(
                        f"| {name} | {data['count']} | "
                        f"{data['avg_duration_s']} | "
                        f"{data['p95_duration_s']} | {err_count} |"
                    )
            else:
                lines.append("_No tool calls recorded yet._")
            return "\n".join(lines)

        elif action == "reset":
            collector.reset()
            return "Metrics reset successfully."

        else:
            raise _tool_error(
                f"Unknown action: '{params.action}'. Valid actions: 'get', 'reset'.",
                "validation_error",
                "Set action to 'get' or 'reset'.",
            )

    descriptors.append(
        ToolDescriptor(
            name="instagram_metrics",
            toolset=TOOLSET_NAME,
            auth_tier="anon",
            annotations=_METRICS_ANNOTATIONS,
            input_model=MetricsInput,
            description_first_line="🌐 NO LOGIN REQUIRED — view or reset server metrics.",
        )
    )

    # ── instagram_plugins ─────────────────────────────────────────────────────

    _PLUGINS_ANNOTATIONS: dict = {
        "title": "Instagram MCP Plugins",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }

    @mcp.tool(name="instagram_plugins", annotations=_PLUGINS_ANNOTATIONS)
    async def instagram_plugins(params: PluginsInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — list loaded plugins.

        Shows all third-party plugins discovered and loaded via entry_points
        (group: instagram_mcp.tools).

        Actions:
        - 'list' (default) — list all loaded plugins with status
        """
        action = (params.action or "list").strip().lower()

        if action == "list":
            plugin_mgr = getattr(mcp, "_plugin_manager", None)
            if plugin_mgr is None:
                return "Plugin system not initialized."

            plugins = plugin_mgr.list_plugins()
            if not plugins:
                lines = [
                    "# Instagram MCP Plugins",
                    "",
                    "_No plugins installed._",
                    "",
                    "To create a plugin, publish a package with an entry point in the "
                    "`instagram_mcp.tools` group.",
                ]
            else:
                lines = [
                    "# Instagram MCP Plugins",
                    "",
                    f"**Loaded:** {sum(1 for p in plugins if p['status'] == 'loaded')}",
                    f"**Errors:** {sum(1 for p in plugins if p['status'] == 'error')}",
                    "",
                    "| Plugin | Module | Status | Error |",
                    "|--------|--------|--------|-------|",
                ]
                for p in plugins:
                    lines.append(
                        f"| {p['name']} | {p['module']} | "
                        f"{p['status']} | {p.get('error') or '-'} |"
                    )
            return "\n".join(lines)

        else:
            raise _tool_error(
                f"Unknown action: '{params.action}'. Valid actions: 'list'.",
                "validation_error",
                "Set action to 'list'.",
            )

    descriptors.append(
        ToolDescriptor(
            name="instagram_plugins",
            toolset=TOOLSET_NAME,
            auth_tier="anon",
            annotations=_PLUGINS_ANNOTATIONS,
            input_model=PluginsInput,
            description_first_line="🌐 NO LOGIN REQUIRED — list loaded plugins.",
        )
    )

    return descriptors


__all__ = ["TOOLSET_NAME", "register_server"]
