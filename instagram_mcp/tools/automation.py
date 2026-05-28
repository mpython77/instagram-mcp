"""Automation toolset — batch scraping, scheduling, monitoring, sessions, OAuth.

``instagram_batch_scrape`` is 🌐 anon and is registered unconditionally.
``instagram_schedule``, ``instagram_monitor``, ``instagram_sessions`` and
``instagram_oauth`` are 🔐 auth and are gated by ``MCPConfig`` per Requirement
4.5–4.6. Lazy imports of ``scheduler``, ``monitor``, ``oauth_manager`` and
``session_manager`` are performed inside tool function bodies — they MUST NOT
appear at module top level (Requirement 20.2). Tool function bodies are
preserved byte-for-byte from the legacy ``instagram_mcp/tools.py``; only the
enclosing closure has changed.

Validates: Requirements 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3,
8.1, 8.3, 17.2, 20.2.
"""

from __future__ import annotations

import logging
import tempfile
import time
from typing import List

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from ..formatter import (
    format_monitor_markdown,
    format_oauth_markdown,
    format_schedule_markdown,
    format_sessions_markdown,
)
from ..models import (
    MonitorInput,
    OAuthInput,
    ScheduleInput,
    SessionInput,
)
from ._helpers import (
    ToolDescriptor,
    _exception_to_tool_error,
    _tool_error,
    sanitize_username,
)

logger = logging.getLogger("instagram_mcp.tools.automation")

TOOLSET_NAME = "automation"


# ═════════════════════════════════════════════════════════════════════════════
# BATCH SCRAPE INPUT (ported verbatim from legacy tools.py)
# ═════════════════════════════════════════════════════════════════════════════

class BatchScrapeInput(BaseModel):
    """Input for large-scale batch scraping of Instagram profiles."""

    targets: List[str] = Field(
        ...,
        description="Instagram usernames to scrape (max 2000, without @).",
        min_length=1,
        max_length=2000,
    )
    since_date: str = Field(
        default="",
        description="Include only posts after this date (DD.MM.YYYY). Leave empty for no lower bound.",
    )
    until_date: str = Field(
        default="",
        description="Include only posts before this date (DD.MM.YYYY). Leave empty for no upper bound.",
    )
    max_workers: int = Field(
        default=20,
        description="Parallel workers (1-100). Default 20. 50-100 is safe with healthy proxies; 100 is safe in profile_only mode.",
        ge=1,
        le=100,
    )
    max_posts_per_profile: int = Field(
        default=50,
        description="Maximum posts to fetch per profile (1-500). Higher = richer data but slower. Ignored when profile_only=True.",
        ge=1,
        le=500,
    )
    use_cookies: bool = Field(
        default=False,
        description="Use cookies for authenticated requests.",
    )
    output_file: str = Field(
        default="",
        description="File path to save full JSON results. Leave empty to use a temp file.",
    )
    profile_only: bool = Field(
        default=False,
        description="If True, fetches ONLY profile metadata (no posts/feed). 30-60x faster for bulk follower/bio scraping. Dead detection falls back to posts_count==0.",
    )
    stream_jsonl: bool = Field(
        default=True,
        description="If True (default), append each completed profile to output_file+'.jsonl' in real time (atomic, append-only). Memory-safe for huge batches; tail -f friendly. Set False to disable.",
    )


# ═════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL HELPERS (ported verbatim from legacy tools.py)
# ═════════════════════════════════════════════════════════════════════════════

def _parse_publish_at(value: str) -> int:
    """Parse a human-readable or timestamp string to Unix timestamp."""
    from datetime import datetime, timezone

    value = value.strip()
    if value.isdigit():
        return int(value)

    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise ValueError(
        f"Cannot parse publish_at value: {value!r}. "
        "Use ISO format like '2026-05-20T15:00:00' or Unix timestamp."
    )


# ═════════════════════════════════════════════════════════════════════════════
# REGISTRAR
# ═════════════════════════════════════════════════════════════════════════════

