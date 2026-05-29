"""Analysis toolset — engagement, collaborations, hashtags, captions, reports, comments.

All six tools registered by this submodule are anonymous (🌐): they require no
cookies and never depend on an authenticated session. The bodies below are
ported verbatim from the legacy ``instagram_mcp/tools.py`` (task 7.2) — only
the closure host changed (per-toolset ``register_analysis`` instead of the
monolithic ``register_tools``). Logic, error handling, progress reporting and
exporter calls are unchanged.

The orchestrator (``instagram_mcp.tools.register_tools``) is responsible for
invoking ``register_analysis`` only when the ``analysis`` toolset is enabled
in :class:`MCPConfig`; this submodule itself does not consult
``config.enabled_toolsets``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..models import (
    AccountReportInput,
    AnalyzeCommentsInput,
    CaptionAnalyzeInput,
    CollabNetworkInput,
    DateRange,
    EngagementAnalysisInput,
    HashtagSuggestInput,
)
from ..formatter import (
    format_account_report_markdown,
    format_collab_network_markdown,
    format_comment_analysis_markdown,
    format_engagement_analysis_markdown,
)
from ..parser import (
    parse_comments,
    parse_feed_items,
    parse_profile,
    shortcode_to_media_id,
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

logger = logging.getLogger("instagram_mcp.tools.analysis")

TOOLSET_NAME = "analysis"


# Annotation dicts — passed verbatim to both ``@mcp.tool(annotations=...)`` and
# the matching ``ToolDescriptor`` so the audit can verify parity.
_ANALYZE_ENGAGEMENT_ANNOTATIONS: dict = {
    "title": "Instagram Engagement Analysis",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_FIND_COLLAB_NETWORK_ANNOTATIONS: dict = {
    "title": "Instagram Collaboration Network",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_HASHTAG_SUGGEST_ANNOTATIONS: dict = {
    "title": "Instagram Hashtag Suggestions",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_CAPTION_ANALYZE_ANNOTATIONS: dict = {
    "title": "Instagram Caption Analyzer",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_ACCOUNT_REPORT_ANNOTATIONS: dict = {
    "title": "Instagram Account Full Report",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_ANALYZE_COMMENTS_ANNOTATIONS: dict = {
    "title": "Instagram Comment Sentiment & Audience Interaction Analyzer",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def register_analysis(
    mcp: "FastMCP",
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register the analysis toolset.

    All six tools in this submodule are anonymous (auth_tier = ``"anon"``);
    they are registered unconditionally regardless of cookie state. Toolset
    gating is the orchestrator's responsibility (see
    ``instagram_mcp.tools.register_tools``).
    """

    descriptors: list[ToolDescriptor] = []

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 1: instagram_analyze_engagement
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_analyze_engagement",
        annotations=_ANALYZE_ENGAGEMENT_ANNOTATIONS,
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

    descriptors.append(ToolDescriptor(
        name="instagram_analyze_engagement",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_ANALYZE_ENGAGEMENT_ANNOTATIONS,
        input_model=EngagementAnalysisInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 2: instagram_find_collab_network
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_find_collab_network",
        annotations=_FIND_COLLAB_NETWORK_ANNOTATIONS,
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

    descriptors.append(ToolDescriptor(
        name="instagram_find_collab_network",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_FIND_COLLAB_NETWORK_ANNOTATIONS,
        input_model=CollabNetworkInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 3: instagram_hashtag_suggest
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_hashtag_suggest",
        annotations=_HASHTAG_SUGGEST_ANNOTATIONS,
    )
    async def instagram_hashtag_suggest(params: HashtagSuggestInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — Suggest related hashtags for a niche.

        Analyzes top posts under the seed hashtag, extracts hashtags they use,
        ranks by frequency, and groups them into tiers by post volume:
        - Mega (10M+ posts) — maximum reach, very competitive
        - Macro (1M–10M) — broad reach, moderate competition
        - Mid (100K–1M) — good balance of reach and discoverability
        - Micro (<100K) — niche, highly targeted, less competition

        Returns a balanced set optimized for discoverability and a copy-paste block.

        Args:
            seed_hashtag: Starting hashtag (e.g. "fitness" or "#fitness")
            target_count: How many hashtags to return (5–50, default 30)

        Returns:
            Tiered hashtag suggestions with copy-paste block.
        """
        await ctx.info(f"instagram_hashtag_suggest: #{params.seed_hashtag} count={params.target_count}")
        try:
            data = await client.hashtag_suggest(params.seed_hashtag, params.target_count)
            lines = [
                f"**Hashtag Suggestions for #{data['seed']}**",
                f"Analyzed {data['posts_analyzed']} top posts, found {data['unique_hashtags_found']} unique hashtags",
                "",
            ]
            tiers = data["tiers"]
            if tiers["mega_10M_plus"]:
                lines.append("**Mega (10M+ posts):** " + " ".join(f"#{t}" for t in tiers["mega_10M_plus"]))
            if tiers["macro_1M_10M"]:
                lines.append("**Macro (1M–10M):** " + " ".join(f"#{t}" for t in tiers["macro_1M_10M"]))
            if tiers["mid_100k_1M"]:
                lines.append("**Mid (100K–1M):** " + " ".join(f"#{t}" for t in tiers["mid_100k_1M"]))
            if tiers["micro_under_100k"]:
                lines.append("**Micro (<100K):** " + " ".join(f"#{t}" for t in tiers["micro_under_100k"]))
            lines += [
                "",
                f"**Copy-paste ({len(data['balanced_set'])} hashtags):**",
                data["copy_paste"],
            ]
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_hashtag_suggest",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_HASHTAG_SUGGEST_ANNOTATIONS,
        input_model=HashtagSuggestInput,
        description_first_line="🌐 NO LOGIN REQUIRED — Suggest related hashtags for a niche.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 4: instagram_caption_analyze
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_caption_analyze",
        annotations=_CAPTION_ANALYZE_ANNOTATIONS,
    )
    async def instagram_caption_analyze(params: CaptionAnalyzeInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — Analyze caption patterns of an Instagram account.

        Fetches recent posts and extracts:
        - Average caption length
        - Hashtag count distribution
        - Emoji usage rate
        - CTA (call-to-action) presence rate
        - Most-used hashtags
        - Top 3 posts by likes with caption excerpts
        - Actionable improvement tips

        Args:
            username: Instagram username to analyze
            max_posts: Number of recent posts to analyze (5–50, default 20)

        Returns:
            Caption pattern analysis with insights and top-performing examples.
        """
        await ctx.info(f"instagram_caption_analyze: @{params.username} posts={params.max_posts}")
        try:
            data = await client.caption_analyze(params.username, params.max_posts)
            lines = [
                f"**Caption Analysis — @{data['username']}**",
                f"Analyzed {data['posts_analyzed']} posts",
                "",
                f"📏 Avg caption length: **{data['avg_caption_length']} chars**",
                f"#️⃣ Avg hashtags per post: **{data['avg_hashtag_count']}**",
                f"😀 Emoji usage: **{data['emoji_usage_rate']}%** of posts",
                f"📣 CTA usage: **{data['cta_usage_rate']}%** of posts",
            ]
            if data["top_hashtags"]:
                lines.append("")
                lines.append("**Most-used hashtags:**")
                for h in data["top_hashtags"][:8]:
                    lines.append(f"  #{h['tag']} ({h['count']}x)")
            if data["top_posts_by_likes"]:
                lines.append("")
                lines.append("**Top posts by likes:**")
                for i, p in enumerate(data["top_posts_by_likes"], 1):
                    cap = p["caption"].replace("\n", " ")[:120]
                    lines.append(f"  {i}. ❤️ {p['like_count']:,} — \"{cap}\"")
            if data["insights"]:
                lines.append("")
                lines.append("**Insights:**")
                for tip in data["insights"]:
                    lines.append(f"  • {tip}")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_caption_analyze",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_CAPTION_ANALYZE_ANNOTATIONS,
        input_model=CaptionAnalyzeInput,
        description_first_line="🌐 NO LOGIN REQUIRED — Analyze caption patterns of an Instagram account.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 5: instagram_account_report
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_account_report",
        annotations=_ACCOUNT_REPORT_ANNOTATIONS,
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

        from ..formatter import (
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

    descriptors.append(ToolDescriptor(
        name="instagram_account_report",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_ACCOUNT_REPORT_ANNOTATIONS,
        input_model=AccountReportInput,
        description_first_line="🌐 NO LOGIN REQUIRED — full account report in one call.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 6: instagram_analyze_comments
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_analyze_comments",
        annotations=_ANALYZE_COMMENTS_ANNOTATIONS,
    )
    async def instagram_analyze_comments(params: AnalyzeCommentsInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.

        Analyze comments for a given Instagram post, classifying sentiment (Positive,
        Neutral, Negative) and producing an in-depth audience interaction report.

        This tool:
        1. Fetches comments for the post (up to max_comments).
        2. Performs rule-based sentiment classification on text and emojis.
        3. Measures engagement metrics (likes and replies) per sentiment class.
        4. Identifies top emojis and context keywords (excluding stopwords).
        5. Formats a comprehensive Markdown report with qualitative highlight comments.

        Args:
            params: post (shortcode or URL), max_comments (1-500, default 100),
                    sort_order ('popular' or 'recent').
        """
        shortcode = params.post  # already extracted by Pydantic validator

        try:
            media_id = shortcode_to_media_id(shortcode)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram shortcode or post URL.")

        await ctx.info(
            f"instagram_analyze_comments: {shortcode} (media_id={media_id}, "
            f"max={params.max_comments}, sort={params.sort_order})"
        )
        _t0 = time.perf_counter()
        await ctx.report_progress(0.0, float(params.max_comments), message="Fetching comments for analysis...")

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

        comments = parse_comments(
            raw_comments=raw_comments,
            caption_raw=caption_raw,
            max_comments=params.max_comments,
        )
        actual = [c for c in comments if not c.is_caption]

        actual_comments = []
        for c in actual:
            actual_comments.append({
                "text": c.text,
                "like_count": c.comment_like_count,
                "child_comment_count": c.child_comment_count,
                "user": {"username": c.username}
            })

        out = format_comment_analysis_markdown(shortcode, actual_comments)

        elapsed = time.perf_counter() - _t0
        await ctx.report_progress(1.0, 1.0, message="Analysis complete!")
        await ctx.info(
            f"Analyze comments {shortcode} ✓ — {len(actual)} comments — {elapsed:.2f}s"
        )
        await exporter.save(
            "analyze_comments",
            shortcode,
            {"comments_analyzed": len(actual_comments)},
            elapsed,
        )
        return out

    descriptors.append(ToolDescriptor(
        name="instagram_analyze_comments",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_ANALYZE_COMMENTS_ANNOTATIONS,
        input_model=AnalyzeCommentsInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

    return descriptors


__all__ = ["TOOLSET_NAME", "register_analysis"]
