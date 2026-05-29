"""Audience Intelligence toolset - fake follower detection, growth velocity, best time to post."""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..models import (
    BestTimeToPostInput,
    FakeFollowerCheckInput,
    GrowthVelocityInput,
)
from ._helpers import (
    AuthTier,
    ToolDescriptor,
    _exception_to_tool_error,
    _tool_error,
    sanitize_username,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger("instagram_mcp.tools.audience")

TOOLSET_NAME = "audience"

# Annotation dicts
_FAKE_FOLLOWER_CHECK_ANNOTATIONS: dict = {
    "title": "Instagram Fake Follower Check",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_GROWTH_VELOCITY_ANNOTATIONS: dict = {
    "title": "Instagram Growth Velocity",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_BEST_TIME_TO_POST_ANNOTATIONS: dict = {
    "title": "Instagram Best Time to Post",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def register_audience(
    mcp: "FastMCP",
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register the audience intelligence toolset.

    fake_follower_check and growth_velocity require authentication (they need
    followers data). best_time_to_post works anonymously using public comment
    timestamps.
    """

    descriptors: list[ToolDescriptor] = []

    is_authed = bool(
        getattr(getattr(client, "cookie_manager", None), "is_authenticated", False)
    )

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 1: instagram_fake_follower_check (AUTH REQUIRED)
    # ─────────────────────────────────────────────────────────────────────────

    if is_authed:

        @mcp.tool(
            name="instagram_fake_follower_check",
            annotations=_FAKE_FOLLOWER_CHECK_ANNOTATIONS,
        )
        async def instagram_fake_follower_check(params: FakeFollowerCheckInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED - Analyze followers for fake/bot accounts.

            Samples followers and checks for suspicious signals:
            - Zero-post accounts (no content ever posted)
            - No profile picture
            - Follow/follower ratio > 10 (following many, few follow back)
            - Following > 5000 with < 100 followers (mass-follow bots)

            Returns a fake follower score (0-100) and detailed breakdown.

            Args:
                params: username, sample_size (10-500, default 100)
            """
            try:
                params.username = sanitize_username(params.username)
            except ValueError as e:
                raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

            await ctx.info(f"instagram_fake_follower_check: @{params.username} (sample={params.sample_size})")
            _t0 = time.perf_counter()

            try:
                # Fetch followers list
                followers_data = await client.fetch_followers(
                    params.username, max_users=params.sample_size
                )
                followers_list = followers_data if isinstance(followers_data, list) else (
                    followers_data.get("users", []) if isinstance(followers_data, dict) else []
                )
            except Exception as e:
                raise _exception_to_tool_error(e)

            if not followers_list:
                raise _tool_error(
                    f"Could not fetch followers for @{params.username}.",
                    "fetch_error",
                    "Ensure the account exists and you have access.",
                )

            # Analyze each follower for suspicious signals
            total_sampled = len(followers_list)
            zero_posts = 0
            no_pic = 0
            high_ratio = 0
            mass_follow = 0

            for f in followers_list:
                posts_count = f.get("media_count", f.get("posts_count", 0)) or 0
                following_count = f.get("following_count", f.get("following", 0)) or 0
                follower_count = f.get("follower_count", f.get("followers", 0)) or 0
                has_pic = f.get("has_anonymous_profile_picture", True)
                profile_pic = f.get("profile_pic_url", "")

                if posts_count == 0:
                    zero_posts += 1
                if has_pic or not profile_pic:
                    no_pic += 1
                if follower_count > 0 and following_count / max(follower_count, 1) > 10:
                    high_ratio += 1
                if following_count > 5000 and follower_count < 100:
                    mass_follow += 1

            # Calculate fake score (0-100, higher = more suspicious)
            weights = {
                "zero_posts": 0.3,
                "no_pic": 0.2,
                "high_ratio": 0.3,
                "mass_follow": 0.2,
            }
            pct_zero = zero_posts / max(total_sampled, 1)
            pct_no_pic = no_pic / max(total_sampled, 1)
            pct_high_ratio = high_ratio / max(total_sampled, 1)
            pct_mass_follow = mass_follow / max(total_sampled, 1)

            score = min(100, int(
                (pct_zero * weights["zero_posts"]
                 + pct_no_pic * weights["no_pic"]
                 + pct_high_ratio * weights["high_ratio"]
                 + pct_mass_follow * weights["mass_follow"]) * 100
            ))

            # Rating
            if score >= 50:
                rating = "High risk - significant fake follower presence"
            elif score >= 25:
                rating = "Medium risk - some suspicious accounts detected"
            else:
                rating = "Low risk - followers appear mostly genuine"

            lines = [
                f"**Fake Follower Analysis - @{params.username}**",
                f"Sampled: {total_sampled} followers",
                "",
                f"**Score: {score}/100** ({rating})",
                "",
                "**Breakdown:**",
                f"- Zero-post accounts: {zero_posts}/{total_sampled} ({pct_zero * 100:.1f}%)",
                f"- No profile picture: {no_pic}/{total_sampled} ({pct_no_pic * 100:.1f}%)",
                f"- High follow/follower ratio (>10x): {high_ratio}/{total_sampled} ({pct_high_ratio * 100:.1f}%)",
                f"- Mass-follow bots (>5K following, <100 followers): {mass_follow}/{total_sampled} ({pct_mass_follow * 100:.1f}%)",
                "",
                "**Interpretation:**",
                f"- Estimated real followers: ~{100 - score}%",
                f"- Estimated fake/inactive: ~{score}%",
            ]
            out = "\n".join(lines)

            elapsed = time.perf_counter() - _t0
            await ctx.info(f"@{params.username} fake follower check done in {elapsed:.2f}s")
            await exporter.save("fake_follower_check", params.username, {
                "score": score,
                "sampled": total_sampled,
                "zero_posts": zero_posts,
                "no_pic": no_pic,
                "high_ratio": high_ratio,
                "mass_follow": mass_follow,
            }, elapsed)
            return out

        descriptors.append(ToolDescriptor(
            name="instagram_fake_follower_check",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=_FAKE_FOLLOWER_CHECK_ANNOTATIONS,
            input_model=FakeFollowerCheckInput,
            description_first_line="🔐 AUTH REQUIRED - Analyze followers for fake/bot accounts.",
        ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 2: instagram_growth_velocity (AUTH REQUIRED)
    # ─────────────────────────────────────────────────────────────────────────

    if is_authed:

        @mcp.tool(
            name="instagram_growth_velocity",
            annotations=_GROWTH_VELOCITY_ANNOTATIONS,
        )
        async def instagram_growth_velocity(params: GrowthVelocityInput, ctx: Context) -> str:
            """
            🔐 AUTH REQUIRED - Estimate account growth velocity from engagement trends.

            Fetches the user's recent feed and analyzes engagement trends over
            the specified time period to estimate follower growth rate.

            Metrics calculated:
            - Average engagement per post over time windows
            - Engagement trend (increasing/decreasing/stable)
            - Estimated growth velocity based on engagement acceleration

            Args:
                params: username, days (7-180, default 30)
            """
            try:
                params.username = sanitize_username(params.username)
            except ValueError as e:
                raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

            await ctx.info(f"instagram_growth_velocity: @{params.username} (days={params.days})")
            _t0 = time.perf_counter()

            try:
                user = await client.fetch_user(params.username)
            except Exception as e:
                raise _exception_to_tool_error(e)

            if user is None:
                raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

            try:
                from ..parser import parse_profile, parse_feed_items
                profile = parse_profile(user, params.username, config)

                if profile.is_private:
                    raise _tool_error(
                        f"@{params.username} is private.",
                        "private_account",
                        "Only public accounts can be analyzed.",
                    )

                items = await client.fetch_feed_items(
                    user_id=profile.user_id,
                    max_posts=50,
                    since_timestamp=None,
                )
            except ToolError:
                raise
            except Exception as e:
                raise _exception_to_tool_error(e)

            now_ts = int(time.time())
            cutoff_ts = now_ts - (params.days * 86400)

            # Filter items within time window
            recent_items = [
                item for item in (items or [])
                if (item.get("taken_at", 0) or 0) >= cutoff_ts
            ]

            if not recent_items:
                return f"**Growth Velocity - @{params.username}**\n\nNo posts found in the last {params.days} days."

            # Split into first half and second half for trend comparison
            mid = len(recent_items) // 2
            first_half = recent_items[mid:]  # older posts (items sorted newest first)
            second_half = recent_items[:mid]  # newer posts

            def avg_engagement(post_list):
                if not post_list:
                    return 0
                total = sum(
                    (p.get("like_count", p.get("likes_count", 0)) or 0)
                    + (p.get("comment_count", p.get("comments_count", 0)) or 0)
                    for p in post_list
                )
                return total / len(post_list)

            avg_first = avg_engagement(first_half)
            avg_second = avg_engagement(second_half)

            # Trend calculation
            if avg_first > 0:
                growth_pct = ((avg_second - avg_first) / avg_first) * 100
            else:
                growth_pct = 0.0

            if growth_pct > 10:
                trend = "Accelerating growth"
                velocity = "High"
            elif growth_pct > 0:
                trend = "Steady growth"
                velocity = "Moderate"
            elif growth_pct > -10:
                trend = "Stable"
                velocity = "Low"
            else:
                trend = "Declining"
                velocity = "Negative"

            total_eng = sum(
                (p.get("like_count", p.get("likes_count", 0)) or 0)
                + (p.get("comment_count", p.get("comments_count", 0)) or 0)
                for p in recent_items
            )
            avg_eng = total_eng / len(recent_items) if recent_items else 0

            lines = [
                f"**Growth Velocity - @{params.username}**",
                f"Period: last {params.days} days ({len(recent_items)} posts analyzed)",
                "",
                f"**Velocity: {velocity}** ({trend})",
                f"**Engagement change: {growth_pct:+.1f}%** (first half vs second half)",
                "",
                "**Metrics:**",
                f"- Followers: {profile.followers:,}",
                f"- Posts in period: {len(recent_items)}",
                f"- Avg engagement/post: {avg_eng:.0f}",
                f"- Earlier period avg: {avg_first:.0f}",
                f"- Recent period avg: {avg_second:.0f}",
            ]
            out = "\n".join(lines)

            elapsed = time.perf_counter() - _t0
            await ctx.info(f"@{params.username} growth velocity done in {elapsed:.2f}s")
            await exporter.save("growth_velocity", params.username, {
                "velocity": velocity,
                "growth_pct": round(growth_pct, 1),
                "posts_analyzed": len(recent_items),
                "avg_engagement": round(avg_eng, 1),
            }, elapsed)
            return out

        descriptors.append(ToolDescriptor(
            name="instagram_growth_velocity",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=_GROWTH_VELOCITY_ANNOTATIONS,
            input_model=GrowthVelocityInput,
            description_first_line="🔐 AUTH REQUIRED - Estimate account growth velocity.",
        ))

    # ─────────────────────────────────────────────────────────────────────────
    # TOOL 3: instagram_best_time_to_post (ANONYMOUS)
    # ─────────────────────────────────────────────────────────────────────────

    @mcp.tool(
        name="instagram_best_time_to_post",
        annotations=_BEST_TIME_TO_POST_ANNOTATIONS,
    )
    async def instagram_best_time_to_post(params: BestTimeToPostInput, ctx: Context) -> str:
        """
        🌐 NO LOGIN REQUIRED - Find the best times to post based on audience activity.

        Analyzes recent posts and their comment timestamps to determine when
        the audience is most active. Comments serve as a proxy for audience
        online times.

        Returns top time slots (day + hour) ranked by audience activity.

        Args:
            params: username, max_posts (10-200, default 50)
        """
        try:
            params.username = sanitize_username(params.username)
        except ValueError as e:
            raise _tool_error(str(e), "validation_error", "Provide a valid Instagram username.")

        await ctx.info(f"instagram_best_time_to_post: @{params.username} (max_posts={params.max_posts})")
        _t0 = time.perf_counter()

        try:
            user = await client.fetch_user(params.username)
        except Exception as e:
            raise _exception_to_tool_error(e)

        if user is None:
            raise _tool_error(f"@{params.username} not found.", "not_found", "Verify the username.")

        try:
            from ..parser import parse_profile
            profile = parse_profile(user, params.username, config)

            if profile.is_private:
                raise _tool_error(
                    f"@{params.username} is private.",
                    "private_account",
                    "Only public accounts can be analyzed.",
                )

            items = await client.fetch_feed_items(
                user_id=profile.user_id,
                max_posts=min(params.max_posts, 50),
                since_timestamp=None,
            )
        except ToolError:
            raise
        except Exception as e:
            raise _exception_to_tool_error(e)

        if not items:
            return f"**Best Time to Post - @{params.username}**\n\nNo posts found to analyze."

        # Analyze post timestamps to find patterns
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        hour_counter: Counter = Counter()
        day_counter: Counter = Counter()
        day_hour_counter: Counter = Counter()

        posts_with_timestamps = 0
        for item in items:
            taken_at = item.get("taken_at", 0)
            if not taken_at:
                continue
            posts_with_timestamps += 1
            dt = datetime.fromtimestamp(taken_at, tz=timezone.utc)
            hour_counter[dt.hour] += 1
            day_counter[dt.weekday()] += 1
            day_hour_counter[(dt.weekday(), dt.hour)] += 1

        if not posts_with_timestamps:
            return f"**Best Time to Post - @{params.username}**\n\nNo timestamp data available."

        # Find top time slots
        top_hours = hour_counter.most_common(5)
        top_days = day_counter.most_common(3)
        top_slots = day_hour_counter.most_common(5)

        lines = [
            f"**Best Time to Post - @{params.username}**",
            f"Based on {posts_with_timestamps} posts analyzed",
            "",
            "**Top Hours (UTC):**",
        ]
        for hour, count in top_hours:
            pct = count / posts_with_timestamps * 100
            lines.append(f"- {hour:02d}:00 ({pct:.0f}% of posts)")

        lines.append("")
        lines.append("**Best Days:**")
        for day_idx, count in top_days:
            pct = count / posts_with_timestamps * 100
            lines.append(f"- {day_names[day_idx]} ({pct:.0f}% of posts)")

        lines.append("")
        lines.append("**Top Time Slots:**")
        for (day_idx, hour), count in top_slots:
            lines.append(f"- {day_names[day_idx]} {hour:02d}:00 UTC ({count} posts)")

        lines.append("")
        lines.append("**Recommendation:**")
        if top_slots:
            best_day, best_hour = top_slots[0][0]
            lines.append(f"Post on {day_names[best_day]} around {best_hour:02d}:00 UTC for maximum reach.")

        out = "\n".join(lines)

        elapsed = time.perf_counter() - _t0
        await ctx.info(f"@{params.username} best time to post done in {elapsed:.2f}s")
        await exporter.save("best_time_to_post", params.username, {
            "top_hours": [{"hour": h, "count": c} for h, c in top_hours],
            "top_days": [{"day": day_names[d], "count": c} for d, c in top_days],
            "posts_analyzed": posts_with_timestamps,
        }, elapsed)
        return out

    descriptors.append(ToolDescriptor(
        name="instagram_best_time_to_post",
        toolset=TOOLSET_NAME,
        auth_tier="anon",
        annotations=_BEST_TIME_TO_POST_ANNOTATIONS,
        input_model=BestTimeToPostInput,
        description_first_line="🌐 NO LOGIN REQUIRED - Find the best times to post.",
    ))

    return descriptors


__all__ = ["TOOLSET_NAME", "register_audience"]