def register_automation(mcp, client, config, exporter) -> list[ToolDescriptor]:
    """Register every automation-domain tool with ``mcp`` and return a
    :class:`ToolDescriptor` per registered tool.

    ``instagram_batch_scrape`` is registered unconditionally (anon tier).
    The four auth-tier tools (``instagram_schedule``, ``instagram_monitor``,
    ``instagram_sessions``, ``instagram_oauth``) are skipped when
    ``config.hide_auth_when_no_cookies`` is True and no cookies are loaded.
    """

    descriptors: list[ToolDescriptor] = []

    cookie_manager = getattr(client, "cookie_manager", None)
    is_authed = bool(cookie_manager and getattr(cookie_manager, "is_authenticated", False))

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_batch_scrape (🌐 anon — always registered)
    # ─────────────────────────────────────────────────────────────────────
    _batch_scrape_annotations = {
        "title": "Instagram Batch Profile Scraper",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }

    @mcp.tool(
        name="instagram_batch_scrape",
        annotations=_batch_scrape_annotations,
    )
    async def instagram_batch_scrape(params: BatchScrapeInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Scrape up to 2000 Instagram profiles with profile info, feed tags, and
        dead-account detection. Async, high-concurrency, with resume support.

        SPEED MODES (choose the right one for your use case):
          ┌──────────────────────────────────────────────────────────────────┐
          │ profile_only=True  ⚡ TURBO MODE — 30-60x faster                 │
          │   • No feed fetch, just profile metadata                         │
          │   • Best for: bulk follower counts, bio scraping, dead-check    │
          │   • Safe to use max_workers=100                                 │
          │   • 1000 profiles ≈ 30-60s with healthy proxies                 │
          ├──────────────────────────────────────────────────────────────────┤
          │ profile_only=False (default) — full feed analysis               │
          │   • Profile + N posts + tags + dead detection                   │
          │   • Use max_workers=20-50 to avoid rate limits                  │
          │   • 500 profiles × 50 posts ≈ 5-10min with healthy proxies     │
          └──────────────────────────────────────────────────────────────────┘

        WORKER GUIDANCE:
          • 1 proxy or direct connection:  max_workers=10-20
          • 5-10 proxies:                  max_workers=30-50
          • 20+ proxies:                   max_workers=50-100
          • profile_only mode tolerates higher concurrency than full mode.

        OUTPUT FORMATS:
          • output_file=<path>.json  — final aggregated JSON (always written)
          • stream_jsonl=True        — also append each profile to <path>.jsonl
                                       live (tail -f friendly, never truncated)

        RESUME: re-run with the same output_file to skip already-done usernames.
        FAIL-FAST: auto-aborts if >60% error rate after 50 completions
                   (likely IP-banned or proxy dead — saves the partial output).

        ⚠️  LIMIT: max 2000 targets per call. For 5000+, split into batches
        with the same output_file (resume kicks in automatically).

        Args:
            params: targets (list, max 2000), max_workers (1-100, default 20),
                    max_posts_per_profile (1-500, default 50),
                    since_date / until_date (DD.MM.YYYY),
                    use_cookies, output_file,
                    profile_only (default False — TURBO MODE if True),
                    stream_jsonl (default True — live append to .jsonl)
        """
        import os as _os

        sanitized: List[str] = []
        for raw in params.targets:
            try:
                sanitized.append(sanitize_username(raw))
            except ValueError:
                pass
        if not sanitized:
            raise _tool_error("All provided usernames are empty or invalid.", "validation_error", "Provide at least one valid username.")

        if params.since_date and params.until_date:
            from datetime import datetime as _datetime, timezone as _timezone
            try:
                _since_dt = _datetime.strptime(params.since_date.strip(), "%d.%m.%Y").replace(tzinfo=_timezone.utc)
                _until_dt = _datetime.strptime(params.until_date.strip(), "%d.%m.%Y").replace(tzinfo=_timezone.utc)
            except ValueError as e:
                raise _tool_error(
                    f"Invalid date format: {e}. Use DD.MM.YYYY (e.g. 01.03.2026).",
                    "validation_error",
                    "Correct the date format and try again.",
                )
            if _since_dt > _until_dt:
                raise _tool_error(
                    f"since_date ({params.since_date}) is after until_date ({params.until_date}). "
                    "The start date must be earlier than or equal to the end date.",
                    "validation_error",
                    "Swap since_date and until_date so the range goes from earlier to later.",
                )

        await ctx.info(f"instagram_batch_scrape: {len(sanitized)} profiles, {params.max_workers} workers")
        _t0 = time.perf_counter()

        from ..batch_runner import BatchConfig, BatchRunner

        _tmp_file = None
        if params.output_file:
            output_file = params.output_file
        else:
            _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, prefix="ig_batch_")
            _tmp.close()
            output_file = _tmp.name
            _tmp_file = output_file

        targets_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="ig_targets_")
        targets_tmp.write("\n".join(sanitized))
        targets_tmp.close()

        try:
            async def _batch_progress(completed: int, total: int, msg: str) -> None:
                await ctx.info(msg)
                await ctx.report_progress(float(completed), float(total), message=msg)

            batch_cfg = BatchConfig(
                targets_file=targets_tmp.name,
                output_file=output_file,
                max_workers=params.max_workers,
                max_posts=params.max_posts_per_profile,
                since_date=params.since_date,
                until_date=params.until_date,
                use_cookies=params.use_cookies,
                save_every=max(10, len(sanitized) // 10) if len(sanitized) >= 10 else len(sanitized),
                profile_only=params.profile_only,
                stream_jsonl=params.stream_jsonl,
                fail_fast_threshold=0.6,   # auto-abort at 60% error rate after 50 samples
                fail_fast_min_samples=50,
            )
            runner = BatchRunner(batch_cfg, client, progress_cb=_batch_progress)

            try:
                stats = await runner.run()
            except Exception as e:
                raise _tool_error(f"Batch scrape failed: {e}", "batch_error", "Check logs or reduce max_workers.")

        finally:
            try:
                _os.unlink(targets_tmp.name)
            except Exception:
                pass

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"Batch ✓ — {stats.completed}/{stats.total}, {stats.rate:.1f}/s — {elapsed:.1f}s")

        lines = [
            "## Instagram Batch Scrape Results",
            "",
            f"**Total targets:** {stats.total}",
            f"**Completed:** {stats.completed}",
            "",
            "| Status | Count |",
            "|--------|------:|",
            f"| ✅ Active | {stats.active} |",
            f"| ❌ Not Found | {stats.not_found} |",
            f"| 🔒 Private | {stats.private} |",
            f"| 💀 Dead | {stats.dead} |",
            f"| ⚠️ Error | {stats.error} |",
            "",
            f"**Rate:** {stats.rate:.1f} profiles/s",
            f"**Elapsed:** {stats.elapsed_seconds:.1f}s",
        ]
        if params.since_date or params.until_date:
            lines.append(f"**Date filter:** {params.since_date or '*'} → {params.until_date or '*'}")
        if params.output_file:
            lines.append(f"**Output saved to:** `{params.output_file}`")
        elif _tmp_file:
            lines.append(f"**Temp output:** `{_tmp_file}` *(set output_file to persist)*")

        await exporter.save("batch_scrape", f"batch_{len(sanitized)}", {
            "stats": {
                "total": stats.total,
                "completed": stats.completed,
                "active": stats.active,
                "not_found": stats.not_found,
                "private": stats.private,
                "dead": stats.dead,
                "error": stats.error,
                "rate": round(stats.rate, 2),
                "elapsed_seconds": round(stats.elapsed_seconds, 1),
            },
            "output_file": output_file,
            "targets": sanitized,
            "date_filter": {
                "since": params.since_date or None,
                "until": params.until_date or None,
            },
        }, elapsed)

        return "\n".join(lines)

    descriptors.append(
        ToolDescriptor(
            name="instagram_batch_scrape",
            toolset=TOOLSET_NAME,
            auth_tier="anon",
            annotations=_batch_scrape_annotations,
            input_model=BatchScrapeInput,
            description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # AUTH-TIER TOOLS (gated by hide_auth_when_no_cookies)
    # ─────────────────────────────────────────────────────────────────────
    if config.hide_auth_when_no_cookies and not is_authed:
        return descriptors

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_schedule (🔐 auth)
    # ─────────────────────────────────────────────────────────────────────
    _schedule_annotations = {
        "title": "Instagram Post Scheduler",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }

    @mcp.tool(
        name="instagram_schedule",
        annotations=_schedule_annotations,
    )
    async def instagram_schedule(params: ScheduleInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED for 'add' — Schedule posts for future publishing.

        Actions:
          add    — queue a post to be published at a specific date/time
          list   — view all pending scheduled posts
          cancel — cancel a pending post by ID
          status — show scheduler health

        Scheduled posts are stored locally and published automatically
        when their scheduled time arrives (checked every 60 seconds).

        Args:
            params: action, images, caption, publish_at, post_id
        """
        from ..scheduler import PostScheduler
        import os as _os

        scheduler: PostScheduler = getattr(mcp, "_post_scheduler", None)  # type: ignore[attr-defined]
        if scheduler is None:
            raise _tool_error(
                "Scheduler not initialized.",
                "config_error",
                "Restart the server — the scheduler should start automatically.",
            )

        action = params.action.lower().strip()
        await ctx.info(f"instagram_schedule: action={action}")

        try:
            if action == "add":
                if not params.images:
                    raise _tool_error("images required for action='add'", "validation_error")
                if not params.publish_at:
                    raise _tool_error("publish_at required for action='add'", "validation_error")

                # Parse publish_at
                publish_ts = _parse_publish_at(params.publish_at)

                entry = await scheduler.add(
                    images=params.images,
                    caption=params.caption,
                    publish_at=publish_ts,
                    location=params.location,
                )
                return format_schedule_markdown("add", entry)

            elif action == "list":
                pending = await scheduler.list_pending()
                return format_schedule_markdown("list", {"pending": pending})

            elif action == "cancel":
                if not params.post_id:
                    raise _tool_error("post_id required for action='cancel'", "validation_error")
                removed = await scheduler.cancel(params.post_id)
                return format_schedule_markdown("cancel", {"removed": removed, "post_id": params.post_id})

            elif action == "status":
                stats = scheduler.stats()
                return format_schedule_markdown("status", stats)

            else:
                raise _tool_error(
                    f"Unknown action '{action}'",
                    "validation_error",
                    "Valid actions: add, list, cancel, status",
                )
        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_schedule",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=_schedule_annotations,
            input_model=ScheduleInput,
            description_first_line="🔐 AUTH REQUIRED for 'add' — Schedule posts for future publishing.",
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_monitor (🔐 auth)
    # ─────────────────────────────────────────────────────────────────────
    _monitor_annotations = {
        "title": "Instagram Account Monitor",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }

    @mcp.tool(
        name="instagram_monitor",
        annotations=_monitor_annotations,
    )
    async def instagram_monitor(params: MonitorInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Monitor Instagram accounts for new posts via webhook.

        Polls accounts at a configurable interval and sends an HTTP POST
        to your webhook URL when new content is detected.

        Actions:
          add    — start monitoring an account (webhook URL required)
          remove — stop monitoring an account
          list   — view all active monitors
          status — show monitor service health
          test   — send a test webhook to verify your URL works

        Webhook payload:
          {event, username, shortcode, post_url, caption, likes, timestamp, detected_at}

        Args:
            params: action, username, webhook_url, interval (60-3600s)
        """
        from ..monitor import AccountMonitor

        monitor: AccountMonitor = getattr(mcp, "_account_monitor", None)  # type: ignore[attr-defined]
        if monitor is None:
            raise _tool_error(
                "Monitor service not initialized.",
                "config_error",
                "Restart the server — the monitor should start automatically.",
            )

        action = params.action.lower().strip()
        await ctx.info(f"instagram_monitor: action={action}")

        try:
            if action == "add":
                username = params.username.strip().lstrip("@").lower()
                if not username:
                    raise _tool_error("username required for action='add'", "validation_error")
                if not params.webhook_url:
                    raise _tool_error("webhook_url required for action='add'", "validation_error")
                entry = await monitor.add(
                    username=username,
                    webhook_url=params.webhook_url,
                    interval=params.interval,
                )
                return format_monitor_markdown("add", entry)

            elif action == "remove":
                username = params.username.strip().lstrip("@").lower()
                if not username:
                    raise _tool_error("username required for action='remove'", "validation_error")
                removed = monitor.remove(username)
                return format_monitor_markdown("remove", {"removed": removed, "username": username})

            elif action == "list":
                entries = monitor.list_active()
                return format_monitor_markdown("list", {"monitors": entries})

            elif action == "status":
                stats = monitor.stats()
                return format_monitor_markdown("status", stats)

            elif action == "test":
                if not params.webhook_url:
                    raise _tool_error("webhook_url required for action='test'", "validation_error")
                username = params.username or "test"
                success = await monitor.test_webhook(params.webhook_url, username)
                return format_monitor_markdown("test", {"success": success, "webhook_url": params.webhook_url})

            else:
                raise _tool_error(
                    f"Unknown action '{action}'",
                    "validation_error",
                    "Valid actions: add, remove, list, status, test",
                )
        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_monitor",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=_monitor_annotations,
            input_model=MonitorInput,
            description_first_line="🔐 AUTH REQUIRED — Monitor Instagram accounts for new posts via webhook.",
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_sessions (🔐 auth — multi-account env management)
    # ─────────────────────────────────────────────────────────────────────
    _sessions_annotations = {
        "title": "Instagram Multi-Account Sessions",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }

    @mcp.tool(
        name="instagram_sessions",
        annotations=_sessions_annotations,
    )
    async def instagram_sessions(params: SessionInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — View all loaded Instagram sessions (multi-account support).

        Shows all named sessions loaded from environment variables.
        To add sessions, set INSTAGRAM_MCP_COOKIES_<ALIAS>=<path>.

        Example env vars:
          INSTAGRAM_MCP_COOKIES=cookies.txt        → alias 'default'
          INSTAGRAM_MCP_COOKIES_BRAND=brand.txt    → alias 'brand'
          INSTAGRAM_MCP_COOKIES_AGENCY=agency.txt  → alias 'agency'

        Args:
            params: action ('list' or 'status')
        """
        from ..session_manager import SessionManager

        session_mgr: SessionManager = getattr(mcp, "_session_manager", None)  # type: ignore[attr-defined]
        if session_mgr is None:
            return "## Sessions\n\nNo session manager initialized."

        status = session_mgr.status()
        authed = len(session_mgr.authenticated_aliases())
        return format_sessions_markdown({
            "sessions": status,
            "authenticated_count": authed,
        })

    descriptors.append(
        ToolDescriptor(
            name="instagram_sessions",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=_sessions_annotations,
            input_model=SessionInput,
            description_first_line="🔐 AUTH REQUIRED — View all loaded Instagram sessions (multi-account support).",
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_oauth (🔐 auth — Graph API token management)
    # ─────────────────────────────────────────────────────────────────────
    _oauth_annotations = {
        "title": "Instagram OAuth Manager",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }

    @mcp.tool(
        name="instagram_oauth",
        annotations=_oauth_annotations,
    )
    async def instagram_oauth(params: OAuthInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Manage Instagram Graph API OAuth 2.0 tokens.

        Provides a complete OAuth flow for official Instagram Graph API access
        (business/creator accounts). Works alongside cookies-based tools.

        Actions:
          init_flow     — generate the authorization URL to visit in browser
          exchange_code — exchange the callback 'code' for a long-lived token
          refresh_token — refresh the token before it expires (60-day cycle)
          status        — show token validity and expiry

        Prerequisites:
          Set env vars: INSTAGRAM_MCP_OAUTH_APP_ID, INSTAGRAM_MCP_OAUTH_APP_SECRET,
          INSTAGRAM_MCP_OAUTH_REDIRECT_URI

        Args:
            params: action, code (for exchange_code), scopes (for init_flow)
        """
        from ..oauth_manager import OAuthManager

        oauth: OAuthManager = getattr(mcp, "_oauth_manager", None)  # type: ignore[attr-defined]
        if oauth is None:
            return format_oauth_markdown("status", {
                "configured": False,
                "has_token": False,
                "token_valid": False,
            })

        action = params.action.lower().strip()
        await ctx.info(f"instagram_oauth: action={action}")

        try:
            if action == "init_flow":
                scopes = params.scopes or None
                url = oauth.get_auth_url(scopes=scopes)
                return format_oauth_markdown("init_flow", {"auth_url": url})

            elif action == "exchange_code":
                if not params.code:
                    raise _tool_error("code required for action='exchange_code'", "validation_error")
                result = await oauth.exchange_code(params.code)
                return format_oauth_markdown("exchange_code", result)

            elif action == "refresh_token":
                result = await oauth.refresh_token()
                return format_oauth_markdown("refresh_token", result)

            elif action == "status":
                return format_oauth_markdown("status", oauth.status())

            else:
                raise _tool_error(
                    f"Unknown action '{action}'",
                    "validation_error",
                    "Valid actions: init_flow, exchange_code, refresh_token, status",
                )
        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_oauth",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=_oauth_annotations,
            input_model=OAuthInput,
            description_first_line="🔐 AUTH REQUIRED — Manage Instagram Graph API OAuth 2.0 tokens.",
        )
    )

    return descriptors


__all__ = [
    "TOOLSET_NAME",
    "BatchScrapeInput",
    "register_automation",
]
