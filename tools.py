"""
MCP Tool registration — 13 tools, optimised for LLM agents.

AUTH TIERS:
  🌐 ANONYMOUS (10 tools) — no login, no cookies, fully public
  🔐 AUTHENTICATED (3 tools) — requires cookies.txt with a valid Instagram session

Tools:
  1. instagram_profile          — 🌐 Profile + optional feed tags + activity status
  2. instagram_feed_deep        — 🌐 Deep paginated feed analysis (up to 200 posts)
  3. instagram_analyze_engagement — 🌐 ER%, content mix, best days, top posts
  4. instagram_find_collab_network — 🌐 Collaboration/mention network map
  5. instagram_compare_profiles  — 🌐 Side-by-side comparison (2-5 accounts)
  6. instagram_bulk_check        — 🌐 Up to 20 profiles in parallel
  7. instagram_batch_scrape      — 🌐 Large-scale scraping (up to 500 profiles)
  8. instagram_server            — 🌐 Server diagnostics + cache management
  9. instagram_tagged_by         — 🔐 Posts where OTHERS tagged this account
 10. instagram_reposts           — 🔐 Content this account reposted from others
 11. instagram_post              — 🌐 Full details for a single post by shortcode/URL
 12. instagram_reels             — 🔐 Account's own reels with play counts
 13. instagram_post_comments     — 🌐 Comments on a post with per-comment like counts

Architecture:
  - Every tool has ctx: Context → MCP-native progress + logging (all async)
  - ToolError raised for ALL error cases → isError=true in protocol response
  - Tool annotations: readOnlyHint, idempotentHint, destructiveHint, openWorldHint
  - ctx.report_progress(progress, total, message) with descriptive messages
  - ctx.info/debug/warning/error — all properly awaited (they are async)
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from typing import List

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .client import InstagramClient
from .config import MCPConfig
from .exceptions import InstagramMCPError
from .formatter import (
    format_account_status_markdown,
    format_bulk_results_markdown,
    format_collab_network_markdown,
    format_compare_profiles_markdown,
    format_deep_feed_markdown,
    format_diagnostics_markdown,
    format_engagement_analysis_markdown,
    format_post_markdown,
    format_posts_markdown,
    format_profile_markdown,
    format_profile_with_tags_markdown,
    format_comments_markdown,
    format_reels_markdown,
    format_reposts_markdown,
    format_tagged_by_markdown,
)
from .models import (
    BulkProfilesInput,
    CollabNetworkInput,
    CompareProfilesInput,
    DateRange,
    DeepFeedInput,
    EngagementAnalysisInput,
    FeedTagResult,
    InstagramProfile,
    PostCommentsInput,
    PostInput,
    ProfileInput,
    ReelsInput,
    RepostsInput,
    ServerInput,
    TaggedByInput,
)
from .parser import (
    check_dead_account,
    extract_page_info,
    parse_comments,
    parse_feed_tags,
    parse_feed_tags_from_edges,
    parse_post_html,
    parse_profile,
    parse_reels_edges,
    parse_repost_items,
    parse_tagged_tab_edges,
    shortcode_to_media_id,
)

logger = logging.getLogger("instagram_mcp.tools")


# ═════════════════════════════════════════════════════════════════════════════
# BATCH SCRAPE INPUT
# ═════════════════════════════════════════════════════════════════════════════

class BatchScrapeInput(BaseModel):
    """Input for large-scale batch scraping of Instagram profiles."""

    targets: List[str] = Field(
        ...,
        description="Instagram usernames to scrape (max 500, without @).",
        min_length=1,
        max_length=500,
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
        default=10,
        description="Parallel workers (1-20). Higher = faster but more rate-limit risk.",
        ge=1,
        le=20,
    )
    use_cookies: bool = Field(
        default=False,
        description="Use cookies for authenticated requests.",
    )
    output_file: str = Field(
        default="",
        description="File path to save full JSON results. Leave empty to use a temp file.",
    )


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def sanitize_username(raw: str) -> str:
    """Strip whitespace, remove '@', lowercase. Raises ValueError if empty."""
    cleaned = raw.strip().lstrip("@").lower()
    if not cleaned:
        raise ValueError("Username cannot be empty")
    return cleaned


def _tool_error(
    msg: str,
    error_type: str = "error",
    suggested_action: str = "",
) -> ToolError:
    """Build a ToolError with LLM-friendly structured message."""
    parts = [f"❌ **Error** ({error_type}): {msg}"]
    if suggested_action:
        parts.append(f"💡 **Action**: {suggested_action}")
    return ToolError("\n".join(parts))


def _exception_to_tool_error(e: Exception) -> ToolError:
    """Convert any exception to a structured ToolError."""
    if isinstance(e, InstagramMCPError):
        return _tool_error(str(e), e.error_type, e.suggested_action)
    return _tool_error(str(e), "unexpected_error", "Unexpected error. Check server logs or try again.")


# ═════════════════════════════════════════════════════════════════════════════
# SHARED PAGINATION HELPER
# ═════════════════════════════════════════════════════════════════════════════

async def _paginate_feed(
    client: InstagramClient,
    config: "MCPConfig",
    user: dict,
    profile,
    max_posts: int,
    max_age_days: int,
    date_range,
    ctx: Context,
) -> tuple:
    """
    Fetch paginated feed edges. Returns (all_edges, pages_fetched, has_more, effective_max).
    First 12 posts come from user dict (already fetched). Remainder via GraphQL cursor.
    """
    page_info = extract_page_info(user)
    first_edges = page_info.get("first_page_edges", [])
    end_cursor = page_info.get("end_cursor", "")
    has_next = page_info.get("has_next_page", False)

    all_edges = list(first_edges)
    pages_fetched = 1
    has_more = has_next

    effective_max = min(max_posts, config.max_pagination_posts)
    remaining = effective_max - len(all_edges)

    await ctx.report_progress(len(all_edges), float(effective_max), message=f"{len(all_edges)}/{effective_max} posts fetched")

    if remaining > 0 and has_next and end_cursor:
        await ctx.info(f"Paginating: {len(all_edges)} posts so far, fetching up to {remaining} more...")
        feed_result = await client.fetch_user_feed(
            user_id=profile.user_id,
            username=profile.username,
            end_cursor=end_cursor,
            max_posts=remaining,
            max_age_days=max_age_days,
            cache_ttl=config.cache_feed_ttl,
            date_range=date_range,
        )
        new_edges = feed_result.get("edges", [])
        all_edges.extend(new_edges)
        pages_fetched += feed_result.get("pages_fetched", 0)
        has_more = feed_result.get("has_more", False)
        if not new_edges and remaining > 0:
            await ctx.warning(
                f"Pagination returned 0 posts (cursor may be incompatible). "
                f"Got {len(all_edges)} total (first page only)."
            )
    elif remaining > 0 and not end_cursor:
        await ctx.info(f"No pagination cursor — profile returned {len(all_edges)} posts only.")

    await ctx.report_progress(float(len(all_edges)), float(effective_max), message=f"Done: {len(all_edges)} posts")
    return all_edges, pages_fetched, has_more, effective_max


# ═════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

def register_tools(mcp: FastMCP, client: InstagramClient, config: MCPConfig) -> None:
    """Register all 13 MCP tools (10 anonymous + 3 authenticated)."""

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 1: instagram_profile
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_profile",
        annotations={
            "title": "Instagram Profile",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_profile(params: ProfileInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Fetch a public Instagram account's profile data.

        Behaviour is controlled by two flags — pick the mode you need:

        ┌─────────────────────────────────────────────────────────────────────┐
        │ include_feed=True + check_alive=True  (DEFAULT — PRIMARY MODE)      │
        │   One API call → profile + up to 12 recent post tags + activity.   │
        │   Returns: all profile fields, tags list (usertags + @mentions),   │
        │   per-tag post URL + timestamp, last_post_days, is_dead status.    │
        ├─────────────────────────────────────────────────────────────────────┤
        │ include_feed=False + check_alive=True  (STATUS CHECK)               │
        │   Fastest activity check — is the account active / dead / private? │
        │   Returns: status, last_post_days, followers, posts_count.         │
        ├─────────────────────────────────────────────────────────────────────┤
        │ include_feed=False + check_alive=False  (PROFILE ONLY)              │
        │   Absolute minimum: bio, followers, category, website, flags.       │
        │   No post data. Single API call, fastest possible.                 │
        ├─────────────────────────────────────────────────────────────────────┤
        │ include_feed=True + check_alive=False  (TAGS ONLY, NO STATUS)       │
        │   Profile + tag extraction, but skip the dead-account check.       │
        └─────────────────────────────────────────────────────────────────────┘

        Private accounts: always return profile metadata, feed is skipped.
        not_found: raises ToolError (except in status-check mode, returns content).
        Results cached — repeated calls for the same user are instant.

        Args:
            params: username, include_feed (default True), max_feed_posts (1-12),
                    max_age_days (1-365), check_alive (default True),
                    dead_threshold_days (30-3650), since_timestamp, until_timestamp,
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username (without @).")

        await ctx.info(
            f"instagram_profile: @{params.username} "
            f"(feed={params.include_feed}, alive={params.check_alive}, "
            f"max={params.max_feed_posts}p, {params.max_age_days}d)"
        )
        _t0 = time.perf_counter()

        try:
            ttl = config.cache_profile_ttl
            user = await client.fetch_user(params.username, ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            if params.check_alive and not params.include_feed:
                return format_account_status_markdown(
                    params.username, "not_found", False, False,
                    0, 0, 0, params.dead_threshold_days,
                )
            raise _tool_error(
                f"@{params.username} not found — the account may have been deleted or renamed.",
                "not_found",
                "Verify the username is correct and the account exists.",
            )

        try:
            profile = parse_profile(user, params.username, config)

            is_dead, last_post_days = False, 0
            if params.check_alive and not profile.is_private:
                is_dead, last_post_days = check_dead_account(user, params.dead_threshold_days)

            if not params.include_feed:
                if params.check_alive:
                    status = "private" if profile.is_private else ("dead" if is_dead else "active")
                    out = format_account_status_markdown(
                        params.username, status, is_dead, profile.is_private,
                        last_post_days, profile.followers, profile.posts_count,
                        params.dead_threshold_days,
                    )
                else:
                    out = format_profile_markdown(profile)
            else:
                _since = params.resolved_since()
                _until = params.resolved_until()
                date_range = (
                    DateRange(since=_since, until=_until)
                    if (_since or _until) else None
                )

                feed_tags_result = FeedTagResult()
                if not profile.is_private:
                    await ctx.info(f"@{params.username}: parsing {params.max_feed_posts} posts...")
                    feed_tags_result = parse_feed_tags(
                        user, params.max_feed_posts, params.max_age_days, date_range=date_range,
                    )

                out = format_profile_with_tags_markdown(profile, feed_tags_result, is_dead, last_post_days)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} ✓ — {elapsed:.2f}s")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 2: instagram_feed_deep
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_feed_deep",
        annotations={
            "title": "Instagram Deep Feed Analysis",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_feed_deep(params: DeepFeedInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Paginated feed analysis — fetches up to 200 posts across multiple API pages.

        The first 12 posts come from the profile request at no extra cost. Each
        additional page of 12 posts requires one GraphQL cursor request. Example:
        100 posts ≈ 9 requests. Progress is reported via MCP progress notifications.

        DATE-RANGE SCRAPING (smart pagination):
        Pass `since_date`/`until_date` (e.g. '01.03.2026' / '31.03.2026') to
        fetch only posts in a specific window. The server paginates from the
        newest post, silently skips posts after `until`, and stops automatically
        once 100 consecutive posts older than `since` are seen — so you only pay
        for pages that contain (or lead to) the requested range. Increase
        max_posts to expand the cap on how many in-range posts are returned.

        Returns the same fields as instagram_profile with include_feed=True, but
        over a much larger post window, making it suitable for:
        - Brand collaboration history spanning weeks or months
        - Content format and cadence patterns over time
        - Complete hashtag and mention mapping across all recent content
        - Engagement trend analysis by post type and day of week

        Set include_posts_detail=True to include full per-post data: caption,
        hashtags, likes, comments, location, music (reels), dimensions.

        Args:
            params: username, max_posts (1-200, default 50), max_age_days (1-365, default 30),
                    include_posts_detail (bool), since_date / until_date
                    (DD.MM.YYYY or YYYY-MM-DD), or raw since_timestamp /
                    until_timestamp (Unix seconds).
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        await ctx.info(f"instagram_feed_deep: @{params.username} (max={params.max_posts}, {params.max_age_days}d)")
        _t0 = time.perf_counter()

        try:
            user = await client.fetch_user(params.username, config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            profile = parse_profile(user, params.username, config)
            _since = params.resolved_since()
            _until = params.resolved_until()
            date_range = (
                DateRange(since=_since, until=_until)
                if (_since or _until) else None
            )

            if profile.is_private:
                return format_deep_feed_markdown(profile, FeedTagResult(), False, 0)

            all_edges, pages_fetched, has_more, effective_max = await _paginate_feed(
                client, config, user, profile,
                params.max_posts, params.max_age_days, date_range, ctx,
            )

            feed_tags = parse_feed_tags_from_edges(
                edges=all_edges, max_posts=effective_max,
                max_age_days=params.max_age_days, detect_pinned=True,
                pages_fetched=pages_fetched, has_more_posts=has_more,
                date_range=date_range,
            )
            is_dead, last_post_days = check_dead_account(user)

            out = format_deep_feed_markdown(profile, feed_tags, is_dead, last_post_days)
            if params.include_posts_detail and feed_tags.posts:
                out += "\n\n" + format_posts_markdown(feed_tags.posts)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(
            f"@{params.username} ✓ — {feed_tags.posts_checked} posts, "
            f"{pages_fetched} pages, {len(feed_tags.tags)} tags — {elapsed:.2f}s"
        )
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 3: instagram_analyze_engagement
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_analyze_engagement",
        annotations={
            "title": "Instagram Engagement Analysis",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_analyze_engagement(params: EngagementAnalysisInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Compute engagement rate and content performance metrics for a public account.

        Fetches up to max_posts recent posts (paginated) and calculates:
        - Engagement rate % = (avg_likes + avg_comments) / followers × 100
          Benchmarks: Excellent ≥6%, Good 3-6%, Average 1-3%, Low <1%
        - Average likes and comments per post
        - Content type breakdown (reels / carousels / images / videos) with
          per-type average likes, comments, and video views
        - Best posting days by average likes (Mon-Sun)
        - Top 5 highest-performing posts with direct links
        - Top 15 hashtags ranked by usage frequency
        - Total video views across all video content

        More posts → more accurate statistics. Private accounts raise ToolError.

        Args:
            params: username, max_posts (1-200, default 50), max_age_days (1-365,
                    default 90)
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        await ctx.info(f"instagram_analyze_engagement: @{params.username} ({params.max_posts} posts, {params.max_age_days}d)")
        _t0 = time.perf_counter()

        try:
            user = await client.fetch_user(params.username, config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            profile = parse_profile(user, params.username, config)

            if profile.is_private:
                raise _tool_error(
                    f"@{params.username} is private — engagement data not accessible.",
                    "private_account",
                    "Only public accounts can be analysed for engagement.",
                )

            all_edges, pages_fetched, _, effective_max = await _paginate_feed(
                client, config, user, profile,
                params.max_posts, params.max_age_days, None, ctx,
            )

            feed_tags = parse_feed_tags_from_edges(
                edges=all_edges, max_posts=effective_max,
                max_age_days=params.max_age_days, detect_pinned=True,
                pages_fetched=pages_fetched,
            )

            out = format_engagement_analysis_markdown(profile, feed_tags.posts)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} ✓ — {len(feed_tags.posts)} posts, {pages_fetched} pages — {elapsed:.2f}s")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 4: instagram_find_collab_network
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_find_collab_network",
        annotations={
            "title": "Instagram Collaboration Network",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_find_collab_network(params: CollabNetworkInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Map all people and brands an account collaborates with across recent posts.

        Scans up to max_posts posts and extracts collaborators across four
        relationship types:
        - Photo usertags — accounts tagged directly in image/video frames
        - Caption @mentions — accounts @-mentioned in caption text
        - Official co-authors — Instagram Collab posts (jointly published)
        - Paid sponsors — paid partnership disclosures (official sponsor tags)

        Each collaborator entry shows: frequency (post count they appear in)
        and a link to the first post they appeared in. Raise min_frequency to
        filter out one-off mentions and surface regular collaborators.

        Private accounts raise ToolError.

        Args:
            params: username, max_posts (1-200, default 50), max_age_days (1-365,
                    default 90), min_frequency (1-50, default 1)
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        await ctx.info(f"instagram_find_collab_network: @{params.username} ({params.max_posts} posts)")
        _t0 = time.perf_counter()

        try:
            user = await client.fetch_user(params.username, config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            profile = parse_profile(user, params.username, config)

            if profile.is_private:
                raise _tool_error(
                    f"@{params.username} is private — collaboration data not accessible.",
                    "private_account",
                    "Only public accounts can be analysed.",
                )

            all_edges, pages_fetched, _, effective_max = await _paginate_feed(
                client, config, user, profile,
                params.max_posts, params.max_age_days, None, ctx,
            )

            feed_tags = parse_feed_tags_from_edges(
                edges=all_edges, max_posts=effective_max,
                max_age_days=params.max_age_days, detect_pinned=True,
                pages_fetched=pages_fetched,
            )

            out = format_collab_network_markdown(profile, feed_tags.posts, params.min_frequency)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} ✓ — {len(feed_tags.posts)} posts, {pages_fetched} pages — {elapsed:.2f}s")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 5: instagram_compare_profiles
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_compare_profiles",
        annotations={
            "title": "Instagram Profile Comparison",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_compare_profiles(params: CompareProfilesInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Fetch 2-5 Instagram profiles in parallel and display them side by side.

        All profiles are fetched concurrently for maximum speed. Returns a
        structured comparison table with:
        - Account status (active/dead/private/not found) and days since last post
        - Followers, following, total post count
        - Verification badge, account type (Personal/Creator/Business)
        - Content category, website presence
        - Feature flags: has Reels, has Guides
        - User ID

        Not-found or errored accounts appear as empty rows rather than raising
        ToolError, so a partial result is always returned.

        Args:
            params: usernames (list of 2-5, without @)
        """
        cleaned = []
        for raw in params.usernames:
            try:
                cleaned.append(sanitize_username(raw))
            except ValueError:
                pass
        if len(cleaned) < 2:
            raise _tool_error("Need at least 2 valid usernames to compare.", "validation_error", "Provide 2-5 Instagram usernames.")

        await ctx.info(f"instagram_compare_profiles: {', '.join('@' + u for u in cleaned)}")
        await ctx.report_progress(0.0, float(len(cleaned)), message=f"Fetching {len(cleaned)} profiles in parallel...")
        _t0 = time.perf_counter()

        try:
            tasks = [asyncio.create_task(client.fetch_user(u, config.cache_profile_ttl)) for u in cleaned]
            raw_users = await asyncio.gather(*tasks, return_exceptions=True)

            entries = []
            for i, (username, result) in enumerate(zip(cleaned, raw_users)):
                if isinstance(result, Exception) or result is None:
                    entries.append((InstagramProfile(username=username), False, 0))
                else:
                    profile = parse_profile(result, username, config)
                    is_dead, last_post_days = check_dead_account(result)
                    entries.append((profile, is_dead, last_post_days))
                await ctx.report_progress(float(i + 1), float(len(cleaned)), message=f"@{username} done")

            out = format_compare_profiles_markdown(entries)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"Compare ✓ — {len(entries)} profiles — {elapsed:.2f}s")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 6: instagram_bulk_check
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_bulk_check",
        annotations={
            "title": "Instagram Bulk Profile Check",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_bulk_check(params: BulkProfilesInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Fetch up to 20 Instagram profiles in parallel with activity status for each.

        Each result includes: found/not_found, followers, following, post count,
        category, website, private/verified/business flags, is_dead flag,
        days since last post. Smart proxy rotation spreads requests across
        available proxies. Not-found and private accounts appear in results
        rather than raising ToolError.

        Raises ToolError only on complete failure (network down, all proxies failed).

        Args:
            params: usernames (list, max 20), concurrency (1-20, default 5)
        """
        sanitized = []
        for raw in params.usernames:
            try:
                sanitized.append(sanitize_username(raw))
            except ValueError:
                pass
        if not sanitized:
            raise _tool_error("Username list is empty or all usernames are invalid.", "validation_error", "Provide at least one valid Instagram username.")

        await ctx.info(f"instagram_bulk_check: {len(sanitized)} profiles, concurrency={params.concurrency}")
        await ctx.report_progress(0.0, float(len(sanitized)), message=f"Starting {len(sanitized)} profiles...")
        _t0 = time.perf_counter()

        try:
            raw_results = await client.fetch_bulk(sanitized, params.concurrency, config.cache_profile_ttl)

            parsed_results = []
            for i, raw in enumerate(raw_results):
                username = raw.get("username", "")
                if not raw.get("found") or not raw.get("user"):
                    parsed_results.append({"username": username, "found": False, "error": raw.get("error")})
                else:
                    user_data = raw.get("user", {})
                    p = parse_profile(user_data, username, config)
                    is_dead, last_post_days = check_dead_account(user_data)
                    parsed_results.append({
                        "username": username, "found": True,
                        "user_id": p.user_id, "full_name": p.full_name,
                        "followers": p.followers, "following": p.following,
                        "posts_count": p.posts_count, "category": p.category,
                        "website": p.website, "is_private": p.is_private,
                        "is_verified": p.is_verified, "is_business": p.is_business,
                        "is_dead": is_dead, "last_post_days": last_post_days,
                    })
                await ctx.report_progress(float(i + 1), float(len(sanitized)), message=f"@{username} done")

            out = format_bulk_results_markdown(parsed_results)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        found = sum(1 for r in parsed_results if r.get("found"))
        elapsed = time.perf_counter() - _t0
        await ctx.info(f"Bulk ✓ — {found}/{len(sanitized)} found — {elapsed:.2f}s")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 7: instagram_batch_scrape
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_batch_scrape",
        annotations={
            "title": "Instagram Batch Profile Scraper",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def instagram_batch_scrape(params: BatchScrapeInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Scrape up to 500 Instagram profiles with profile info, feed tags, and
        dead-account detection.

        Runs asynchronously with configurable concurrency (max 20 parallel workers).
        Supports optional date-range filtering to restrict which posts are
        analysed. Results are saved to output_file as JSON; if no path is given,
        a temporary file is used and its path is returned.

        Returns a summary table: total targets, completed count, and a breakdown
        by status (active / not_found / private / dead / error) with
        throughput (profiles/second) and total elapsed time.

        Args:
            params: targets (list, max 500), since_date (DD.MM.YYYY),
                    until_date (DD.MM.YYYY), max_workers (1-20),
                    use_cookies, output_file
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
            from datetime import datetime as _datetime
            try:
                _since_dt = _datetime.strptime(params.since_date.strip(), "%d.%m.%Y")
                _until_dt = _datetime.strptime(params.until_date.strip(), "%d.%m.%Y")
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

        from .batch_runner import BatchConfig, BatchRunner

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
            batch_cfg = BatchConfig(
                targets_file=targets_tmp.name,
                output_file=output_file,
                max_workers=params.max_workers,
                since_date=params.since_date,
                until_date=params.until_date,
                use_cookies=params.use_cookies,
                save_every=max(10, len(sanitized) // 10) if len(sanitized) >= 10 else len(sanitized),
            )
            runner = BatchRunner(batch_cfg, client)

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

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 8: instagram_server
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_server",
        annotations={
            "title": "Instagram MCP Server Management",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
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
                raise _tool_error(f"Failed to collect diagnostics: {e}", "internal_error")

        elif action == "clear_cache":
            try:
                count = await client.cache.clear()
                await ctx.info(f"Full cache flush: {count} entries removed")
                return f"✅ All cache cleared ({count} entries removed). Next requests will fetch fresh data."
            except Exception as e:
                raise _tool_error(f"Cache clear failed: {e}", "internal_error")

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
                raise _tool_error(f"Cache clear failed for @{username_raw}: {e}", "internal_error")

        else:
            raise _tool_error(
                f"Unknown action: '{params.action}'. Valid actions: 'status', 'clear_cache', 'clear_user'.",
                "validation_error",
                "Set action to one of: 'status', 'clear_cache', 'clear_user'.",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 9: instagram_tagged_by
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_tagged_by",
        annotations={
            "title": "Instagram Tagged-By Feed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_tagged_by(params: TaggedByInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch posts made BY OTHERS that tag the specified account in their photos
        or videos. This is the "Tagged" tab on an Instagram profile — content
        created by external accounts that mention/tag this user.

        ┌─────────────────────────────────────────────────────────────────────┐
        │ SETUP (one-time, before first use):                                 │
        │  1. Log in to Instagram in your browser                             │
        │  2. Export cookies using a browser extension:                       │
        │     • Chrome: "Get cookies.txt LOCALLY" or "Cookie-Editor"         │
        │     • Firefox: "cookies.txt" extension                              │
        │  3. Save the file as cookies.txt (Netscape format)                  │
        │  4. Place it at one of these locations (checked in order):          │
        │     a. Path in INSTAGRAM_MCP_COOKIES env var                        │
        │     b. ./cookies.txt  (current working directory)                   │
        │     c. ../cookies.txt (parent directory)                            │
        │  5. The file must contain a valid 'sessionid' cookie for            │
        │     instagram.com                                                   │
        └─────────────────────────────────────────────────────────────────────┘

        Difference from instagram_profile tags:
        - instagram_profile → tags the ACCOUNT created (usertags on own posts)
        - instagram_tagged_by → posts OTHERS created that tag this account

        Use cases:
        - See who is organically posting about a brand/creator
        - Find UGC (user-generated content) for a product account
        - Discover brand advocates and superfans
        - Measure share-of-voice across external creators

        Returns per post: poster username, post URL, type, likes, comments,
        caption snippet, approximate date.

        Args:
            params: username, max_posts (1-200, default 50),
                    min_poster_followers (filter by poster's follower count, default 0)
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        # Auth check — give a clear, actionable error before making any requests
        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.txt not found. Place a Netscape-format cookies.txt with your "
                "Instagram session in the working directory, or set INSTAGRAM_MCP_COOKIES "
                "env var to its path."
            )
            raise _tool_error(
                f"instagram_tagged_by requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.txt, and restart the MCP server.",
            )

        await ctx.info(f"instagram_tagged_by: @{params.username} (max={params.max_posts})")
        _t0 = time.perf_counter()

        # First fetch the profile to get user_id
        try:
            user = await client.fetch_user(params.username, config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            profile = parse_profile(user, params.username, config)

            await ctx.info(f"@{params.username} (id={profile.user_id}): fetching tagged posts...")
            await ctx.report_progress(0.0, float(params.max_posts), message="Fetching tagged posts...")

            tagged_result = await client.fetch_tagged_posts_paginated(
                user_id=profile.user_id,
                username=params.username,
                max_posts=params.max_posts,
                cache_ttl=config.cache_tagged_ttl,
            )

            tagged_posts = parse_tagged_tab_edges(
                tagged_result.get("edges") or [],
                max_posts=params.max_posts,
            )
            out = format_tagged_by_markdown(profile, tagged_posts, params.min_poster_followers)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} tagged_by ✓ — {len(tagged_posts)} posts — {elapsed:.2f}s")
        await ctx.report_progress(float(len(tagged_posts)), float(params.max_posts), message="Done")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 10: instagram_reposts
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_reposts",
        annotations={
            "title": "Instagram Reposts Tab",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_reposts(params: RepostsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch content that this account ACTIVELY CHOSE TO REPOST from other creators.
        This is the Reposts Tab — showing what external content the account amplifies.

        ┌──────────────────────────────────────────────────────────────────────┐
        │ KEY DISTINCTION — three "content from others" surfaces:              │
        │                                                                      │
        │ instagram_find_collab_network  🌐  — who appears in OWN posts       │
        │   (tags/mentions IN captions and images the account itself made)     │
        │                                                                      │
        │ instagram_tagged_by            🔐  — who tagged US in THEIR posts   │
        │   (passive — we did nothing, they mentioned us)                      │
        │                                                                      │
        │ instagram_reposts              🔐  — what WE reposted from others   │
        │   (active — we chose to share their content to our audience)         │
        └──────────────────────────────────────────────────────────────────────┘

        Each repost = an explicit endorsement. A brand reposting a creator's
        content signals: "we approve of this person and want our audience to
        see them." This makes reposts the strongest relationship signal.

        Use cases:
        - Discover who a brand or creator officially endorses
        - Find UGC creators the brand amplifies (often precedes paid collabs)
        - Map the "inner circle" of creators a brand trusts
        - Compare: who tags them vs. who they actually amplify

        Returns per repost: original creator, post type, likes, comments,
        caption snippet, approximate date.

        Setup (same as instagram_tagged_by — one cookies.txt covers both):
        1. Export cookies from browser after logging in to Instagram
        2. Save as cookies.json (EditThisCookie) or cookies.txt (Get cookies.txt)
        3. Place next to MCP server or set INSTAGRAM_MCP_COOKIES env var
        4. Restart the server

        Args:
            params: username, max_posts (1-200, default 50)
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.json or cookies.txt not found. Place a cookie export file "
                "in the working directory, or set INSTAGRAM_MCP_COOKIES env var."
            )
            raise _tool_error(
                f"instagram_reposts requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.json, and restart the MCP server.",
            )

        await ctx.info(f"instagram_reposts: @{params.username} (max={params.max_posts})")
        _t0 = time.perf_counter()

        try:
            user = await client.fetch_user(params.username, config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            profile = parse_profile(user, params.username, config)

            await ctx.info(
                f"@{params.username} (id={profile.user_id}): fetching repost history..."
            )
            await ctx.report_progress(
                0.0, float(params.max_posts), message="Fetching reposts..."
            )

            result = await client.fetch_reposts_paginated(
                user_id=profile.user_id,
                username=params.username,
                max_posts=params.max_posts,
                cache_ttl=config.cache_reposts_ttl,
            )

            raw_items = result.get("items") or []
            pages_fetched = result.get("pages_fetched", 1)
            has_more = result.get("has_more", False)

            repost_items = parse_repost_items(raw_items, max_posts=params.max_posts)
            out = format_reposts_markdown(profile, repost_items)

            if has_more:
                out += (
                    f"\n\n*Showing {len(repost_items)} of more available reposts. "
                    f"Increase max_posts to fetch more.*"
                )

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(
            f"@{params.username} reposts ✓ — {len(repost_items)} items, "
            f"{pages_fetched} pages — {elapsed:.2f}s"
        )
        await ctx.report_progress(
            float(len(repost_items)), float(params.max_posts), message="Done"
        )
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 11: instagram_post
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_post",
        annotations={
            "title": "Instagram Post Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_post(params: PostInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Fetch full details for a single Instagram post by shortcode or URL.

        ┌──────────────────────────────────────────────────────────────────────┐
        │ Input — any of these formats work:                                   │
        │   'DXjuqH9nDVE'                        ← bare shortcode             │
        │   'https://www.instagram.com/p/DXjuqH9nDVE/'  ← full URL           │
        │   'instagram.com/p/DXjuqH9nDVE'        ← URL without scheme        │
        └──────────────────────────────────────────────────────────────────────┘

        Returns all available post metadata:

        📍 Location
          - Place name (e.g. "SoHo, New York", "Eden Rock - St Barths")
          - GPS coordinates (lat/lng) with Google Maps link
          - Present only if the poster added a location tag

        📋 Post Details
          - Author @username + verification status
          - Post type: image / video / carousel / reels
          - Exact posting timestamp (from page HTML — accurate, not estimated)
          - Like count, comment count, view/play count (reels)
          - Carousel slide count, video duration

        📝 Caption
          - Full caption text (up to 600 chars displayed)
          - Extracted hashtags as clickable links
          - @mentions found in caption

        👥 People
          - Accounts tagged in the photo/video (usertags)
          - Caption @mentions
          - Co-authors (collab posts)

        🎵 Music (reels only)
          - Artist name and track title

        Results are cached — fetching the same post twice is instant.
        Private posts or deleted posts raise ToolError.

        Args:
            params: post — shortcode (e.g. 'DXjuqH9nDVE') or full Instagram
                    post URL
        """
        shortcode = params.post  # already cleaned by PostInput validator
        await ctx.info(f"instagram_post: {shortcode}")
        _t0 = time.perf_counter()

        try:
            await ctx.report_progress(0.0, 1.0, message="Fetching post page...")
            html = await client.fetch_post(shortcode, cache_ttl=config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        try:
            info = parse_post_html(html, shortcode)

            if not info.username and not info.taken_at:
                raise _tool_error(
                    f"Post /{shortcode}/ could not be parsed — "
                    "it may be private, deleted, or temporarily unavailable.",
                    "parse_error",
                    "Verify the shortcode is correct and the post is publicly visible.",
                )

            out = format_post_markdown(info)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        loc_name = info.location.name if info.location.has_location else "no location"
        await ctx.info(
            f"{shortcode} ✓ — @{info.username}, {info.taken_at_str}, "
            f"{loc_name} — {elapsed:.2f}s"
        )
        await ctx.report_progress(1.0, 1.0, message="Done")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 12: instagram_reels
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_reels",
        annotations={
            "title": "Instagram Reels Tab",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_reels(params: ReelsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch the account's own reels with PLAY COUNTS — the primary reel metric.

        ┌──────────────────────────────────────────────────────────────────────┐
        │ WHY THIS TOOL EXISTS                                                 │
        │                                                                      │
        │ play_count is NOT available via instagram_feed_deep or               │
        │ instagram_analyze_engagement. The standard feed API returns          │
        │ view_count=null for all reels. Only the Reels Tab endpoint           │
        │ (PolarisProfileReelsTabContentQuery_connection) exposes true         │
        │ play counts, making this tool essential for reel performance         │
        │ analysis.                                                            │
        └──────────────────────────────────────────────────────────────────────┘

        Returns per reel: post URL, play count, like count, comment count,
        approximate posting date, thumbnail dimensions, pinned status.

        Use cases:
        - Find highest-performing reels by play count
        - Compare play counts vs like counts (virality indicators)
        - Identify which reels resonated with the audience
        - Audit reel posting frequency and volume

        Setup (same as instagram_tagged_by — one cookies.txt covers all auth tools):
        1. Export cookies from browser after logging in to Instagram
        2. Save as cookies.json (EditThisCookie) or cookies.txt (Get cookies.txt)
        3. Place next to MCP server or set INSTAGRAM_MCP_COOKIES env var
        4. Restart the server

        Args:
            params: username, max_reels (1-200, default 50)
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.json or cookies.txt not found. Place a cookie export file "
                "in the working directory, or set INSTAGRAM_MCP_COOKIES env var."
            )
            raise _tool_error(
                f"instagram_reels requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.json, and restart the MCP server.",
            )

        await ctx.info(f"instagram_reels: @{params.username} (max={params.max_reels})")
        _t0 = time.perf_counter()

        try:
            user = await client.fetch_user(params.username, config.cache_profile_ttl)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            profile = parse_profile(user, params.username, config)

            await ctx.info(
                f"@{params.username} (id={profile.user_id}): fetching reels tab..."
            )
            await ctx.report_progress(
                0.0, float(params.max_reels), message="Fetching reels..."
            )

            result = await client.fetch_reels_paginated(
                user_id=profile.user_id,
                username=params.username,
                max_reels=params.max_reels,
                cache_ttl=config.cache_reels_ttl,
            )

            raw_edges = result.get("edges") or []
            pages_fetched = result.get("pages_fetched", 1)
            has_more = result.get("has_more", False)

            reel_items = parse_reels_edges(raw_edges, max_reels=params.max_reels)
            out = format_reels_markdown(profile, reel_items)

            if has_more:
                out += (
                    f"\n\n*Showing {len(reel_items)} of more available reels. "
                    f"Increase max_reels to fetch more.*"
                )

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(
            f"@{params.username} reels ✓ — {len(reel_items)} reels, "
            f"{pages_fetched} pages — {elapsed:.2f}s"
        )
        await ctx.report_progress(
            float(len(reel_items)), float(params.max_reels), message="Done"
        )
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 13: instagram_post_comments
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_post_comments",
        annotations={
            "title": "Instagram Post Comments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_post_comments(params: PostCommentsInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — anonymous, no cookies needed.

        Fetch and analyze comments on a single public Instagram post.

        ┌──────────────────────────────────────────────────────────────────────┐
        │ WHY THIS TOOL EXISTS                                                 │
        │                                                                      │
        │ instagram_post returns the comment COUNT only. This tool returns     │
        │ the actual comment text + per-comment like counts + author info +    │
        │ threading depth — enabling sentiment analysis, top-commenter         │
        │ identification, and audience language breakdown.                     │
        └──────────────────────────────────────────────────────────────────────┘

        Returns per comment: text, like count, reply count, author username,
        verified status, posting time, GIF indicator, language flag.

        sort_order options:
        - 'popular' (default) — most-liked comments first. Best for finding
          the community's most-resonant reactions to a post.
        - 'recent' — chronological order. Best for monitoring live activity
          or finding the latest audience responses.

        Shortcode conversion: shortcode → numeric media_id (no extra API call).
        Works for posts, reels (/reel/), and IGTV (/tv/) URLs.

        Args:
            params: post (shortcode or URL), max_comments (1-500, default 100),
                    sort_order ('popular' | 'recent')
        """
        shortcode = params.post  # already extracted by Pydantic validator

        try:
            media_id = shortcode_to_media_id(shortcode)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram shortcode or post URL.")

        post_url = f"https://www.instagram.com/p/{shortcode}/"

        await ctx.info(
            f"instagram_post_comments: {shortcode} (media_id={media_id}, "
            f"max={params.max_comments}, sort={params.sort_order})"
        )
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, float(params.max_comments), message="Fetching comments...")

        try:
            result = await client.fetch_comments_paginated(
                media_id=media_id,
                max_comments=params.max_comments,
                sort_order=params.sort_order,
                cache_ttl=config.cache_comments_ttl,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        raw_comments = result.get("comments") or []
        caption_raw = result.get("caption")
        comment_count = result.get("comment_count", 0)
        pages_fetched = result.get("pages_fetched", 1)
        has_more = result.get("has_more", False)

        comments = parse_comments(
            raw_comments=raw_comments,
            caption_raw=caption_raw,
            max_comments=params.max_comments,
        )
        actual = [c for c in comments if not c.is_caption]

        out = format_comments_markdown(
            shortcode=shortcode,
            post_url=post_url,
            comment_count=comment_count,
            comments=comments,
            pages_fetched=pages_fetched,
            sort_order=params.sort_order,
        )

        if has_more:
            out += (
                f"\n\n*Showing {len(actual)} of {comment_count:,} total comments. "
                f"Increase max_comments to fetch more.*"
            )

        elapsed = time.perf_counter() - _t0
        await ctx.info(
            f"Post {shortcode} comments ✓ — {len(actual)} comments, "
            f"{pages_fetched} page(s) — {elapsed:.2f}s"
        )
        await ctx.report_progress(
            float(len(actual)), float(params.max_comments), message="Done"
        )
        return out
