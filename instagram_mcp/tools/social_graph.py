"""Social-graph domain tools.

This submodule registers every Instagram tool that operates on the social
graph or on user-side state mutations (search, followers/following, likes,
comments, follow/block, story actions, broadcast channels, account privacy,
home/saved/liked feeds, activity feed, follower comparison, user-id lookup,
post management, video upload, and challenge code submission).

Every tool registered here is ``auth_tier == "auth"`` (🔐). The ``MCPConfig``
``hide_auth_when_no_cookies`` flag plus ``client.cookie_manager.is_authenticated``
are checked once at the top of :func:`register_social_graph`; when no cookies
are present and the flag is enabled, the entire submodule is skipped.

Each tool body is ported byte-for-byte from the legacy
``instagram_mcp/tools.py``; only the registration scaffolding has changed.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context

from ._helpers import (
    ToolDescriptor,
    _exception_to_tool_error,
    _tool_error,
    sanitize_username,
)
from ..formatter import (
    format_dm_send_markdown,
    format_followers_markdown,
    format_following_markdown,
    format_post_likers_markdown,
    format_search_markdown,
    format_similar_accounts_markdown,
)
from ..models import (
    AccountPrivacyInput,
    ActivityFeedInput,
    BlockUserInput,
    BroadcastChannelInput,
    CommentHideInput,
    CommentLikeInput,
    CommentReplyInput,
    CompareFollowersInput,
    DeleteCommentInput,
    EditProfileInput,
    FollowersInput,
    FollowingInput,
    FollowUserInput,
    HomeFeedInput,
    LikePostInput,
    LikedPostsInput,
    MediaInsightsInput,
    PostCommentInput,
    PostDeleteInput,
    PostLikersInput,
    PostSaveInput,
    PublishStoryInput,
    SavedPostsInput,
    SearchInput,
    SimilarAccountsInput,
    StoryMarkSeenInput,
    StoryReplyInput,
    SubmitVerificationCodeInput,
    ToggleCommentsInput,
    UploadVideoInput,
    UserFollowersInput,
    UserIdLookupInput,
    UserSearchInput,
)

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from mcp.server.fastmcp import FastMCP

    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger(__name__)

TOOLSET_NAME = "social_graph"


# Annotation presets used by every descriptor below. The dicts mirror the
# ``annotations=`` payload passed to ``@mcp.tool`` decorator inside the
# registrar; we keep separate copies (per descriptor) because some legacy
# tools deviate slightly from the contract default and ``ToolDescriptor``
# is frozen.

_AUTH_TIER = "auth"


def register_social_graph(
    mcp: "FastMCP",
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register every social-graph tool with ``mcp``.

    See module docstring for the contract. Returns the list of
    :class:`ToolDescriptor` objects registered (empty when the auth gate
    skips the entire submodule).
    """
    descriptors: list[ToolDescriptor] = []
    is_authed = bool(
        getattr(getattr(client, "cookie_manager", None), "is_authenticated", False)
    )

    # Single auth gate — every tool in this submodule is 🔐 AUTH REQUIRED.
    if config.hide_auth_when_no_cookies and not is_authed:
        return descriptors

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

    descriptors.append(ToolDescriptor(
        name="instagram_search",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=SearchInput,
        description_first_line="🔐 AUTH REQUIRED — Search Instagram for accounts and/or hashtags.",
    ))

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

    descriptors.append(ToolDescriptor(
        name="instagram_followers_list",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Followers List",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=FollowersInput,
        description_first_line="🔐 AUTH REQUIRED — Fetch recent followers of an Instagram account.",
    ))

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

    descriptors.append(ToolDescriptor(
        name="instagram_following_list",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Following List",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=FollowingInput,
        description_first_line="🔐 AUTH REQUIRED — Fetch accounts that a user is following (with pagination).",
    ))

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

    descriptors.append(ToolDescriptor(
        name="instagram_post_likers",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Post Likers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=PostLikersInput,
        description_first_line="🔐 AUTH REQUIRED — Fetch users who liked an Instagram post.",
    ))

    # ── Similar Accounts ─────────────────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_similar_accounts",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Similar Accounts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=SimilarAccountsInput,
        description_first_line="🔐 AUTH REQUIRED — find accounts similar to a given user.",
    ))


    # ── Post Comment ─────────────────────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_post_comment",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Post Comment",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=PostCommentInput,
        description_first_line="🔐 AUTH REQUIRED — Post a comment on an Instagram post.",
    ))

    # ── User Search ──────────────────────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_user_search",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram User Search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=UserSearchInput,
        description_first_line="🔐 AUTH REQUIRED — Search Instagram users by username or name.",
    ))

    # ── User Followers (by user_id) ──────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_user_followers",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram User Followers List",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=UserFollowersInput,
        description_first_line="🔐 AUTH REQUIRED — Get followers list for a user by numeric user_id.",
    ))

    # ── User Following (by user_id) ──────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_user_following",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram User Following List",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=UserFollowersInput,
        description_first_line="🔐 AUTH REQUIRED — Get following list for a user by numeric user_id.",
    ))

    # ── Story Mark Seen ──────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_story_mark_seen",
        annotations={
            "title": "Instagram Story Mark Seen",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
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

    descriptors.append(ToolDescriptor(
        name="instagram_story_mark_seen",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Story Mark Seen",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=StoryMarkSeenInput,
        description_first_line="🔐 AUTH REQUIRED — Mark stories as seen (viewed).",
    ))

    # ── Story Reply ──────────────────────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_story_reply",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Story Reply",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=StoryReplyInput,
        description_first_line="🔐 AUTH REQUIRED — Reply to a story by sending a DM to the story owner.",
    ))

    # ── Edit Profile ─────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_edit_profile",
        annotations={
            "title": "Instagram Edit Profile",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
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

    descriptors.append(ToolDescriptor(
        name="instagram_edit_profile",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Edit Profile",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=EditProfileInput,
        description_first_line="🔐 AUTH REQUIRED — Edit your Instagram profile (bio, name, website).",
    ))

    # ── Post Save ────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_post_save",
        annotations={
            "title": "Instagram Post Save/Unsave",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
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

    descriptors.append(ToolDescriptor(
        name="instagram_post_save",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Post Save/Unsave",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=PostSaveInput,
        description_first_line="🔐 AUTH REQUIRED — Save (bookmark) or unsave an Instagram post.",
    ))

    # ── Block / Unblock User ────────────────────────────────────────────────

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

    descriptors.append(ToolDescriptor(
        name="instagram_block_user",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Block/Unblock User",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=BlockUserInput,
        description_first_line="🔐 AUTH REQUIRED — Block or unblock an Instagram user by numeric user_id.",
    ))

    # ── Like / Unlike Post ──────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_post_like",
        annotations={
            "title": "Instagram Like/Unlike Post",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
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

    descriptors.append(ToolDescriptor(
        name="instagram_post_like",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Like/Unlike Post",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=LikePostInput,
        description_first_line="🔐 AUTH REQUIRED — Like or unlike an Instagram post.",
    ))

    # ── Follow / Unfollow User ──────────────────────────────────────────────

    @mcp.tool(
        name="instagram_follow_user",
        annotations={
            "title": "Instagram Follow/Unfollow User",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
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

    descriptors.append(ToolDescriptor(
        name="instagram_follow_user",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Follow/Unfollow User",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=FollowUserInput,
        description_first_line="🔐 AUTH REQUIRED — Follow or unfollow an Instagram user by numeric user_id.",
    ))

    # ── Delete Comment ──────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_delete_comment",
        annotations={
            "title": "Instagram Delete Comment",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def instagram_delete_comment(params: DeleteCommentInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Delete a comment on an Instagram post.

        You can delete your own comments on any post, or any comment on your own posts.

        Args:
            media_id: Numeric media_id of the post (get from instagram_post tool)
            comment_id: Numeric comment_id to delete (get from instagram_post_comments tool)

        Returns:
            Confirmation that the comment was deleted.
        """
        await ctx.info(f"instagram_delete_comment: media={params.media_id} comment={params.comment_id}")
        try:
            data = await client.delete_comment(params.media_id, params.comment_id)
            return f"🗑️ Comment {data['comment_id']} deleted from post {data['media_id']}."
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_delete_comment",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Delete Comment",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=DeleteCommentInput,
        description_first_line="🔐 AUTH REQUIRED — Delete a comment on an Instagram post.",
    ))

    # ── Publish Story ───────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_publish_story",
        annotations={
            "title": "Instagram Publish Story",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def instagram_publish_story(params: PublishStoryInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Publish a photo as an Instagram Story.

        Uploads the image and configures it as a story (visible for 24 hours).
        Optionally publish to Close Friends only.

        Args:
            image_path: Local path to a JPEG or PNG image file
            close_friends_only: If True, story is visible only to Close Friends list

        Returns:
            Story media_id and confirmation of publish.
        """
        await ctx.info(f"instagram_publish_story: path={params.image_path} close_friends={params.close_friends_only}")
        try:
            data = await client.publish_story(params.image_path, params.close_friends_only)
            audience = " (Close Friends only)" if params.close_friends_only else ""
            media_id = data.get("media_id", "unknown")
            return f"📖 Story published{audience}. media_id={media_id}"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_publish_story",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Publish Story",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=PublishStoryInput,
        description_first_line="🔐 AUTH REQUIRED — Publish a photo as an Instagram Story.",
    ))

    # ── Broadcast Channel ───────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_broadcast_channel",
        annotations={
            "title": "Instagram Broadcast Channel",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def instagram_broadcast_channel(params: BroadcastChannelInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Read Instagram Broadcast Channel info or posts.

        Broadcast channels are one-way channels creators use to send updates to followers.

        Actions:
          info  — get channel title, description, subscriber count
          posts — list recent posts in the channel (use max_id for pagination)

        Args:
            channel_id: Broadcast channel ID
            action: 'info' or 'posts'
            max_id: Pagination cursor from previous 'posts' call

        Returns:
            Channel metadata or list of posts.
        """
        await ctx.info(f"instagram_broadcast_channel: id={params.channel_id} action={params.action}")
        try:
            if params.action == "info":
                data = await client.broadcast_channel_info(params.channel_id)
                lines = [
                    f"**Broadcast Channel: {data['title'] or params.channel_id}**",
                    f"Subscribers: {data['subscriber_count']:,}",
                ]
                if data.get("description"):
                    lines.append(f"Description: {data['description']}")
                if data.get("broadcast_status"):
                    lines.append(f"Status: {data['broadcast_status']}")
                return "\n".join(lines)
            elif params.action == "posts":
                data = await client.broadcast_channel_posts(params.channel_id, params.max_id)
                posts = data["posts"]
                if not posts:
                    return "No posts found in this broadcast channel."
                lines = [f"**Broadcast Posts ({len(posts)}):**"]
                for p in posts:
                    preview = (p.get("text") or "")[:80]
                    lines.append(f"- [{p['post_id']}] {preview} | ❤️ {p.get('like_count', 0)}")
                if data.get("next_max_id"):
                    lines.append(f"\n_Next page cursor: `{data['next_max_id']}`_")
                return "\n".join(lines)
            else:
                return f"Unknown action '{params.action}'. Use 'info' or 'posts'."
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_broadcast_channel",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Broadcast Channel",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=BroadcastChannelInput,
        description_first_line="🔐 AUTH REQUIRED — Read Instagram Broadcast Channel info or posts.",
    ))


    # ── Comment Reply ───────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_comment_reply",
        annotations={
            "title": "Instagram Comment Reply",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def instagram_comment_reply(params: CommentReplyInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Reply to a specific comment on an Instagram post.

        Creates a threaded reply under the target comment. Get comment_id from
        instagram_post_comments tool.

        Args:
            params: media_id (post), comment_id (to reply to), text
        """
        await ctx.info(f"instagram_comment_reply: media={params.media_id} comment={params.comment_id}")
        try:
            data = await client.comment_reply(params.media_id, params.comment_id, params.text)
            return (
                f"✅ Reply posted on post {data['media_id']}\n"
                f"Reply ID: {data['comment_id']}\n"
                f"Reply to: {data['replied_to']}\n"
                f"Text: {data['text']}"
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_comment_reply",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Comment Reply",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=CommentReplyInput,
        description_first_line="🔐 AUTH REQUIRED — Reply to a specific comment on an Instagram post.",
    ))

    # ── Comment Like ────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_comment_like",
        annotations={
            "title": "Instagram Comment Like",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def instagram_comment_like(params: CommentLikeInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Like or unlike a comment on an Instagram post.

        Args:
            params: comment_id, action ('like' or 'unlike')
        """
        await ctx.info(f"instagram_comment_like: comment={params.comment_id} action={params.action}")
        try:
            data = await client.comment_like(params.comment_id, params.action)
            return f"✅ Comment {data['status']}: `{data['comment_id']}`"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_comment_like",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Comment Like",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=CommentLikeInput,
        description_first_line="🔐 AUTH REQUIRED — Like or unlike a comment on an Instagram post.",
    ))

    # ── Comment Hide ────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_comment_hide",
        annotations={
            "title": "Instagram Comment Hide",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def instagram_comment_hide(params: CommentHideInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Hide or unhide a comment on your Instagram post.

        Hidden comments remain visible to the commenter but not to the public.
        Requires you to own the post.

        Args:
            params: comment_id, hide (True=hide, False=unhide)
        """
        action = "hide" if params.hide else "unhide"
        await ctx.info(f"instagram_comment_hide: {action} comment={params.comment_id}")
        try:
            data = await client.comment_hide(params.comment_id, params.hide)
            return f"✅ Comment {data['status']}: `{data['comment_id']}`"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_comment_hide",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Comment Hide",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=CommentHideInput,
        description_first_line="🔐 AUTH REQUIRED — Hide or unhide a comment on your Instagram post.",
    ))

    # ── Post Delete ─────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_post_delete",
        annotations={
            "title": "Instagram Post Delete",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def instagram_post_delete(params: PostDeleteInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Permanently delete one of your own Instagram posts.

        ⚠️ This action is irreversible. The post cannot be recovered.

        Args:
            params: media_id (numeric, from instagram_post or instagram_feed_deep)
        """
        await ctx.info(f"instagram_post_delete: media={params.media_id}")
        try:
            data = await client.post_delete(params.media_id)
            return f"✅ Post permanently deleted: `{data['media_id']}`"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_post_delete",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Post Delete",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=PostDeleteInput,
        description_first_line="🔐 AUTH REQUIRED — Permanently delete one of your own Instagram posts.",
    ))

    # ── Toggle Comments ─────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_toggle_comments",
        annotations={
            "title": "Instagram Toggle Comments",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def instagram_toggle_comments(params: ToggleCommentsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Enable or disable comments on one of your posts.

        Args:
            params: media_id (numeric), enabled (True=enable, False=disable)
        """
        action = "enable" if params.enabled else "disable"
        await ctx.info(f"instagram_toggle_comments: {action} media={params.media_id}")
        try:
            data = await client.toggle_comments(params.media_id, params.enabled)
            return f"✅ Comments {data['status']} on post `{data['media_id']}`"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_toggle_comments",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Toggle Comments",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=ToggleCommentsInput,
        description_first_line="🔐 AUTH REQUIRED — Enable or disable comments on one of your posts.",
    ))

    # ── Media Insights ──────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_media_insights",
        annotations={
            "title": "Instagram Media Insights",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_media_insights(params: MediaInsightsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Get performance insights for one of your own posts.

        Returns reach, impressions, saves, shares, and profile visits.
        Requires a Business or Creator account for full metrics.

        Args:
            params: media_id (numeric, from instagram_post)
        """
        await ctx.info(f"instagram_media_insights: media={params.media_id}")
        try:
            data = await client.media_insights(params.media_id)
            insights = data.get("insights", {})
            if not insights:
                return f"No insights available for media `{params.media_id}`. Requires Business/Creator account."
            lines = [f"## Post Insights: `{params.media_id}`\n"]
            metric_names = {
                "reach": "Reach", "impressions": "Impressions", "saved": "Saves",
                "shares": "Shares", "likes": "Likes", "comments": "Comments",
                "profile_visits": "Profile visits", "plays": "Video plays",
            }
            for key, label in metric_names.items():
                if key in insights:
                    lines.append(f"- **{label}**: {insights[key]:,}")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_media_insights",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Media Insights",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=MediaInsightsInput,
        description_first_line="🔐 AUTH REQUIRED — Get performance insights for one of your own posts.",
    ))

    # ── Upload Video (feed post, not reel) ──────────────────────────────────

    @mcp.tool(
        name="instagram_upload_video",
        annotations={
            "title": "Instagram Upload Video",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def instagram_upload_video(params: UploadVideoInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Upload a video as a regular Instagram feed post.

        For Reels (vertical short-form), use instagram_upload_reel instead.
        Supports MP4 videos up to 60 seconds for feed posts.

        Args:
            params: video_path (local MP4), caption, optional cover_path,
                    disable_comments, hide_like_count
        """
        await ctx.info(f"instagram_upload_video: path={params.video_path}")
        try:
            await ctx.report_progress(0, 3, "Uploading video...")
            data = await client.upload_video_feed(
                params.video_path,
                caption=params.caption,
                cover_path=params.cover_path,
                disable_comments=params.disable_comments,
                hide_like_count=params.hide_like_count,
            )
            await ctx.report_progress(3, 3, "Published")
            shortcode = data.get("shortcode", "")
            media_id = data.get("media_id", "")
            url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else ""
            lines = ["**Video post published successfully!**"]
            if url:
                lines.append(f"URL: {url}")
            if media_id:
                lines.append(f"media_id: {media_id}")
            if shortcode:
                lines.append(f"shortcode: `{shortcode}`")
            if params.caption:
                lines.append(f"Caption: {params.caption[:80]}{'...' if len(params.caption) > 80 else ''}")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_upload_video",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Upload Video",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=UploadVideoInput,
        description_first_line="🔐 AUTH REQUIRED — Upload a video as a regular Instagram feed post.",
    ))

    # ── Account Privacy ─────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_account_privacy",
        annotations={
            "title": "Instagram Account Privacy",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def instagram_account_privacy(params: AccountPrivacyInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Toggle your account between private and public mode.

        Private accounts require approval before new followers can see your content.

        Args:
            params: is_private (True=private, False=public)
        """
        mode = "private" if params.is_private else "public"
        await ctx.info(f"instagram_account_privacy: set to {mode}")
        try:
            data = await client.account_privacy(params.is_private)
            return f"✅ Account is now **{data['status']}**"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_account_privacy",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Account Privacy",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        input_model=AccountPrivacyInput,
        description_first_line="🔐 AUTH REQUIRED — Toggle your account between private and public mode.",
    ))


    # ── Home Feed ───────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_home_feed",
        annotations={
            "title": "Instagram Home Feed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_home_feed(params: HomeFeedInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Get your Instagram home timeline.

        Returns posts from accounts you follow in reverse chronological order.

        Args:
            params: limit (1-50), optional cursor for pagination
        """
        await ctx.info(f"instagram_home_feed: limit={params.limit}")
        try:
            data = await client.home_feed(params.limit, params.cursor)
            posts = data["posts"]
            if not posts:
                return "No posts found in your home feed."
            lines = [f"## Home Feed — {data['count']} posts\n"]
            for p in posts:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(p.get("taken_at", 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if p.get("taken_at") else "?"
                cap = (p.get("caption") or "")[:80]
                lines.append(
                    f"- **@{p['username']}** | {dt} | ❤ {p.get('like_count', 0):,}\n"
                    f"  [{p.get('shortcode', p.get('media_id', ''))}] {cap}"
                )
            if data.get("more_available") and data.get("next_max_id"):
                lines.append(f"\n_More available. cursor: `{data['next_max_id']}`_")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_home_feed",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Home Feed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=HomeFeedInput,
        description_first_line="🔐 AUTH REQUIRED — Get your Instagram home timeline.",
    ))

    # ── Saved Posts ─────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_saved_posts",
        annotations={
            "title": "Instagram Saved Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_saved_posts(params: SavedPostsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Get your saved/bookmarked Instagram posts.

        Returns posts you have saved using instagram_post_save.

        Args:
            params: limit (1-50), optional cursor for pagination
        """
        await ctx.info(f"instagram_saved_posts: limit={params.limit}")
        try:
            data = await client.saved_posts(params.limit, params.cursor)
            posts = data["posts"]
            if not posts:
                return "No saved posts found."
            lines = [f"## Saved Posts — {data['count']}\n"]
            for p in posts:
                cap = (p.get("caption") or "")[:80]
                lines.append(
                    f"- **@{p['username']}** — [{p.get('shortcode', p.get('media_id', ''))}] {cap}"
                )
            if data.get("more_available") and data.get("next_max_id"):
                lines.append(f"\n_More available. cursor: `{data['next_max_id']}`_")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_saved_posts",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Saved Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=SavedPostsInput,
        description_first_line="🔐 AUTH REQUIRED — Get your saved/bookmarked Instagram posts.",
    ))

    # ── Liked Posts ─────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_liked_posts",
        annotations={
            "title": "Instagram Liked Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_liked_posts(params: LikedPostsInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Get posts you have liked.

        Returns your personal liked media history.

        Args:
            params: limit (1-50), optional cursor for pagination
        """
        await ctx.info(f"instagram_liked_posts: limit={params.limit}")
        try:
            data = await client.liked_posts(params.limit, params.cursor)
            posts = data["posts"]
            if not posts:
                return "No liked posts found."
            lines = [f"## Liked Posts — {data['count']}\n"]
            for p in posts:
                cap = (p.get("caption") or "")[:80]
                lines.append(
                    f"- **@{p['username']}** — [{p.get('shortcode', p.get('media_id', ''))}] {cap}"
                )
            if data.get("more_available") and data.get("next_max_id"):
                lines.append(f"\n_More available. cursor: `{data['next_max_id']}`_")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_liked_posts",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Liked Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=LikedPostsInput,
        description_first_line="🔐 AUTH REQUIRED — Get posts you have liked.",
    ))

    # ── Activity Feed ───────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_activity_feed",
        annotations={
            "title": "Instagram Activity Feed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_activity_feed(params: ActivityFeedInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Get your Instagram notification/activity feed.

        Returns likes, comments, follows, and mentions on your posts.

        Args:
            params: limit (1-100, default 30)
        """
        await ctx.info(f"instagram_activity_feed: limit={params.limit}")
        try:
            data = await client.activity_feed(params.limit)
            notifications = data["notifications"]
            if not notifications:
                return "No recent activity."
            lines = [f"## Activity Feed — {data['count']} notifications\n"]
            for n in notifications:
                from datetime import datetime, timezone
                ts = n.get("timestamp", 0)
                dt = datetime.fromtimestamp(int(str(ts)[:10]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "?"
                text = n.get("text", "")[:100]
                lines.append(f"- [{dt}] **{n['type']}** (user: {n['user_id']}) — {text}")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_activity_feed",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Activity Feed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=ActivityFeedInput,
        description_first_line="🔐 AUTH REQUIRED — Get your Instagram notification/activity feed.",
    ))

    # ── Compare Followers ───────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_compare_followers",
        annotations={
            "title": "Instagram Compare Followers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_compare_followers(params: CompareFollowersInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Compare your followers and following lists.

        Finds:
        - Unfollowers: accounts you follow who don't follow you back
        - Fans: accounts who follow you but you don't follow back

        Args:
            params: analysis_type ('unfollowers', 'fans', or 'both'), max_users (1-2000)
        """
        await ctx.info(f"instagram_compare_followers: type={params.analysis_type} max={params.max_users}")
        valid_types = ("unfollowers", "fans", "both")
        if params.analysis_type not in valid_types:
            raise _tool_error(f"analysis_type must be one of: {valid_types}", "validation_error")
        try:
            await ctx.report_progress(0, 2, "Fetching follower/following lists...")
            data = await client.compare_followers(params.analysis_type, params.max_users)
            await ctx.report_progress(2, 2, "Done")
            lines = [f"## Follower Comparison\n"]
            if "unfollower_count" in data:
                ids = data.get("unfollowers", [])
                lines.append(f"### Unfollowers ({data['unfollower_count']})")
                lines.append("_(You follow them, they don't follow back)_")
                for uid in ids[:50]:
                    lines.append(f"- user_id: `{uid}`")
                if len(ids) > 50:
                    lines.append(f"_...and {len(ids) - 50} more_")
            if "fan_count" in data:
                ids = data.get("fans", [])
                lines.append(f"\n### Fans ({data['fan_count']})")
                lines.append("_(They follow you, you don't follow back)_")
                for uid in ids[:50]:
                    lines.append(f"- user_id: `{uid}`")
                if len(ids) > 50:
                    lines.append(f"_...and {len(ids) - 50} more_")
            return "\n".join(lines)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_compare_followers",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram Compare Followers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=CompareFollowersInput,
        description_first_line="🔐 AUTH REQUIRED — Compare your followers and following lists.",
    ))

    # ── User ID Lookup ──────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_user_id_lookup",
        annotations={
            "title": "Instagram User ID Lookup",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def instagram_user_id_lookup(params: UserIdLookupInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Bidirectional lookup: username → user_id or user_id → username.

        Useful for converting between the two formats required by different tools.
        Auto-detects lookup direction based on input (numeric = ID, text = username).

        Args:
            params: value (username or user_id), lookup_type ('auto', 'username_to_id', 'id_to_username')
        """
        await ctx.info(f"instagram_user_id_lookup: value={params.value} type={params.lookup_type}")
        try:
            data = await client.user_id_lookup(params.value, params.lookup_type)
            verified = " ✓" if data.get("is_verified") else ""
            private = " 🔒" if data.get("is_private") else ""
            return (
                f"## User Lookup: `{params.value}`\n"
                f"- Username: **@{data['username']}**{verified}{private}\n"
                f"- User ID: `{data['user_id']}`\n"
                f"- Full name: {data.get('full_name', '')}"
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_user_id_lookup",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Instagram User ID Lookup",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        input_model=UserIdLookupInput,
        description_first_line="🔐 AUTH REQUIRED — Bidirectional lookup: username → user_id or user_id → username.",
    ))

    # ── Submit Verification Code (Challenge) ────────────────────────────────
    #
    # In legacy ``tools.py`` this tool sat *outside* every ``if _enabled(...)``
    # block — it was always registered. We move it into ``social_graph`` per
    # the spec design (relates to user session restoration); the single
    # auth-gate at the top of this registrar covers the same intent.

    @mcp.tool(
        name="instagram_submit_verification_code",
        annotations={
            "title": "Submit Verification Code",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def instagram_submit_verification_code(params: SubmitVerificationCodeInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Submit SMS/Email/2FA code to solve a pending checkpoint challenge.

        If a tool tells you that verification is required, get the code and run this tool.

        Args:
            params: code (6-digit code), alias (optional, defaults to 'default')
        """
        await ctx.info(f"instagram_submit_verification_code: alias={params.alias} code=******")
        try:
            from ..challenge import ChallengeResolver
            res = await ChallengeResolver.submit_code(params.code, params.alias)
            if res["success"]:
                if client.account_pool:
                    client.account_pool.restore_account(params.alias)
                return f"✅ **Success!** {res['message']}"
            else:
                return f"❌ **Failed:** {res['message']}"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(ToolDescriptor(
        name="instagram_submit_verification_code",
        toolset=TOOLSET_NAME,
        auth_tier=_AUTH_TIER,
        annotations={
            "title": "Submit Verification Code",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        input_model=SubmitVerificationCodeInput,
        description_first_line="🔐 AUTH REQUIRED — Submit SMS/Email/2FA code to solve a pending checkpoint challenge.",
    ))

    return descriptors


__all__ = ["TOOLSET_NAME", "register_social_graph"]
