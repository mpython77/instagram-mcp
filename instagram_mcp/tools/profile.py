"""Profile toolset — public profile data, deep feed, bulk checks, comparisons, Threads.

All six tools registered by this submodule are anonymous (🌐): they require no
cookies and never depend on an authenticated session. The bodies below are
ported verbatim from the legacy ``instagram_mcp/tools.py`` (task 7.1) — only
the closure host changed (per-toolset ``register_profile`` instead of the
monolithic ``register_tools``). Logic, error handling, progress reporting and
exporter calls are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..models import (
    BulkProfilesInput,
    CompareProfilesInput,
    DateRange,
    DeepFeedInput,
    FeedTagResult,
    InstagramProfile,
    ProfileInput,
    ThreadsPostsInput,
    ThreadsProfileInput,
)
from ..formatter import (
    format_account_status_markdown,
    format_bulk_results_markdown,
    format_compare_profiles_markdown,
    format_deep_feed_markdown,
    format_posts_markdown,
    format_profile_markdown,
    format_profile_with_tags_markdown,
)
from ..parser import (
    check_dead_account,
    check_dead_account_from_items,
    parse_feed_items,
    parse_profile,
)
from ._helpers import (
    ToolDescriptor,
    _exception_to_tool_error,
    _paginate_feed,
    _tool_error,
    sanitize_username,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger("instagram_mcp.tools.profile")

TOOLSET_NAME = "profile"


# Annotation dicts — passed verbatim to both ``@mcp.tool(annotations=...)`` and
# the matching ``ToolDescriptor`` so the audit can verify parity.
_PROFILE_ANNOTATIONS: dict = {
    "title": "Instagram Profile",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_FEED_DEEP_ANNOTATIONS: dict = {
    "title": "Instagram Deep Feed Analysis",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_COMPARE_PROFILES_ANNOTATIONS: dict = {
    "title": "Instagram Profile Comparison",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_BULK_CHECK_ANNOTATIONS: dict = {
    "title": "Instagram Bulk Profile Check",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_THREADS_PROFILE_ANNOTATIONS: dict = {
    "title": "Threads Profile",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_THREADS_POSTS_ANNOTATIONS: dict = {
    "title": "Threads Posts",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def register_profile(
    mcp: "FastMCP",
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register the profile toolset.

    All six tools in this submodule are anonymous (auth_tier = ``"anon"``);
    they are registered unconditionally regardless of cookie state.
    """

    descriptors: list[ToolDescriptor] = []

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 1: instagram_profile
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_profile",
        annotations=_PROFILE_ANNOTATIONS,
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
            await client.cache_media_urls(profile)

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
                    await client.cache_media_urls(feed_tags_result)
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

    descriptors.append(ToolDescriptor(
        name="instagram_profile",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_PROFILE_ANNOTATIONS,
        input_model=ProfileInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 2: instagram_feed_deep
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_feed_deep",
        annotations=_FEED_DEEP_ANNOTATIONS,
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

    descriptors.append(ToolDescriptor(
        name="instagram_feed_deep",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_FEED_DEEP_ANNOTATIONS,
        input_model=DeepFeedInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 3: instagram_compare_profiles
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_compare_profiles",
        annotations=_COMPARE_PROFILES_ANNOTATIONS,
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

    descriptors.append(ToolDescriptor(
        name="instagram_compare_profiles",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_COMPARE_PROFILES_ANNOTATIONS,
        input_model=CompareProfilesInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 4: instagram_bulk_check
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_bulk_check",
        annotations=_BULK_CHECK_ANNOTATIONS,
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

    descriptors.append(ToolDescriptor(
        name="instagram_bulk_check",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_BULK_CHECK_ANNOTATIONS,
        input_model=BulkProfilesInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 5: instagram_threads_profile
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_threads_profile",
        annotations=_THREADS_PROFILE_ANNOTATIONS,
    )
    async def instagram_threads_profile(params: ThreadsProfileInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — Get a Threads profile by username.

        Fetches follower count, bio, verification status, and thread count from
        Meta's Threads platform (threads.net).

        Args:
            username: Threads username (with or without @)

        Returns:
            Profile metadata including followers, bio, verification status.
        """
        await ctx.info(f"instagram_threads_profile: @{params.username}")
        try:
            data = await client.threads_profile(params.username)
            verified = " ✓" if data["is_verified"] else ""
            private = " 🔒" if data["is_private"] else ""
            lines = [
                f"**@{data['username']}**{verified}{private}",
                f"Name: {data['display_name']}",
                f"Followers: {data['followers']:,} | Following: {data['following']:,}",
                f"Threads: {data['threads_count']:,}",
            ]
            if data.get("bio"):
                lines.append(f"Bio: {data['bio']}")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_threads_profile",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_THREADS_PROFILE_ANNOTATIONS,
        input_model=ThreadsProfileInput,
        description_first_line="🌐 NO LOGIN REQUIRED — Get a Threads profile by username.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 6: instagram_threads_posts
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_threads_posts",
        annotations=_THREADS_POSTS_ANNOTATIONS,
    )
    async def instagram_threads_posts(params: ThreadsPostsInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — Get recent Threads posts for a user.

        Fetches up to 20 recent threads/posts from Meta's Threads platform.
        Use max_id from previous result for pagination.

        Args:
            username: Threads username (with or without @)
            max_id: Pagination cursor from previous call

        Returns:
            List of recent posts with text, likes, replies, and timestamps.
        """
        await ctx.info(f"instagram_threads_posts: @{params.username}")
        try:
            data = await client.threads_user_posts(params.username, params.max_id)
            posts = data["posts"]
            if not posts:
                return f"No threads found for @{params.username}."
            lines = [f"**Threads by @{data['username']} ({len(posts)} posts):**"]
            for p in posts:
                preview = (p.get("text") or "[media]")[:120]
                lines.append(
                    f"- [{p['post_id']}] {preview} | ❤️ {p.get('like_count', 0)} 💬 {p.get('reply_count', 0)}"
                )
            if data.get("next_max_id"):
                lines.append(f"\n_Next page cursor: `{data['next_max_id']}`_")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_threads_posts",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_THREADS_POSTS_ANNOTATIONS,
        input_model=ThreadsPostsInput,
        description_first_line="🌐 NO LOGIN REQUIRED — Get recent Threads posts for a user.",
    ))

    return descriptors


__all__ = ["TOOLSET_NAME", "register_profile"]
