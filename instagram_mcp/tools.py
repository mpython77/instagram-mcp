"""
MCP Tool registration — 26 tools, optimised for LLM agents.

AUTH TIERS:
  🌐 ANONYMOUS (15 tools) — no login, no cookies, fully public
  🔐 AUTHENTICATED (11 tools) — requires cookies.txt with a valid Instagram session
  🌐/🔐 AUTO-MODE (1 tool) — works anonymously, upgrades when cookies present

Tools:
  --- Profile & Feed ---
  1.  instagram_profile          — 🌐 Profile + optional feed tags + activity status
  2.  instagram_feed_deep        — 🌐 Deep paginated feed analysis (up to 200 posts)
  3.  instagram_bulk_check       — 🌐 Up to 20 profiles in parallel
  4.  instagram_compare_profiles — 🌐 Side-by-side comparison (2-5 accounts)
  --- Analysis ---
  5.  instagram_analyze_engagement  — 🌐 ER%, content mix, best days, top posts
  6.  instagram_find_collab_network — 🌐 Collaboration/mention network map
  --- Content ---
  7.  instagram_post             — 🌐 Full details for a single post by shortcode/URL
  8.  instagram_post_comments    — 🌐 Comments on a post with per-comment like counts
  9.  instagram_hashtag          — 🌐/🔐 Hashtag trending posts (auto-upgrades with auth)
  10. instagram_hashtag_deep     — 🌐 Deep hashtag analytics: top accounts, best hour
  11. instagram_location_posts   — 🌐 Posts by location ID
  12. instagram_audio_reels      — 🌐 Reels clustered by trending audio
  13. instagram_post_bulk        — 🌐 Parallel fetch of multiple posts
  --- Social Graph ---
  14. instagram_search           — 🔐 Search accounts and hashtags by keyword
  15. instagram_followers_list   — 🔐 Recent followers of an account
  16. instagram_following_list   — 🔐 Accounts a user follows (full pagination)
  17. instagram_post_likers      — 🔐 Users who liked a post
  18. instagram_tagged_by        — 🔐 Posts where OTHERS tagged this account
  19. instagram_reposts          — 🔐 Content this account reposted from others
  20. instagram_reels             — 🔐 Account's own reels with play counts
  21. instagram_stories          — 🔐 Active Stories with music, mentions, linked posts
  22. instagram_highlights       — 🔐 Story highlight collections
  23. instagram_similar_accounts — 🌐 Discover accounts similar to a given one
  --- Intelligence ---
  24. instagram_niche_top        — 🌐 Top accounts in a niche by engagement
  25. instagram_account_report   — 🌐 Full account intelligence report
  --- Batch ---
  26. instagram_batch_scrape     — 🌐 Large-scale scraping (up to 2000 profiles)
  --- Server ---
  27. instagram_server           — 🌐 Server diagnostics + cache management

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
from .exceptions import FetchError, InstagramMCPError
from .exporter import JsonExporter
from .formatter import (
    format_account_report_markdown,
    format_account_status_markdown,
    format_audio_reels_markdown,
    format_upload_result_markdown,
    format_bulk_results_markdown,
    format_collab_network_markdown,
    format_compare_profiles_markdown,
    format_deep_feed_markdown,
    format_diagnostics_markdown,
    format_engagement_analysis_markdown,
    format_followers_markdown,
    format_following_markdown,
    format_hashtag_deep_markdown,
    format_hashtag_markdown,
    format_highlights_markdown,
    format_location_posts_markdown,
    format_niche_top_markdown,
    format_post_bulk_markdown,
    format_post_likers_markdown,
    format_search_markdown,
    format_similar_accounts_markdown,
    format_post_markdown,
    format_posts_markdown,
    format_profile_markdown,
    format_profile_with_tags_markdown,
    format_comments_markdown,
    format_reels_markdown,
    format_reposts_markdown,
    format_stories_markdown,
    format_tagged_by_markdown,
    format_dm_inbox_markdown,
    format_dm_thread_markdown,
    format_dm_send_markdown,
    format_schedule_markdown,
    format_monitor_markdown,
    format_oauth_markdown,
    format_sessions_markdown,
)
from .models import (
    AccountReportInput,
    AudioReelsInput,
    UploadPhotoInput,
    BulkProfilesInput,
    CollabNetworkInput,
    CompareProfilesInput,
    DateRange,
    DeepFeedInput,
    EngagementAnalysisInput,
    FeedTagResult,
    FollowersInput,
    FollowingInput,
    HashtagDeepInput,
    HashtagInput,
    HighlightsInput,
    LocationPostsInput,
    NicheTopInput,
    PostBulkInput,
    PostLikersInput,
    SearchInput,
    SimilarAccountsInput,
    InstagramProfile,
    PostCommentsInput,
    PostInput,
    ProfileInput,
    ReelsInput,
    RepostsInput,
    ServerInput,
    StoriesInput,
    TaggedByInput,
    DownloadInput,
    DMInboxInput,
    DMThreadInput,
    DMSendInput,
    DMReactInput,
    DMUnsendInput,
    DMMarkSeenInput,
    PostCommentInput,
    PostSaveInput,
    UserSearchInput,
    UserFollowersInput,
    BlockUserInput,
    LikePostInput,
    FollowUserInput,
    StoryMarkSeenInput,
    StoryReplyInput,
    EditProfileInput,
    ScheduleInput,
    MonitorInput,
    OAuthInput,
    SessionInput,
)
from .parser import (
    check_dead_account,
    check_dead_account_from_items,
    parse_comments,
    parse_feed_items,
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
    profile,
    max_posts: int,
    max_age_days: int,
    date_range,
    ctx: Context,
) -> tuple:
    """
    Fetch feed items via v1/feed/user with max_id pagination.
    Reports per-page progress to ctx. Returns (items, effective_max).
    """
    effective_max = min(max_posts, config.max_pagination_posts)
    since_ts = date_range.since if date_range else None

    await ctx.report_progress(0.0, float(effective_max), message=f"Starting: up to {effective_max} posts...")

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
    await ctx.report_progress(float(len(items)), float(effective_max), message=f"Done: {len(items)} posts fetched")
    return items, effective_max


# ═════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

def register_tools(
    mcp: FastMCP,
    client: InstagramClient,
    config: MCPConfig,
    exporter: JsonExporter,
) -> None:
    """Register all MCP tools, gated by config.enabled_toolsets.

    Toolset groups (configurable via INSTAGRAM_MCP_TOOLSETS env var):
      • profile       — instagram_profile, instagram_feed_deep, instagram_bulk_check,
                        instagram_compare_profiles
      • analysis      — instagram_analyze_engagement, instagram_find_collab_network
      • content       — instagram_post, instagram_post_comments, instagram_hashtag,
                        instagram_location_posts, instagram_audio_reels
      • social_graph  — instagram_followers_list, instagram_following_list,
                        instagram_post_likers, instagram_search, instagram_tagged_by,
                        instagram_reposts, instagram_reels, instagram_stories,
                        instagram_highlights
      • batch         — instagram_batch_scrape
      • server        — instagram_server (always enabled regardless of selection)

    Default: all toolsets registered. When INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES=1
    and no cookies are loaded, auth-required tools are skipped.
    """

    # Authentication availability — auth-only tools may be hidden if no cookies.
    _is_authed = bool(
        getattr(getattr(client, "cookie_manager", None), "is_authenticated", False)
    )
    _enabled_toolsets = set(config.enabled_toolsets or {"all"})
    _all_enabled = "all" in _enabled_toolsets or not _enabled_toolsets
    _hide_auth = bool(config.hide_auth_when_no_cookies) and not _is_authed

    def _enabled(toolset: str, requires_auth: bool = False) -> bool:
        """Return True if tool in this toolset should be registered.

        - "server" toolset is always on (diagnostics).
        - Tools requiring auth are hidden when hide_auth_when_no_cookies and no cookies.
        """
        if requires_auth and _hide_auth:
            return False
        if toolset == "server":
            return True
        if _all_enabled:
            return True
        return toolset in _enabled_toolsets

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
            feed_tags_result = None  # set in else branch below when include_feed=True
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
                    await ctx.info(f"@{params.username}: fetching {params.max_feed_posts} posts...")
                    feed_items = await client.fetch_feed_items(
                        profile.user_id, params.max_feed_posts,
                        since_timestamp=_since,
                    )
                    feed_tags_result = parse_feed_items(
                        feed_items, params.max_feed_posts, params.max_age_days,
                        since_timestamp=_since, until_timestamp=_until,
                    )
                    if params.check_alive:
                        is_dead, last_post_days = check_dead_account_from_items(
                            feed_items, profile.posts_count, params.dead_threshold_days
                        )

                out = format_profile_with_tags_markdown(profile, feed_tags_result, is_dead, last_post_days)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} ✓ — {elapsed:.2f}s")
        await exporter.save("profile", params.username, {
            "profile": profile,
            "feed_tags": feed_tags_result,
            "is_dead": is_dead,
            "last_post_days": last_post_days,
        }, elapsed)
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

        The first 12 posts come from the profile request at no extra cost.
        Additional posts are fetched via v1/feed/user (50 posts per page):
        100 posts ≈ 2 extra requests. Progress is reported via MCP progress notifications.

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

            items, effective_max = await _paginate_feed(
                client, config, profile,
                params.max_posts, params.max_age_days, date_range, ctx,
            )

            feed_tags = parse_feed_items(
                items, effective_max, params.max_age_days,
                since_timestamp=date_range.since if date_range else None,
                until_timestamp=date_range.until if date_range else None,
            )
            is_dead, last_post_days = check_dead_account_from_items(items, profile.posts_count)

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
            f"{len(feed_tags.tags)} tags — {elapsed:.2f}s"
        )
        await exporter.save("feed_deep", params.username, {
            "profile": profile,
            "feed_tags": feed_tags,
            "is_dead": is_dead,
            "last_post_days": last_post_days,
            "posts_fetched": len(feed_tags.posts),
        }, elapsed)
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
                    default 90), since_date / until_date (DD.MM.YYYY) to restrict
                    the analysis to a specific time window
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        _date_suffix = f" [{params.since_date}→{params.until_date}]" if (params.since_date or params.until_date) else ""
        await ctx.info(f"instagram_analyze_engagement: @{params.username} ({params.max_posts} posts, {params.max_age_days}d{_date_suffix})")
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

            _since = params.resolved_since()
            _until = params.resolved_until()
            date_range = DateRange(since=_since, until=_until) if (_since or _until) else None

            items, effective_max = await _paginate_feed(
                client, config, profile,
                params.max_posts, params.max_age_days, date_range, ctx,
            )

            feed_tags = parse_feed_items(
                items, effective_max, params.max_age_days,
                since_timestamp=date_range.since if date_range else None,
                until_timestamp=date_range.until if date_range else None,
            )

            out = format_engagement_analysis_markdown(profile, feed_tags.posts)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} ✓ — {len(feed_tags.posts)} posts — {elapsed:.2f}s")
        await exporter.save("engagement", params.username, {
            "profile": profile,
            "posts": feed_tags.posts,
            "posts_analyzed": len(feed_tags.posts),
        }, elapsed)
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
                    default 90), min_frequency (1-50, default 1),
                    since_date / until_date (DD.MM.YYYY) to restrict to a time window
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        _date_suffix = f" [{params.since_date}→{params.until_date}]" if (params.since_date or params.until_date) else ""
        await ctx.info(f"instagram_find_collab_network: @{params.username} ({params.max_posts} posts{_date_suffix})")
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

            _since = params.resolved_since()
            _until = params.resolved_until()
            date_range = DateRange(since=_since, until=_until) if (_since or _until) else None

            items, effective_max = await _paginate_feed(
                client, config, profile,
                params.max_posts, params.max_age_days, date_range, ctx,
            )

            feed_tags = parse_feed_items(
                items, effective_max, params.max_age_days,
                since_timestamp=date_range.since if date_range else None,
                until_timestamp=date_range.until if date_range else None,
            )

            out = format_collab_network_markdown(profile, feed_tags.posts, params.min_frequency)

        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} ✓ — {len(feed_tags.posts)} posts — {elapsed:.2f}s")
        await exporter.save("collab_network", params.username, {
            "profile": profile,
            "posts": feed_tags.posts,
            "min_frequency": params.min_frequency,
        }, elapsed)
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
        _compare_subject = "+".join(cleaned)
        if len(_compare_subject) > 60:
            _compare_subject = _compare_subject[:57] + "..."
        await exporter.save("compare", _compare_subject, {
            "profiles": [
                {"profile": p, "is_dead": is_dead, "last_post_days": days}
                for p, is_dead, days in entries
            ],
            "count": len(entries),
        }, elapsed)
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
        await exporter.save("bulk_check", f"bulk_{len(sanitized)}", {
            "results": parsed_results,
            "total": len(parsed_results),
            "found": found,
        }, elapsed)
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
                raise _tool_error(f"Cookie reload failed: {e}", "internal_error")

        else:
            raise _tool_error(
                f"Unknown action: '{params.action}'. Valid actions: 'status', 'clear_cache', 'clear_user', 'reload_cookies'.",
                "validation_error",
                "Set action to one of: 'status', 'clear_cache', 'clear_user', 'reload_cookies'.",
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
        await exporter.save("tagged_by", params.username, {
            "profile": profile,
            "tagged_posts": tagged_posts,
            "total": len(tagged_posts),
        }, elapsed)
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
        await exporter.save("reposts", params.username, {
            "profile": profile,
            "repost_items": repost_items,
            "total": len(repost_items),
            "pages_fetched": pages_fetched,
        }, elapsed)
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
        await exporter.save("post", shortcode, {"post": info}, elapsed)
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
        await exporter.save("reels", params.username, {
            "profile": profile,
            "reels": reel_items,
            "total": len(reel_items),
            "pages_fetched": pages_fetched,
        }, elapsed)
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
        await exporter.save("comments", shortcode, {
            "shortcode": shortcode,
            "post_url": post_url,
            "comment_count": comment_count,
            "comments": comments,
            "pages_fetched": pages_fetched,
            "sort_order": params.sort_order,
        }, elapsed)
        return out

    # ── Hashtag ───────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_hashtag",
        annotations={
            "title": "Instagram Hashtag Top Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_hashtag(params: HashtagInput, ctx: Context) -> str:
        """
        🌐/🔐 AUTO-MODE — uses auth if cookies.json present, otherwise anonymous.

        Fetch trending/top posts for an Instagram hashtag.

        ┌──────────────────────────────────────────────────────────────────────┐
        │ TWO MODES                                                            │
        │                                                                      │
        │ 🔐 AUTH (cookies.json present) — RECOMMENDED                         │
        │  • Up to 300 posts (paginated, 30/page)                              │
        │  • Full like counts + play counts + comment counts                   │
        │  • Works for ALL hashtags incl. sensitive ones (#swimwear etc.)      │
        │                                                                      │
        │ 🌐 ANON (no cookies) — fallback                                       │
        │  • Max 12 posts (Instagram SSR limit)                                │
        │  • No like counts                                                    │
        │  • Blocked for some hashtags (#swimwear, #bikini, #fitness …)        │
        └──────────────────────────────────────────────────────────────────────┘

        Returns per post: username, verified, like count, play count,
        comment count, content type, shortcode + URL, caption.

        Args:
            params: tag (hashtag without #), max_posts (default 30, max 300)
        """
        tag = params.tag
        auth_available = (
            client.cookie_manager is not None
            and getattr(client.cookie_manager, "is_authenticated", False)
        )
        mode = "🔐 auth" if auth_available else "🌐 anon"

        await ctx.info(f"instagram_hashtag: #{tag} ({mode}, max={params.max_posts})")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, float(params.max_posts), message=f"Fetching #{tag}...")

        try:
            result = await client.fetch_hashtag(
                tag=tag,
                max_posts=params.max_posts,
                cache_ttl=config.cache_profile_ttl,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            return (
                f"**#{tag}** — not found or no public posts available.\n\n"
                f"*The hashtag may not exist, or Instagram may have restricted access.*"
            )

        posts = result.get("posts") or []
        related = result.get("related_searches") or []
        has_more = result.get("has_more", False)

        elapsed = time.perf_counter() - _t0
        auth_used = result.get("auth_used", False)
        await ctx.info(
            f"Hashtag #{tag} ✓ — {len(posts)} posts "
            f"({'auth' if auth_used else 'anon'}) — {elapsed:.2f}s"
        )
        await ctx.report_progress(float(len(posts)), float(params.max_posts), message="Done")

        await exporter.save("hashtag", tag, {
            "tag":             tag,
            "posts":           posts,
            "has_more":        has_more,
            "related_searches": related,
            "auth_used":       auth_used,
        }, elapsed)

        return format_hashtag_markdown(
            tag=tag,
            posts=posts,
            related_searches=related,
            has_more=has_more,
            auth_used=auth_used,
        )

    # ── Search ────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_search",
        annotations={
            "title": "Instagram Search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_search(params: SearchInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Search Instagram for accounts and/or hashtags.

        Finds users by name/username and hashtags by keyword in one call.

        ┌──────────────────────────────────────────────────────────────────────┐
        │ context options:                                                     │
        │  'blended'  — users + hashtags (default, most useful)               │
        │  'user'     — accounts only                                          │
        │  'hashtag'  — hashtags only                                          │
        └──────────────────────────────────────────────────────────────────────┘

        Returns per user: username, full name, verified, private, follower count,
        whether you follow them, whether they have a recent reel.
        Returns per hashtag: name, total post count.

        Use cases:
          - Find an account when you only know a name: query='cristiano'
          - Discover hashtags for a topic: query='football', context='hashtag'
          - Check if a specific username exists: query='nike', context='user'

        Args:
            params: query (search term), context ('blended'/'user'/'hashtag')
        """
        await ctx.info(f"instagram_search: '{params.query}' context={params.context}")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Searching '{params.query}'...")

        try:
            result = await client.fetch_search(
                query=params.query,
                context=params.context,
                cache_ttl=config.cache_profile_ttl,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            return (
                "**instagram_search requires authentication.**\n\n"
                "Please provide a valid `cookies.txt` or `cookies.json` with an active Instagram session.\n"
                "Without auth, Instagram returns 401 for all search requests."
            )

        elapsed = time.perf_counter() - _t0
        users    = result.get("users", [])
        hashtags = result.get("hashtags", [])
        has_more = result.get("has_more", False)

        await ctx.info(
            f"Search '{params.query}' ✓ — {len(users)} users, {len(hashtags)} hashtags — {elapsed:.2f}s"
        )
        await ctx.report_progress(1.0, 1.0, message="Done")

        await exporter.save("search", params.query, {
            "query":    params.query,
            "context":  params.context,
            "users":    users,
            "hashtags": hashtags,
            "has_more": has_more,
        }, elapsed)

        return format_search_markdown(
            query=params.query,
            users=users,
            hashtags=hashtags,
            context=params.context,
            has_more=has_more,
        )

    # ── Followers List ────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_followers_list",
        annotations={
            "title": "Instagram Followers List",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_followers_list(params: FollowersInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Fetch recent followers of an Instagram account.

        ⚠️ Instagram API limitation: only ~50 most recent followers are returned
        for accounts other than your own. Pagination is not available.

        Returns per follower: username, full name, verified, private,
        mutual follow status, recent reel activity.

        Use cases:
          - See who recently followed an account
          - Analyse follower demographics (verified ratio, private ratio)
          - Find mutual connections

        Args:
            params: username (without @)
        """
        await ctx.info(f"instagram_followers_list: @{params.username}")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Fetching followers of @{params.username}...")

        profile = await client.fetch_user(params.username)
        if profile is None:
            return f"**@{params.username}** — account not found or private."
        user_pk = profile.get("pk") or profile.get("id") or ""
        if not user_pk:
            return f"**@{params.username}** — could not resolve user ID."

        try:
            result = await client.fetch_followers(
                user_pk=str(user_pk),
                max_users=params.max_users,
                cache_ttl=config.cache_profile_ttl,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            return (
                "**instagram_followers_list requires authentication.**\n\n"
                "Please provide a valid `cookies.txt` with an active Instagram session."
            )

        elapsed = time.perf_counter() - _t0
        users         = result.get("users", [])
        has_more      = result.get("has_more", False)
        should_limit  = result.get("should_limit", False)
        pages_fetched = result.get("pages_fetched", 1)

        await ctx.info(
            f"Followers @{params.username} ✓ — {len(users)} users, "
            f"{pages_fetched} page(s) {'[limited]' if should_limit else ''} — {elapsed:.2f}s"
        )
        await ctx.report_progress(float(len(users)), float(params.max_users), message="Done")

        await exporter.save("followers", params.username, {
            "username":      params.username,
            "user_pk":       str(user_pk),
            "users":         users,
            "has_more":      has_more,
            "should_limit":  should_limit,
            "pages_fetched": pages_fetched,
        }, elapsed)

        return format_followers_markdown(
            username=params.username,
            user_pk=str(user_pk),
            users=users,
            has_more=has_more,
            should_limit=should_limit,
            pages_fetched=pages_fetched,
        )

    # ── Following List ────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_following_list",
        annotations={
            "title": "Instagram Following List",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_following_list(params: FollowingInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Fetch accounts that a user is following (with pagination).

        Unlike followers, following list supports full pagination (50 per page).
        Use max_users to control how many to fetch (default 200, max 1000).

        Returns per account: username, full name, verified, private,
        mutual follow status, favorite marker (⭐), recent reel activity.

        Use cases:
          - Map an account's network and partners
          - Find brand ambassadors or collaborators they follow
          - Identify who follows back (mutual)

        Args:
            params: username (without @), max_users (default 200)
        """
        await ctx.info(f"instagram_following_list: @{params.username} max={params.max_users}")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, float(params.max_users), message=f"Fetching following of @{params.username}...")

        profile = await client.fetch_user(params.username)
        if profile is None:
            return f"**@{params.username}** — account not found or private."
        user_pk = profile.get("pk") or profile.get("id") or ""
        if not user_pk:
            return f"**@{params.username}** — could not resolve user ID."

        try:
            result = await client.fetch_following(
                user_pk=str(user_pk),
                max_users=params.max_users,
                cache_ttl=config.cache_profile_ttl,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            return (
                "**instagram_following_list requires authentication.**\n\n"
                "Please provide a valid `cookies.txt` with an active Instagram session."
            )

        elapsed = time.perf_counter() - _t0
        users         = result.get("users", [])
        has_more      = result.get("has_more", False)
        pages_fetched = result.get("pages_fetched", 1)

        await ctx.info(f"Following @{params.username} ✓ — {len(users)} users, {pages_fetched} pages — {elapsed:.2f}s")
        await ctx.report_progress(float(len(users)), float(params.max_users), message="Done")

        await exporter.save("following", params.username, {
            "username":      params.username,
            "user_pk":       str(user_pk),
            "users":         users,
            "has_more":      has_more,
            "pages_fetched": pages_fetched,
        }, elapsed)

        return format_following_markdown(
            username=params.username,
            users=users,
            has_more=has_more,
            pages_fetched=pages_fetched,
        )

    # ── Post Likers ───────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_post_likers",
        annotations={
            "title": "Instagram Post Likers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_post_likers(params: PostLikersInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Fetch users who liked an Instagram post.

        Returns ~98 likers (Instagram API limit, no pagination available).
        Also shows total like count for the post.

        Returns per liker: username, full name, verified, private,
        mutual follow status (following/followed_by), recent reel activity.

        Use cases:
          - Identify engaged followers and fans
          - Find influencers who engaged with a post
          - Audience overlap analysis between accounts

        Args:
            params: post (shortcode like 'DXUoQBqiCrY' or full post URL)
        """
        await ctx.info(f"instagram_post_likers: {params.post}")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Fetching likers for {params.post}...")

        try:
            result = await client.fetch_post_likers(
                shortcode=params.post,
                cache_ttl=config.cache_profile_ttl,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            return (
                "**instagram_post_likers requires authentication.**\n\n"
                "Please provide a valid `cookies.txt` with an active Instagram session."
            )

        elapsed = time.perf_counter() - _t0
        shortcode  = result.get("shortcode", params.post)
        users      = result.get("users", [])
        user_count = result.get("user_count", 0)

        await ctx.info(f"Likers {shortcode} ✓ — {len(users)} shown / {user_count:,} total — {elapsed:.2f}s")
        await ctx.report_progress(1.0, 1.0, message="Done")

        await exporter.save("post_likers", shortcode, {
            "shortcode":  shortcode,
            "user_count": user_count,
            "users":      users,
        }, elapsed)

        return format_post_likers_markdown(
            shortcode=shortcode,
            users=users,
            user_count=user_count,
        )

    # ── Stories ───────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_stories",
        annotations={
            "title": "Instagram Stories",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def instagram_stories(params: StoriesInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch an account's currently active Instagram Stories.

        Returns per story: media type (image/video), exact timestamp, expiry time,
        video duration, music (title + artist), mention stickers, hashtag stickers,
        linked post sticker, thumbnail URL, accessibility caption, paid partnership flag.

        Stories expire after 24 hours — results are cached for 2 minutes.
        Use for brand monitoring, competitor tracking, real-time content analysis.

        Args:
            params: username
        """
        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.txt not found. Place a Netscape-format cookies.txt with your "
                "Instagram session in the working directory, or set INSTAGRAM_MCP_COOKIES "
                "env var to its path."
            )
            raise _tool_error(
                f"instagram_stories requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.txt, and restart the MCP server.",
            )

        await ctx.info(f"instagram_stories: @{params.username}")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Fetching stories for @{params.username}...")

        try:
            result = await client.fetch_stories(
                username=params.username,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            raise ToolError("No auth — stories requires authenticated session.")

        items = result.get("items") or []
        story_count = result.get("story_count", 0)
        expiring_at = result.get("expiring_at", 0)
        is_verified = result.get("is_verified", False)

        out = format_stories_markdown(
            username=params.username,
            items=items,
            story_count=story_count,
            expiring_at=expiring_at,
            is_verified=is_verified,
        )

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} stories ✓ — {story_count} stories — {elapsed:.2f}s")
        await ctx.report_progress(1.0, 1.0, message="Done")
        await exporter.save("stories", params.username, result, elapsed)
        return out

    # ── Highlights ────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_highlights",
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
        },
    )
    async def instagram_highlights(params: HighlightsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch an account's Highlights — curated story collections that stay permanently
        on their profile.

        Two modes:
        - Tray only (default): returns all highlight titles, story counts, cover images,
          and creation dates in one fast API call.
        - With media (include_media=True): also fetches the actual story items inside
          each highlight (up to max_media_highlights). Each item has the same rich
          fields as instagram_stories: media type, timestamp, music, mention stickers,
          link stickers, poll stickers, linked post stickers.

        Use for: brand audit (what topics they highlight), content strategy analysis,
        influencer research, archival monitoring.

        Args:
            params: username, max_highlights (1-200, default 50),
                    include_media (bool, default False),
                    max_media_highlights (1-10, default 3)
        """
        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.txt not found. Place a Netscape-format cookies.txt with your "
                "Instagram session in the working directory, or set INSTAGRAM_MCP_COOKIES "
                "env var to its path."
            )
            raise _tool_error(
                f"instagram_highlights requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.txt, and restart the MCP server.",
            )

        await ctx.info(f"instagram_highlights: @{params.username} (max={params.max_highlights}, media={params.include_media})")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Fetching highlights for @{params.username}...")

        try:
            result = await client.fetch_highlights(
                username=params.username,
                max_highlights=params.max_highlights,
                include_media=params.include_media,
                max_media_highlights=params.max_media_highlights,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            raise ToolError("No auth — highlights requires authenticated session.")

        highlights = result.get("highlights") or []
        highlight_count = result.get("highlight_count", 0)
        is_verified = result.get("is_verified", False)

        out = format_highlights_markdown(
            username=params.username,
            highlights=highlights,
            highlight_count=highlight_count,
            is_verified=is_verified,
        )

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} highlights ✓ — {highlight_count} highlights — {elapsed:.2f}s")
        await ctx.report_progress(1.0, 1.0, message="Done")
        await exporter.save("highlights", params.username, result, elapsed)
        return out

    # ── Location Posts ────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_location_posts",
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
        },
    )
    async def instagram_location_posts(params: LocationPostsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch top/ranked posts for an Instagram location.

        Two modes:
        - By location_id: provide the numeric Instagram location ID directly
          (e.g. '213385402' for New York).
        - By location_name: provide a name/search query; the tool will resolve
          the location ID automatically via Instagram's location search.

        Returns a ranked table of posts with likes, comments, play counts,
        author info, and post links.

        Args:
            params: location_id (numeric ID) or location_name (search query),
                    max_posts (1-100, default 33)
        """
        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.txt not found. Place a Netscape-format cookies.txt with your "
                "Instagram session in the working directory, or set INSTAGRAM_MCP_COOKIES "
                "env var to its path."
            )
            raise _tool_error(
                f"instagram_location_posts requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.txt, and restart the MCP server.",
            )

        if not params.location_id and not params.location_name:
            raise _tool_error(
                "Provide either location_id (numeric) or location_name (search query).",
                "missing_param",
                "Example: location_id='213385402' or location_name='Tashkent'",
            )

        label = params.location_id or params.location_name
        await ctx.info(f"instagram_location_posts: {label!r} (max={params.max_posts})")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Fetching location posts for {label!r}...")

        try:
            result = await client.fetch_location_posts(
                location_id=params.location_id,
                location_name=params.location_name,
                max_posts=params.max_posts,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            raise _tool_error(
                "No auth — instagram_location_posts requires authenticated session.",
                "auth_required",
            )

        posts         = result.get("posts") or []
        post_count    = result.get("post_count", 0)
        location_id   = result.get("location_id", "")
        location_name = result.get("location_name", "")
        more_available = result.get("more_available", False)

        out = format_location_posts_markdown(
            location_id=location_id,
            location_name=location_name,
            posts=posts,
            post_count=post_count,
            more_available=more_available,
        )

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"location_posts {label!r} ✓ — {post_count} posts — {elapsed:.2f}s")
        await ctx.report_progress(1.0, 1.0, message="Done")
        await exporter.save("location_posts", label, result, elapsed)
        return out

    # ── Audio Reels ───────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_audio_reels",
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
        },
    )
    async def instagram_audio_reels(params: AudioReelsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.

        Fetch reels that use a specific Instagram audio track.

        Provide the audio_cluster_id (found in a reel's music metadata or
        from the audio page URL at instagram.com/reels/audio/{id}/).

        Returns a table of reels using this audio with likes, play counts,
        author info, and links.

        Args:
            params: audio_cluster_id (required), max_reels (1-100, default 24)
        """
        cm = client.cookie_manager
        if cm is None or not cm.is_authenticated:
            setup_msg = cm.auth_required_error() if cm else (
                "cookies.txt not found. Place a Netscape-format cookies.txt with your "
                "Instagram session in the working directory, or set INSTAGRAM_MCP_COOKIES "
                "env var to its path."
            )
            raise _tool_error(
                f"instagram_audio_reels requires authentication.\n\n{setup_msg}",
                "auth_required",
                "Export cookies from your browser after logging in to Instagram, "
                "save as cookies.txt, and restart the MCP server.",
            )

        await ctx.info(f"instagram_audio_reels: audio_cluster_id={params.audio_cluster_id!r} (max={params.max_reels})")
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, 1.0, message=f"Fetching audio reels for {params.audio_cluster_id!r}...")

        try:
            result = await client.fetch_audio_reels(
                audio_cluster_id=params.audio_cluster_id,
                max_reels=params.max_reels,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        if result is None:
            raise _tool_error(
                "No auth — instagram_audio_reels requires authenticated session.",
                "auth_required",
            )

        posts            = result.get("posts") or []
        audio_cluster_id = result.get("audio_cluster_id", "")
        music_title      = result.get("music_title", "")
        music_artist     = result.get("music_artist", "")
        total_reels_str  = result.get("total_reels_str", "")
        more_available   = result.get("more_available", False)

        out = format_audio_reels_markdown(
            audio_cluster_id=audio_cluster_id,
            music_title=music_title,
            music_artist=music_artist,
            posts=posts,
            total_reels_str=total_reels_str,
            more_available=more_available,
        )

        elapsed = time.perf_counter() - _t0
        await ctx.info(
            f"audio_reels {audio_cluster_id!r} ✓ — {len(posts)} reels — {elapsed:.2f}s"
        )
        await ctx.report_progress(1.0, 1.0, message="Done")
        await exporter.save("audio_reels", audio_cluster_id, result, elapsed)
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 20: instagram_hashtag_deep
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("content"):
        @mcp.tool(
            name="instagram_hashtag_deep",
            annotations={
                "title": "Instagram Hashtag Deep Analysis",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
        async def instagram_hashtag_deep(params: HashtagDeepInput, ctx: Context) -> str:
            """
            🌐/🔐 AUTO-MODE — deep hashtag analytics.

            Fetches up to 500 posts for a hashtag and computes:
            • Top accounts ranked by avg engagement (likes + comments)
            • Content type breakdown (photo / video / carousel)
            • Best posting hour (UTC)
            • Average and total likes, comments, views

            🌐 Anon mode (no cookies): 12 posts max — limited analytics.
            🔐 Auth mode (cookies present): full pagination up to 500 posts — accurate data.

            Use this instead of instagram_hashtag when you need:
            - "Who dominates #fitness?" → top_accounts table
            - "What content type performs best in #travel?" → media_types breakdown
            - "Best time to post for #food?" → best_hour_utc

            Params: tag (required), max_posts (1-500, default 90), top_n (1-50, default 15)

            When NOT to use:
            - Just browsing top posts: use instagram_hashtag instead (faster, simpler)
            - Need individual post details: use instagram_post or instagram_post_bulk
            """
            if not params.tag:
                raise _tool_error("tag is required", "validation_error", "Provide a hashtag without #.")

            await ctx.info(f"instagram_hashtag_deep: #{params.tag} max_posts={params.max_posts}")
            _t0 = time.perf_counter()
            await ctx.report_progress(0.0, 1.0, message=f"Fetching #{params.tag} posts...")

            try:
                result = await client.fetch_hashtag(params.tag, max_posts=params.max_posts)
            except Exception as e:
                raise _exception_to_tool_error(e)

            if result is None:
                raise _tool_error(
                    f"Hashtag #{params.tag} not found or unavailable.",
                    "not_found",
                    "Check the hashtag spelling.",
                )

            posts      = result.get("posts") or []
            auth_used  = result.get("auth_used", False)

            await ctx.report_progress(0.9, 1.0, message=f"Computing analytics on {len(posts)} posts...")
            out = format_hashtag_deep_markdown(params.tag, posts, auth_used, top_n=params.top_n)

            elapsed = time.perf_counter() - _t0
            await ctx.info(f"hashtag_deep #{params.tag} ✓ — {len(posts)} posts — {elapsed:.2f}s")
            await ctx.report_progress(1.0, 1.0, message="Done")
            await exporter.save("hashtag_deep", params.tag, result, elapsed)
            return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 21: instagram_post_bulk
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("content"):
        @mcp.tool(
            name="instagram_post_bulk",
            annotations={
                "title": "Instagram Post Bulk Fetch",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
        async def instagram_post_bulk(params: PostBulkInput, ctx: Context) -> str:
            """
            🌐 NO LOGIN REQUIRED — fetch multiple posts in parallel.

            Given a list of shortcodes or post URLs, fetches all posts in parallel
            and returns a summary table with likes, comments, views, captions,
            location, music, and hashtags.

            Params:
              shortcodes  — list of shortcodes or post URLs (max 50)
              max_concurrency — parallel requests (1-20, default 5)

            Examples:
              shortcodes=["DXjuqH9nDVE", "C1abc123XYZ"]
              shortcodes=["https://www.instagram.com/p/DXjuqH9nDVE/"]

            When NOT to use:
            - Single post: use instagram_post instead
            - Need comments: use instagram_post_comments per shortcode
            - Need all posts from a user: use instagram_feed_deep
            """
            if not params.shortcodes:
                raise _tool_error(
                    "shortcodes list is empty.",
                    "validation_error",
                    "Provide at least one post shortcode or URL.",
                )

            await ctx.info(f"instagram_post_bulk: {len(params.shortcodes)} shortcodes, concurrency={params.max_concurrency}")
            _t0 = time.perf_counter()
            await ctx.report_progress(0.0, 1.0, message=f"Fetching {len(params.shortcodes)} posts...")

            try:
                results = await client.fetch_post_bulk(
                    shortcodes=params.shortcodes,
                    max_concurrency=params.max_concurrency,
                )
            except Exception as e:
                raise _exception_to_tool_error(e)

            ok_count = sum(1 for r in results if r.get("ok"))
            await ctx.report_progress(0.9, 1.0, message=f"Formatting {ok_count}/{len(results)} results...")
            out = format_post_bulk_markdown(results)

            elapsed = time.perf_counter() - _t0
            await ctx.info(f"post_bulk ✓ — {ok_count}/{len(results)} OK — {elapsed:.2f}s")
            await ctx.report_progress(1.0, 1.0, message="Done")
            await exporter.save("post_bulk", f"bulk_{len(params.shortcodes)}", {"results": results}, elapsed)
            return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 22: instagram_similar_accounts
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):
        @mcp.tool(
            name="instagram_similar_accounts",
            annotations={
                "title": "Instagram Similar Accounts",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
        async def instagram_similar_accounts(params: SimilarAccountsInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — find accounts similar to a given user.

            Uses Instagram's internal discover/chaining API to return accounts
            that Instagram considers similar (same niche, audience overlap).

            Useful for:
            - Competitor discovery: find all brands competing in the same space
            - Influencer sourcing: start from one account, expand the network
            - Niche mapping: understand who else operates in this niche

            Params:
              username — seed account (without @)
              limit    — max accounts to return (1-50, default 20)

            When NOT to use:
            - No auth / cookies: this tool will return a "no auth" error
            - Need niche ranking by engagement: use instagram_niche_top instead
            """
            try:
                username = sanitize_username(params.username)
            except ValueError as e:
                raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

            await ctx.info(f"instagram_similar_accounts: @{username} limit={params.limit}")
            _t0 = time.perf_counter()
            await ctx.report_progress(0.0, 1.0, message=f"Finding accounts similar to @{username}...")

            try:
                accounts = await client.fetch_similar_accounts(username, limit=params.limit)
            except Exception as e:
                raise _exception_to_tool_error(e)

            if accounts is None:
                raise _tool_error(
                    "instagram_similar_accounts requires an authenticated session.",
                    "auth_required",
                    "Export cookies from your browser after logging in to Instagram, "
                    "save as cookies.txt, and restart the MCP server.",
                )

            out = format_similar_accounts_markdown(username, accounts)
            elapsed = time.perf_counter() - _t0
            await ctx.info(f"similar_accounts @{username} ✓ — {len(accounts)} accounts — {elapsed:.2f}s")
            await ctx.report_progress(1.0, 1.0, message="Done")
            await exporter.save("similar_accounts", username, {"accounts": accounts}, elapsed)
            return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 23: instagram_niche_top
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("content"):
        @mcp.tool(
            name="instagram_niche_top",
            annotations={
                "title": "Instagram Niche Top Accounts",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
        async def instagram_niche_top(params: NicheTopInput, ctx: Context) -> str:
            """
            🌐/🔐 AUTO-MODE — discover top accounts in a hashtag niche.

            Fetches posts for a hashtag, then aggregates them by author and
            ranks accounts by engagement, post count, or total likes.

            Returns a leaderboard of the most active / best-performing accounts
            in that niche — great for influencer discovery and competitor research.

            🌐 Anon mode: 12 posts max — very limited (use auth for useful results).
            🔐 Auth mode: up to 500 posts — accurate niche map.

            Params:
              tag        — hashtag (without #)
              max_posts  — posts to fetch for analysis (12-500, default 90)
              top_n      — accounts to return (3-50, default 15)
              sort_by    — 'engagement' | 'post_count' | 'total_likes' (default 'engagement')

            When NOT to use:
            - Need full profile details for each account: follow up with instagram_bulk_check
            - Need similar accounts by Instagram's algorithm: use instagram_similar_accounts
            """
            await ctx.info(f"instagram_niche_top: #{params.tag} max_posts={params.max_posts} top_n={params.top_n}")
            _t0 = time.perf_counter()
            await ctx.report_progress(0.0, 1.0, message=f"Fetching #{params.tag} posts for niche analysis...")

            try:
                result = await client.fetch_hashtag(params.tag, max_posts=params.max_posts)
            except Exception as e:
                raise _exception_to_tool_error(e)

            if result is None:
                raise _tool_error(
                    f"Hashtag #{params.tag} not found.",
                    "not_found",
                    "Check the hashtag spelling.",
                )

            posts     = result.get("posts") or []
            auth_used = result.get("auth_used", False)

            # Compute per-account stats
            from collections import defaultdict
            acc_data: dict = defaultdict(lambda: {
                "post_count": 0, "total_likes": 0, "total_comments": 0,
                "verified": False, "account_type": 0,
            })
            for p in posts:
                uname = p.get("username", "")
                if not uname:
                    continue
                d = acc_data[uname]
                d["post_count"]     += 1
                d["total_likes"]    += p.get("like_count") or 0
                d["total_comments"] += p.get("comment_count") or 0
                d["verified"]        = d["verified"] or bool(p.get("verified"))
                d["account_type"]    = p.get("account_type", 0) or d["account_type"]

            accounts_list = [
                {
                    "username":       u,
                    "post_count":     d["post_count"],
                    "total_likes":    d["total_likes"],
                    "total_comments": d["total_comments"],
                    "avg_likes":      d["total_likes"] // max(d["post_count"], 1),
                    "avg_comments":   d["total_comments"] // max(d["post_count"], 1),
                    "avg_engagement": (d["total_likes"] + d["total_comments"]) // max(d["post_count"], 1),
                    "verified":       d["verified"],
                    "account_type":   d["account_type"],
                }
                for u, d in acc_data.items()
            ]

            sort_key = {
                "engagement": lambda x: x["avg_engagement"],
                "post_count": lambda x: x["post_count"],
                "total_likes": lambda x: x["total_likes"],
            }.get(params.sort_by, lambda x: x["avg_engagement"])
            accounts_list.sort(key=sort_key, reverse=True)
            top = accounts_list[: params.top_n]

            await ctx.report_progress(0.9, 1.0, message="Formatting niche ranking...")
            out = format_niche_top_markdown(
                tag=params.tag,
                accounts=top,
                posts_analysed=len(posts),
                sort_by=params.sort_by,
                auth_used=auth_used,
            )
            elapsed = time.perf_counter() - _t0
            await ctx.info(f"niche_top #{params.tag} ✓ — {len(top)} accounts from {len(posts)} posts — {elapsed:.2f}s")
            await ctx.report_progress(1.0, 1.0, message="Done")
            await exporter.save("niche_top", params.tag, {"accounts": top, "posts_analysed": len(posts)}, elapsed)
            return out

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 24: instagram_account_report
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("analysis"):
        @mcp.tool(
            name="instagram_account_report",
            annotations={
                "title": "Instagram Account Full Report",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
        async def instagram_account_report(params: AccountReportInput, ctx: Context) -> str:
            """
            🌐 NO LOGIN REQUIRED — full account report in one call.

            Combines instagram_profile + instagram_analyze_engagement +
            optionally instagram_find_collab_network into a single comprehensive
            report. Saves 2-3 tool calls when you need the full picture.

            Returns:
            • Profile section: followers, bio, verification, account type, dead-check
            • Engagement section: ER%, content mix, best days, top posts, top hashtags
            • Collab section (if include_collab=True): tags, mentions, sponsors, coauthors

            Params:
              username       — Instagram username (without @)
              max_posts      — posts to fetch for analysis (1-200, default 50)
              include_collab — include collaboration network (default True)

            When NOT to use:
            - Just need profile: use instagram_profile (faster, one API call)
            - Just need engagement: use instagram_analyze_engagement
            - Need 200+ posts: call instagram_feed_deep + instagram_analyze_engagement separately
            """
            try:
                username = sanitize_username(params.username)
            except ValueError as e:
                raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

            await ctx.info(f"instagram_account_report: @{username} max_posts={params.max_posts}")
            _t0 = time.perf_counter()
            await ctx.report_progress(0.0, 1.0, message=f"Fetching profile for @{username}...")

            # 1. Profile
            try:
                user = await client.fetch_user(username)
            except Exception as e:
                raise _exception_to_tool_error(e)
            if user is None:
                raise _tool_error(
                    f"@{username} not found.",
                    "not_found",
                    "Check the username spelling.",
                )

            from .formatter import (
                format_engagement_analysis_markdown,
                format_collab_network_markdown,
            )

            profile = parse_profile(user, username, config)

            await ctx.report_progress(0.3, 1.0, message=f"Fetching {params.max_posts} posts for @{username}...")

            # 2. Feed — use _paginate_feed for consistent pagination + progress
            items, effective_max = await _paginate_feed(
                client, config, profile,
                params.max_posts, 365, None, ctx,
            )
            feed_tags = parse_feed_items(items, effective_max, 365)
            posts = feed_tags.posts

            # engagement_md from format_engagement_analysis_markdown already includes profile
            engagement_md = format_engagement_analysis_markdown(profile, posts)

            await ctx.report_progress(0.7, 1.0, message="Building collaboration map...")

            # 3. Collab (optional)
            collab_md = None
            if params.include_collab and posts:
                collab_md = format_collab_network_markdown(profile, posts)

            out = format_account_report_markdown(username, engagement_md, collab_md)
            elapsed = time.perf_counter() - _t0
            await ctx.info(f"account_report @{username} ✓ — {len(posts)} posts — {elapsed:.2f}s")
            await ctx.report_progress(1.0, 1.0, message="Done")
            await exporter.save(
                "account_report", username,
                {"profile": user, "posts_fetched": len(posts)},
                elapsed,
            )
            return out

    # ── TOOL 25: instagram_upload_photo ───────────────────────────────────────
    if _enabled("upload", requires_auth=True):

        @mcp.tool(
            name="instagram_upload_photo",
            description=(
                "🔐 Upload 1–10 images to Instagram as a post (single photo or carousel). "
                "Requires authenticated session (cookies.txt). "
                "Supports JPEG natively; PNG requires Pillow (pip install Pillow). "
                "Returns the post URL and shortcode immediately after publishing."
            ),
            annotations={
                "readOnlyHint":    False,
                "destructiveHint": True,
                "idempotentHint":  False,
                "openWorldHint":   True,
            },
        )
        async def instagram_upload_photo(params: UploadPhotoInput, ctx: Context) -> str:
            _t0 = time.perf_counter()
            n = len(params.images)
            post_kind = "carousel" if n > 1 else "single photo"
            await ctx.info(f"upload_photo — {n} image(s) → {post_kind}")
            await ctx.report_progress(0.0, 1.0, message=f"Preparing {n} image(s)...")

            try:
                result = await client.upload_photo(
                    image_paths=params.images,
                    caption=params.caption,
                    disable_comments=params.disable_comments,
                    hide_like_count=params.hide_like_count,
                    location_id=params.location_id,
                )
            except Exception as e:
                raise _exception_to_tool_error(e)

            elapsed = time.perf_counter() - _t0
            shortcode = result.get("shortcode", "")
            post_url  = result.get("url", "")

            await ctx.report_progress(1.0, 1.0, message="Published!")
            await ctx.info(
                f"upload_photo ✓ — {post_kind}, shortcode={shortcode!r}, {elapsed:.2f}s"
            )
            await exporter.save(
                "upload_photo",
                shortcode or "unknown",
                result,
                elapsed,
            )
            return format_upload_result_markdown(result, params.images)

    # ── TOOL 26: instagram_download ───────────────────────────────────────────
    if _enabled("download", requires_auth=True):

        @mcp.tool(
            name="instagram_download",
            description=(
                "🔐 Download all media from an Instagram post to a local directory. "
                "Supports single images, videos/reels, and carousels (all slides). "
                "Requires authenticated session (cookies.txt). "
                "Returns the list of saved file paths and media info."
            ),
        )
        async def instagram_download(params: DownloadInput, ctx: Context) -> str:
            """
            Download all media files from an Instagram post.

            🔐 Requires cookies.txt with a valid Instagram session.

            Fetches full media info via /api/v1/media/{id}/info/ then downloads
            each file (image/video) from Instagram's CDN to save_dir.

            Supports:
              - Single image posts → saves 1 .jpg
              - Video / Reel posts → saves 1 .mp4
              - Carousel posts     → saves N .jpg/.mp4 files (one per slide)

            Args:
                params: post (shortcode or URL), save_dir (output directory)

            Returns:
                Markdown summary with file paths, sizes, and media info.
            """
            import os
            import mimetypes
            from curl_cffi.requests import AsyncSession as _CurlSession

            _t0 = time.perf_counter()
            shortcode = params.post
            save_dir = params.save_dir.rstrip("/")

            await ctx.info(f"instagram_download: {shortcode} → {save_dir}")

            if not os.path.isdir(save_dir):
                raise _tool_error(
                    f"Directory does not exist: {save_dir!r}",
                    "validation_error",
                    "Provide an existing absolute directory path for save_dir.",
                )

            # ── 1. Fetch media info ──────────────────────────────────────────
            await ctx.report_progress(0.1, 1.0, message="Fetching media info…")
            try:
                item = await client.fetch_media_info(shortcode)
            except FetchError as exc:
                raise _tool_error(str(exc), "fetch_error", "Check the shortcode and your session cookies.")

            media_type = item.get("media_type", 0)  # 1=image, 2=video, 8=carousel

            # ── 2. Collect (ext, url) pairs ─────────────────────────────────
            def _best_image(node: dict) -> str:
                iv2 = node.get("image_versions2") or {}
                cands = iv2.get("candidates") or []
                return cands[0]["url"] if cands else ""

            def _best_video(node: dict) -> str:
                # Try video_url first (older posts), then video_versions (reels/clips)
                vurl = node.get("video_url", "")
                if vurl:
                    return vurl
                versions = node.get("video_versions") or []
                if versions:
                    # versions are sorted by bandwidth desc; take highest quality
                    return versions[0].get("url", "")
                return ""

            media_pairs: list = []  # [(ext, url), ...]
            if media_type == 1:
                url = _best_image(item)
                if url:
                    media_pairs.append(("jpg", url))
            elif media_type == 2:
                vurl = _best_video(item)
                if vurl:
                    media_pairs.append(("mp4", vurl))
                else:
                    url = _best_image(item)
                    if url:
                        media_pairs.append(("jpg", url))
            elif media_type == 8:
                for slide in item.get("carousel_media") or []:
                    stype = slide.get("media_type", 1)
                    if stype == 2:
                        vurl = _best_video(slide)
                        if vurl:
                            media_pairs.append(("mp4", vurl))
                            continue
                    url = _best_image(slide)
                    if url:
                        media_pairs.append(("jpg", url))

            if not media_pairs:
                raise _tool_error(
                    f"No downloadable media found in post {shortcode!r}",
                    "parse_error",
                    "Post may be private, or media URLs were not returned by Instagram.",
                )

            # ── 3. Download each file ────────────────────────────────────────
            saved_files: list = []
            total = len(media_pairs)
            await ctx.report_progress(0.2, 1.0, message=f"Downloading {total} file(s)…")

            async with _CurlSession(impersonate=config.ig_impersonate) as dl_session:
                for idx, (ext, url) in enumerate(media_pairs, 1):
                    fname = f"{shortcode}_{idx}.{ext}"
                    fpath = os.path.join(save_dir, fname)
                    try:
                        resp = await dl_session.get(
                            url,
                            headers={"Referer": "https://www.instagram.com/"},
                        )
                        if resp.status_code != 200:
                            saved_files.append({"file": fname, "ok": False, "error": f"HTTP {resp.status_code}"})
                            continue
                        with open(fpath, "wb") as f:
                            f.write(resp.content)
                        size_kb = len(resp.content) // 1024
                        saved_files.append({"file": fname, "path": fpath, "size_kb": size_kb, "type": ext, "ok": True})
                        await ctx.report_progress(0.2 + 0.8 * idx / total, 1.0, message=f"Saved {fname} ({size_kb} KB)")
                    except Exception as exc:
                        saved_files.append({"file": fname, "ok": False, "error": str(exc)})

            elapsed = time.perf_counter() - _t0

            # ── 4. Format output ─────────────────────────────────────────────
            type_label = {1: "image", 2: "video", 8: "carousel"}.get(media_type, "unknown")
            ok_files = [f for f in saved_files if f.get("ok")]
            fail_files = [f for f in saved_files if not f.get("ok")]

            lines = [
                f"## Download complete — `{shortcode}`",
                f"- **Type**: {type_label}",
                f"- **Files**: {len(ok_files)}/{total} saved in `{save_dir}`",
                f"- **Time**: {elapsed:.2f}s",
                "",
                "### Saved files",
            ]
            for f in ok_files:
                lines.append(f"- `{f['path']}` ({f['size_kb']} KB, {f['type']})")
            if fail_files:
                lines.append("\n### Errors")
                for f in fail_files:
                    lines.append(f"- `{f['file']}`: {f.get('error', 'unknown error')}")

            await ctx.info(f"instagram_download ✓ — {shortcode}, {len(ok_files)} files, {elapsed:.2f}s")
            return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_inbox
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_dm_inbox",
            annotations={
                "title": "Instagram DM Inbox",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_dm_inbox(params: DMInboxInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — List DM inbox threads.

            Returns your most recent direct message conversations:
            thread title, participants, unread status, last message preview.
            Use thread_id from results to fetch full messages via instagram_dm_thread.

            Args:
                params: limit (1-50, default 20), cursor (pagination)
            """
            await ctx.info(f"instagram_dm_inbox: limit={params.limit}")
            try:
                data = await client.fetch_dm_inbox(
                    limit=params.limit,
                    cursor=params.cursor or None,
                )
                return format_dm_inbox_markdown(data)
            except Exception as e:
                raise _exception_to_tool_error(e)

        @mcp.tool(
            name="instagram_dm_thread",
            annotations={
                "title": "Instagram DM Thread",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_dm_thread(params: DMThreadInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Fetch messages in a DM thread.

            Returns conversation messages in chronological order.
            Supports pagination via cursor for older messages.

            Args:
                params: thread_id (from dm_inbox), limit (1-50), cursor
            """
            await ctx.info(f"instagram_dm_thread: {params.thread_id}")
            try:
                data = await client.fetch_dm_thread(
                    thread_id=params.thread_id,
                    limit=params.limit,
                    cursor=params.cursor or None,
                )
                return format_dm_thread_markdown(data)
            except Exception as e:
                raise _exception_to_tool_error(e)

        @mcp.tool(
            name="instagram_dm_send",
            annotations={
                "title": "Instagram DM Send",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        )
        async def instagram_dm_send(params: DMSendInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Send a text DM via Instagram Web GraphQL.

            Provide either:
            - username: Instagram handle (e.g. 'cristiano') — resolves thread automatically
            - thread_id: igid from instagram_dm_inbox — sends to existing thread

            Args:
                params: username OR thread_id, plus text (max 1000 chars)
            """
            target = params.username or params.thread_id
            await ctx.info(f"instagram_dm_send: target={target}, len={len(params.text)}")
            try:
                if params.username:
                    data = await client.send_dm_to_username(
                        username=params.username,
                        text=params.text,
                    )
                else:
                    data = await client.send_dm_text(
                        thread_id=params.thread_id,
                        text=params.text,
                    )
                return format_dm_send_markdown(data)
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_react
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_dm_react",
            annotations={
                "title": "Instagram DM Reaction",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_dm_react(params: DMReactInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Add or remove an emoji reaction to a DM message.

            Args:
                params: thread_id, item_id, emoji (default ❤), action (react/unreact)
            """
            await ctx.info(f"instagram_dm_react: thread={params.thread_id} item={params.item_id} action={params.action}")
            try:
                if params.action == "unreact":
                    data = await client.dm_unreact(params.thread_id, params.item_id)
                else:
                    data = await client.dm_react(params.thread_id, params.item_id, params.emoji)
                return f"✅ {data['status'].capitalize()}: {data.get('emoji', '')} on message {data['item_id'][:20]}..."
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_unsend
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_dm_unsend",
            annotations={
                "title": "Instagram DM Unsend",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        )
        async def instagram_dm_unsend(params: DMUnsendInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Delete/unsend a DM message (removes it for everyone).

            Args:
                params: thread_id, item_id
            """
            await ctx.info(f"instagram_dm_unsend: thread={params.thread_id} item={params.item_id}")
            try:
                data = await client.dm_unsend(params.thread_id, params.item_id)
                return f"✅ Message deleted: {data['item_id'][:30]}..."
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_mark_seen
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_dm_mark_seen",
            annotations={
                "title": "Instagram DM Mark Seen",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_dm_mark_seen(params: DMMarkSeenInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Mark a DM thread as seen up to a given message.

            Args:
                params: thread_id, item_id (last message to mark as read)
            """
            await ctx.info(f"instagram_dm_mark_seen: thread={params.thread_id}")
            try:
                data = await client.dm_mark_seen(params.thread_id, params.item_id)
                return f"✅ Thread marked as seen up to message {data['item_id'][:30]}..."
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_post_comment
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_post_comment",
            annotations={
                "title": "Instagram Post Comment",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        )
        async def instagram_post_comment(params: PostCommentInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Post a comment on an Instagram post.

            Args:
                params: media_id (numeric), text
            """
            await ctx.info(f"instagram_post_comment: media={params.media_id}")
            try:
                data = await client.post_comment(params.media_id, params.text)
                return (
                    f"✅ Commented on post {data['media_id']}\n"
                    f"Comment ID: {data['comment_id']}\n"
                    f"Text: {data['text']}"
                )
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_user_search
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_user_search",
            annotations={
                "title": "Instagram User Search",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_user_search(params: UserSearchInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Search Instagram users by username or name.

            Args:
                params: query, count (1-50)
            """
            await ctx.info(f"instagram_user_search: query={params.query}")
            try:
                users = await client.search_users(params.query, params.count)
                if not users:
                    return f"No users found for '{params.query}'."
                lines = [f"## Search: '{params.query}' — {len(users)} results\n"]
                for u in users:
                    verified = " ✓" if u["is_verified"] else ""
                    private = " 🔒" if u["is_private"] else ""
                    fc = f" | {u['follower_count']:,} followers" if u.get("follower_count") else ""
                    lines.append(f"- **@{u['username']}**{verified}{private} — {u['full_name']}{fc}")
                return "\n".join(lines)
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_user_followers
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_user_followers",
            annotations={
                "title": "Instagram User Followers List",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_user_followers(params: UserFollowersInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Get followers list for a user by numeric user_id.

            Args:
                params: user_id (numeric), count (1-200), max_id (pagination cursor)
            """
            await ctx.info(f"instagram_user_followers: user_id={params.user_id}")
            try:
                data = await client.get_user_followers(
                    params.user_id, params.count, params.max_id or None
                )
                users = data["users"]
                lines = [f"## Followers of user {params.user_id} — {data['count']} shown\n"]
                for u in users:
                    verified = " ✓" if u["is_verified"] else ""
                    private = " 🔒" if u["is_private"] else ""
                    lines.append(f"- **@{u['username']}**{verified}{private} — {u['full_name']} (id: {u['user_id']})")
                if data["has_more"]:
                    lines.append(f"\n_More available. next_max_id: `{data['next_max_id']}`_")
                return "\n".join(lines)
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_user_following
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_user_following",
            annotations={
                "title": "Instagram User Following List",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_user_following(params: UserFollowersInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Get following list for a user by numeric user_id.

            Args:
                params: user_id (numeric), count (1-200), max_id (pagination cursor)
            """
            await ctx.info(f"instagram_user_following: user_id={params.user_id}")
            try:
                data = await client.get_user_following(
                    params.user_id, params.count, params.max_id or None
                )
                users = data["users"]
                lines = [f"## Following of user {params.user_id} — {data['count']} shown\n"]
                for u in users:
                    verified = " ✓" if u["is_verified"] else ""
                    private = " 🔒" if u["is_private"] else ""
                    lines.append(f"- **@{u['username']}**{verified}{private} — {u['full_name']} (id: {u['user_id']})")
                if data["has_more"]:
                    lines.append(f"\n_More available. next_max_id: `{data['next_max_id']}`_")
                return "\n".join(lines)
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_story_mark_seen
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_story_mark_seen",
            annotations={
                "title": "Instagram Story Mark Seen",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_story_mark_seen(params: StoryMarkSeenInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Mark stories as seen (viewed).

            Args:
                params: reel_ids (list of story media_ids), owner_ids (list of owner user_ids),
                        taken_ats (list of taken_at Unix timestamps)
            """
            await ctx.info(f"instagram_story_mark_seen: {len(params.reel_ids)} stories")
            try:
                data = await client.story_mark_seen(
                    params.reel_ids, params.owner_ids, params.taken_ats
                )
                return f"✅ Marked {data['count']} stories as seen."
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_story_reply
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_story_reply",
            annotations={
                "title": "Instagram Story Reply",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        )
        async def instagram_story_reply(params: StoryReplyInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Reply to a story by sending a DM to the story owner.

            Args:
                params: username (story owner), text (reply message)
            """
            await ctx.info(f"instagram_story_reply: to @{params.username}")
            try:
                data = await client.story_reply(params.username, params.text)
                return format_dm_send_markdown(data)
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_edit_profile
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_edit_profile",
            annotations={
                "title": "Instagram Edit Profile",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_edit_profile(params: EditProfileInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Edit your Instagram profile (bio, name, website).

            Only provide the fields you want to change. Others are kept as-is.

            Args:
                params: biography, full_name, external_url, email, phone_number
            """
            await ctx.info("instagram_edit_profile")
            try:
                data = await client.edit_profile(
                    biography=params.biography,
                    full_name=params.full_name,
                    external_url=params.external_url,
                    email=params.email,
                    phone_number=params.phone_number,
                )
                return (
                    f"✅ Profile updated!\n\n"
                    f"**@{data['username']}** — {data['full_name']}\n"
                    f"Bio: {data['biography']}\n"
                    f"URL: {data['external_url']}"
                )
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_post_save
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_post_save",
            annotations={
                "title": "Instagram Post Save/Unsave",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_post_save(params: PostSaveInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Save (bookmark) or unsave an Instagram post.

            Args:
                params: media_id (numeric post ID), action ('save' or 'unsave')
            """
            await ctx.info(f"instagram_post_save: media={params.media_id} action={params.action}")
            try:
                if params.action == "unsave":
                    data = await client.post_unsave(params.media_id)
                else:
                    data = await client.post_save(params.media_id)
                icon = "🗑️" if data["status"] == "unsaved" else "🔖"
                return f"{icon} Post {data['status']}: media_id={data['media_id']}"
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_block_user
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_block_user",
            annotations={
                "title": "Instagram Block/Unblock User",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_block_user(params: BlockUserInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Block or unblock an Instagram user by numeric user_id.

            Args:
                params: user_id (numeric), action ('block' or 'unblock')
            """
            await ctx.info(f"instagram_block_user: user_id={params.user_id} action={params.action}")
            try:
                if params.action == "unblock":
                    data = await client.unblock_user(params.user_id)
                else:
                    data = await client.block_user(params.user_id)
                icon = "🚫" if data["status"] == "blocked" else "✅"
                return f"{icon} User {params.user_id} {data['status']}. blocking={data['blocking']}"
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_post_like
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_post_like",
            annotations={
                "title": "Instagram Like/Unlike Post",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_post_like(params: LikePostInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Like or unlike an Instagram post.

            Args:
                params: media_id (numeric post ID), action ('like' or 'unlike')
            """
            await ctx.info(f"instagram_post_like: media={params.media_id} action={params.action}")
            try:
                data = await client.like_post(params.media_id, params.action)
                icon = "❤️" if data["status"] == "liked" else "🤍"
                return f"{icon} Post {data['media_id']} {data['status']}."
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_follow_user
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("social_graph", requires_auth=True):

        @mcp.tool(
            name="instagram_follow_user",
            annotations={
                "title": "Instagram Follow/Unfollow User",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_follow_user(params: FollowUserInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED — Follow or unfollow an Instagram user by numeric user_id.

            Args:
                params: user_id (numeric), action ('follow' or 'unfollow')
            """
            await ctx.info(f"instagram_follow_user: user_id={params.user_id} action={params.action}")
            try:
                data = await client.follow_user(params.user_id, params.action)
                icon = "➕" if data["status"] == "followed" else "➖"
                extra = ""
                if data.get("outgoing_request"):
                    extra = " (follow request sent — account is private)"
                elif data.get("following"):
                    extra = " (now following)"
                return f"{icon} User {data['user_id']} {data['status']}.{extra}"
            except Exception as e:
                raise _exception_to_tool_error(e)

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_schedule
    # ─────────────────────────────────────────────────────────────────────────

    if _enabled("server"):

        @mcp.tool(
            name="instagram_schedule",
            annotations={
                "title": "Instagram Post Scheduler",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
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
            from .scheduler import PostScheduler
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

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_monitor
    # ─────────────────────────────────────────────────────────────────────────

        @mcp.tool(
            name="instagram_monitor",
            annotations={
                "title": "Instagram Account Monitor",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
        async def instagram_monitor(params: MonitorInput, ctx: Context) -> str:
            """
            🌐 Monitor Instagram accounts for new posts via webhook.

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
            from .monitor import AccountMonitor

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

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_sessions
    # ─────────────────────────────────────────────────────────────────────────

        @mcp.tool(
            name="instagram_sessions",
            annotations={
                "title": "Instagram Multi-Account Sessions",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        async def instagram_sessions(params: SessionInput, ctx: Context) -> str:
            """
            🌐 View all loaded Instagram sessions (multi-account support).

            Shows all named sessions loaded from environment variables.
            To add sessions, set INSTAGRAM_MCP_COOKIES_<ALIAS>=<path>.

            Example env vars:
              INSTAGRAM_MCP_COOKIES=cookies.txt        → alias 'default'
              INSTAGRAM_MCP_COOKIES_BRAND=brand.txt    → alias 'brand'
              INSTAGRAM_MCP_COOKIES_AGENCY=agency.txt  → alias 'agency'

            Args:
                params: action ('list' or 'status')
            """
            from .session_manager import SessionManager

            session_mgr: SessionManager = getattr(mcp, "_session_manager", None)  # type: ignore[attr-defined]
            if session_mgr is None:
                return "## Sessions\n\nNo session manager initialized."

            status = session_mgr.status()
            authed = len(session_mgr.authenticated_aliases())
            return format_sessions_markdown({
                "sessions": status,
                "authenticated_count": authed,
            })

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL: instagram_oauth
    # ─────────────────────────────────────────────────────────────────────────

        @mcp.tool(
            name="instagram_oauth",
            annotations={
                "title": "Instagram OAuth Manager",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
        async def instagram_oauth(params: OAuthInput, ctx: Context) -> str:
            """
            Manage Instagram Graph API OAuth 2.0 tokens.

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
            from .oauth_manager import OAuthManager

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


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS for new tools
# ─────────────────────────────────────────────────────────────────────────────

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
