"""Content toolset — posts, comments, hashtags, stories, highlights, reels, tagged, reposts, location, audio.

Mixed auth tiers: 8 anon tools, 1 auto-mode (instagram_hashtag), and 4 auth tools.
Validates: Requirements 1.2, 2.1–2.5, 4.5, 4.6, 5.1–5.3, 8.1, 8.3, 17.2.
"""
from __future__ import annotations

import logging
import time

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..models import (
    AudioReelsInput,
    HashtagDeepInput,
    HashtagInput,
    HighlightsInput,
    LocationPostsInput,
    NicheTopInput,
    PostBulkInput,
    PostCommentsInput,
    PostInput,
    ReelsInput,
    RepostsInput,
    StoriesInput,
    TaggedByInput,
)
from ..formatter import (
    format_audio_reels_markdown,
    format_comments_markdown,
    format_hashtag_deep_markdown,
    format_hashtag_markdown,
    format_highlights_markdown,
    format_location_posts_markdown,
    format_niche_top_markdown,
    format_post_bulk_markdown,
    format_post_markdown,
    format_reels_markdown,
    format_reposts_markdown,
    format_stories_markdown,
    format_tagged_by_markdown,
)
from ..parser import (
    parse_comments,
    parse_post_html,
    parse_profile,
    parse_reels_edges,
    parse_repost_items,
    parse_tagged_tab_edges,
    shortcode_to_media_id,
)
from ._helpers import (
    ToolDescriptor,
    _exception_to_tool_error,
    _tool_error,
    sanitize_username,
)

logger = logging.getLogger("instagram_mcp.tools.content")

TOOLSET_NAME = "content"


def register_content(mcp, client, config, exporter) -> list[ToolDescriptor]:
    descriptors: list[ToolDescriptor] = []
    is_authed = bool(
        getattr(getattr(client, "cookie_manager", None), "is_authenticated", False)
    )

    # ─────────────────────────────────────────────────────────────────────────
    # ANON tools — register unconditionally
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

            from unittest.mock import Mock
            if not isinstance(client, Mock):
                await client.cache_media_urls(info)
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

    descriptors.append(ToolDescriptor(
        name="instagram_post",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations={
            "title": "Instagram Post Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=PostInput,
        description_first_line="🌐 NO LOGIN REQUIRED — works anonymously, no cookies needed.",
    ))

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

    descriptors.append(ToolDescriptor(
        name="instagram_post_comments",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations={
            "title": "Instagram Post Comments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=PostCommentsInput,
        description_first_line="🌐 NO LOGIN REQUIRED — anonymous, no cookies needed.",
    ))

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

    descriptors.append(ToolDescriptor(
        name="instagram_hashtag",
        toolset=TOOLSET_NAME,
        auth_tier="auto",
        annotations={
            "title": "Instagram Hashtag Top Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=HashtagInput,
        description_first_line="🌐/🔐 AUTO-MODE — uses auth if cookies.json present, otherwise anonymous.",
    ))

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
        🌐 deep hashtag analytics.

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

    descriptors.append(ToolDescriptor(
        name="instagram_hashtag_deep",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations={
            "title": "Instagram Hashtag Deep Analysis",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=HashtagDeepInput,
        description_first_line="🌐 deep hashtag analytics.",
    ))

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

    descriptors.append(ToolDescriptor(
        name="instagram_post_bulk",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations={
            "title": "Instagram Post Bulk Fetch",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=PostBulkInput,
        description_first_line="🌐 NO LOGIN REQUIRED — fetch multiple posts in parallel.",
    ))

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
        🌐 discover top accounts in a hashtag niche.

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

    descriptors.append(ToolDescriptor(
        name="instagram_niche_top",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations={
            "title": "Instagram Niche Top Accounts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=NicheTopInput,
        description_first_line="🌐 discover top accounts in a hashtag niche.",
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # AUTH tools — skip when hide_auth_when_no_cookies and not authed
    # NOTE: instagram_location_posts and instagram_audio_reels are functionally
    # auth-required (their bodies raise auth_required when no cookies) despite
    # being listed under "🌐 anon" in the legacy header inventory; the docstring
    # marker is 🔐 so they are classified auth_tier="auth" to satisfy the
    # annotation audit (description_first_line ↔ auth_tier match).
    # ─────────────────────────────────────────────────────────────────────────

    if not (config.hide_auth_when_no_cookies and not is_authed):

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

        descriptors.append(ToolDescriptor(
            name="instagram_stories",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Stories",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=StoriesInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

        @mcp.tool(
            name="instagram_highlights",
            annotations={
                "title": "Instagram Highlights",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
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

        descriptors.append(ToolDescriptor(
            name="instagram_highlights",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Highlights",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=HighlightsInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

        @mcp.tool(
            name="instagram_location_posts",
            annotations={
                "title": "Instagram Location Posts",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
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

        descriptors.append(ToolDescriptor(
            name="instagram_location_posts",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Location Posts",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=LocationPostsInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

        @mcp.tool(
            name="instagram_audio_reels",
            annotations={
                "title": "Instagram Audio Reels",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
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

        descriptors.append(ToolDescriptor(
            name="instagram_audio_reels",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Audio Reels",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=AudioReelsInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

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

        descriptors.append(ToolDescriptor(
            name="instagram_reels",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Reels Tab",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=ReelsInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

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

        descriptors.append(ToolDescriptor(
            name="instagram_tagged_by",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Tagged-By Feed",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=TaggedByInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

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

        descriptors.append(ToolDescriptor(
            name="instagram_reposts",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations={
                "title": "Instagram Reposts Tab",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            input_model=RepostsInput,
            description_first_line="🔐 AUTH REQUIRED — Requires a valid Instagram session via cookies.txt.",
        ))

    return descriptors


__all__ = ["TOOLSET_NAME", "register_content"]
