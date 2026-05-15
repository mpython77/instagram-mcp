"""
Output formatters — beautiful, structured, LLM-friendly.

Each result:
  - Markdown: with emojis + tables + separators
  - JSON: structured, crisp
  - For errors: error_type + suggested_action
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime as _dt, timezone as _tz
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CacheStats,
    CommentItem,
    FeedTagResult,
    InstagramPost,
    InstagramProfile,
    PostInfo,
    ProxyStatus,
    ReelItem,
    RepostItem,
    TaggedPost,
)


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_location(loc: Optional[dict]) -> str:
    """Format location dict to string with optional Maps link."""
    if not loc:
        return ""
    name = loc.get("name", "")
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is not None and lng is not None:
        maps_url = f"https://www.google.com/maps?q={lat},{lng}"
        return f"[{name}]({maps_url})"
    query = name.replace(" ", "+")
    maps_url = f"https://www.google.com/maps/search/?api=1&query={query}"
    return f"[{name}]({maps_url})"


def format_followers(n: int) -> str:
    """Return a human-readable follower count string.

    Examples:
        1_234_567  -> "1.2M"
        45_300     -> "45.3K"
        999        -> "999"
    """
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)




# ═══════════════════════════════════════════════════════════════════════════════
# PROFILE FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

_ACCOUNT_TYPE_LABEL = {1: "Personal", 2: "Creator", 3: "Business"}


def format_profile_markdown(p: InstagramProfile) -> str:
    """Profile → beautiful Markdown."""
    lines = [f"## 👤 @{p.username}"]

    if p.full_name:
        lines.append(f"**{p.full_name}**")
    if p.pronouns:
        lines.append(f"*{' · '.join(p.pronouns)}*")

    lines.append("")

    # Engagement hint
    engagement_hint = None
    if p.followers > 0 and p.posts_count > 0:
        engagement_hint = (
            "High" if p.followers > 100_000
            else "Medium" if p.followers > 10_000
            else "Low"
        )

    # Statistics table
    lines.append("| 📊 Metric | Value |")
    lines.append("|:----|----:|")
    lines.append(f"| 👥 Followers | **{format_followers(p.followers)}** |")
    lines.append(f"| 👤 Following | {format_followers(p.following)} |")
    lines.append(f"| 📸 Posts | {p.posts_count:,} |")
    if p.highlight_count > 0:
        lines.append(f"| 🎭 Highlights | {p.highlight_count} |")
    if p.usertags_count > 0:
        lines.append(f"| 🏷️ Tagged in | {p.usertags_count:,} posts |")
    if engagement_hint:
        lines.append(f"| 📈 Engagement | {engagement_hint} |")

    # Badges
    badges = []
    if p.is_verified:
        badges.append("✅ Verified")
    acc_label = _ACCOUNT_TYPE_LABEL.get(p.account_type, "")
    if acc_label:
        badges.append(f"🏷 {acc_label}")
    elif p.is_business:
        badges.append("🏢 Business")
    if p.is_professional and p.account_type not in (2, 3):
        badges.append("⭐ Professional")
    if p.is_private:
        badges.append("🔒 Private")
    if p.is_new_account:
        badges.append("🆕 New")
    if badges:
        lines.append(f"\n{' · '.join(badges)}")

    # Category
    if p.category:
        lines.append(f"📂 **Category**: {p.category}")
    if p.overall_category and p.overall_category != p.category:
        lines.append(f"📂 **Type**: {p.overall_category}")

    # Content capabilities
    content = []
    if p.has_reels:
        content.append("🎬 Reels")
    if p.has_guides:
        content.append("📚 Guides")
    if content:
        lines.append(f"📱 **Content**: {' · '.join(content)}")

    # Bio
    if p.biography:
        bio_clean = p.biography.replace("\n", "\n> ")
        lines.append(f"\n> 📝 {bio_clean}")

    # Links
    if p.website:
        lines.append(f"\n🔗 **Website**: [{p.website}]({p.website})")
    elif p.external_url:
        lines.append(f"\n🔗 **URL**: [{p.external_url}]({p.external_url})")

    # Contact (business)
    if p.contact_phone:
        lines.append(f"📞 **Phone**: {p.contact_phone}")
    if p.public_email:
        lines.append(f"📧 **Email**: {p.public_email}")
    if p.city:
        lines.append(f"📍 **City**: {p.city}")

    if p.user_id:
        lines.append(f"\n🆔 **User ID**: `{p.user_id}`")

    return "\n".join(l for l in lines if l is not None)


def format_profile_json(p: InstagramProfile) -> Dict[str, Any]:
    """Profile → JSON dict."""
    return asdict(p)


# ═══════════════════════════════════════════════════════════════════════════════
# FEED TAGS FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_feed_tags_markdown(ft: FeedTagResult) -> str:
    """Feed tags → structured Markdown."""
    lines = [
        "### 🏷️ Feed Tags Analysis",
        "",
        "| 📊 Statistic | Value |",
        "|:----|----:|",
        f"| 📸 Checked posts | {ft.posts_checked} |",
        f"| 🏷️ Posts with tags | {ft.posts_with_tags} |",
        f"| 👥 Unique tags | **{len(ft.tags)}** |",
    ]

    if ft.tags:
        lines.append("\n**👥 Tagged users:**")
        lines.append("")
        lines.append("| # | Username | Date | Post |")
        lines.append("|:--|:---------|:-----|:-----|")
        for i, tag in enumerate(ft.tags, 1):
            sc = ft.tag_shortcodes.get(tag, "")
            ts = ft.tag_timestamps.get(tag, "—")
            post_link = f"[view](https://www.instagram.com/p/{sc}/)" if sc else "—"
            lines.append(f"| {i} | @{tag} | {ts} | {post_link} |")
    else:
        lines.append("\n*No tags found — nobody was tagged/mentioned in this period.*")

    return "\n".join(lines)


def format_feed_tags_json(ft: FeedTagResult) -> Dict[str, Any]:
    """Feed tags → JSON dict."""
    return {
        "tags": ft.tags,
        "tag_details": [
            {
                "username": tag,
                "post_url": f"https://www.instagram.com/p/{ft.tag_shortcodes.get(tag, '')}/",
                "timestamp": ft.tag_timestamps.get(tag, ""),
            }
            for tag in ft.tags
        ],
        "stats": {
            "posts_checked": ft.posts_checked,
            "posts_with_tags": ft.posts_with_tags,
            "total_tags": len(ft.tags),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# POSTS FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

_POST_TYPE_ICON = {
    "carousel": "🎠",
    "reel": "🎵",
    "igtv": "📺",
    "video": "🎬",
    "image": "📸",
}


def format_posts_markdown(posts: List[InstagramPost]) -> str:
    """Posts list → structured Markdown."""
    if not posts:
        return "### 📸 Recent Posts\n\n*No posts found for this period.*"

    lines = [
        "### 📸 Recent Posts",
        "",
    ]

    for p in posts:
        icon = _POST_TYPE_ICON.get(p.post_type, "📸")

        # Header line: icon, link, timestamp, age
        type_label = p.post_type.upper() if p.post_type else "POST"
        if p.carousel_count > 1:
            type_label += f" ×{p.carousel_count}"
        pin_badge = " 📌 **PINNED**" if p.is_pinned else ""
        lines.append("---")
        lines.append(
            f"**{icon} [{p.shortcode}]({p.post_url})** `{type_label}`{pin_badge} — "
            f"`{p.taken_at_str}` ({p.age_days}d ago)"
        )

        # Engagement
        engagement = f"❤️ {p.likes:,} · 💬 {p.comments:,}"
        if p.video_view_count > 0:
            engagement += f" · 👁️ {format_followers(p.video_view_count)} views"
        lines.append(engagement)

        # Dimensions
        if p.width > 0 and p.height > 0:
            lines.append(f"📐 {p.width}×{p.height}px")

        # Location
        if p.location:
            lines.append(f"📍 {_format_location(p.location)}")

        # Music (Reels)
        if p.music_title or p.music_artist:
            music_str = p.music_title or ""
            if p.music_artist:
                music_str = f"{music_str} — {p.music_artist}" if music_str else p.music_artist
            lines.append(f"🎵 **Music**: {music_str}")

        # People
        if p.coauthors:
            lines.append(f"🤝 **Collab**: {', '.join('@' + c for c in p.coauthors)}")
        if p.sponsor_tags:
            lines.append(f"💼 **Sponsored**: {', '.join('@' + s for s in p.sponsor_tags)}")
        if p.usertags:
            lines.append(f"🏷️ **Usertags**: {', '.join('@' + t for t in p.usertags)}")
        if p.mentions:
            lines.append(f"📣 **Mentions**: {', '.join('@' + m for m in p.mentions)}")

        # Caption
        if p.caption:
            preview = p.caption[:150].replace("\n", " ")
            if len(p.caption) > 150:
                preview += "..."
            lines.append(f"> 📝 {preview}")
        elif p.accessibility_caption:
            preview = p.accessibility_caption[:120].replace("\n", " ")
            if len(p.accessibility_caption) > 120:
                preview += "..."
            lines.append(f"> ♿ {preview}")

        lines.append("")

    return "\n".join(lines)


def format_posts_json(posts: List[InstagramPost]) -> List[Dict[str, Any]]:
    """Posts → JSON list."""
    return [asdict(p) for p in posts]


# ═══════════════════════════════════════════════════════════════════════════════
# FULL PROFILE WITH TAGS
# ═══════════════════════════════════════════════════════════════════════════════

def format_profile_with_tags_markdown(
    profile: InstagramProfile,
    feed_tags: FeedTagResult,
    is_dead: bool,
    last_post_days: int,
) -> str:
    """Profile + tags + status → full beautiful Markdown."""
    sections = [format_profile_markdown(profile)]

    if profile.is_private:
        sections.append("\n⚠️ **Private account** — feed data is not visible.")
    else:
        # Account status — beautiful card
        sections.append("")
        if is_dead:
            sections.append(
                f"> 💀 **DEAD account** — newest post **{last_post_days}** days ago\n"
                f"> This account is not active."
            )
        else:
            sections.append(
                f"> ✅ **Active account** — newest post **{last_post_days}** days ago"
            )

        # Tags
        sections.append("")
        sections.append(format_feed_tags_markdown(feed_tags))

        # Posts
        sections.append("")
        sections.append(format_posts_markdown(feed_tags.posts))

    return "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════════
# BULK FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_bulk_results_markdown(results: List[Dict[str, Any]]) -> str:
    """Bulk results → structured Markdown table."""
    found = sum(1 for r in results if r.get("found"))
    total = len(results)

    lines = [
        f"## 📊 Bulk Profile Results",
        f"**{found}/{total}** accounts found",
        "",
        "| # | Username | Followers | Category | Status |",
        "|:--|:---------|----------:|:---------|:-------|",
    ]

    for i, r in enumerate(results, 1):
        username = r.get("username", "?")
        if not r.get("found"):
            lines.append(f"| {i} | @{username} | — | — | ❌ Not found |")
            continue

        # Status badge
        status_parts = []
        if r.get("is_dead"):
            status_parts.append("💀 Dead")
        elif r.get("is_private"):
            status_parts.append("🔒 Private")
        else:
            status_parts.append("✅ Active")

        if r.get("is_verified"):
            status_parts.append("☑️")

        status = " ".join(status_parts)
        category = r.get("category", "") or "—"
        followers = r.get("followers", 0)

        lines.append(f"| {i} | @{username} | **{format_followers(followers)}** | {category} | {status} |")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# ACCOUNT STATUS FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_account_status_markdown(
    username: str,
    status: str,
    is_dead: bool,
    is_private: bool,
    last_post_days: int,
    followers: int,
    posts_count: int,
    dead_threshold_days: int,
) -> str:
    """Account status → beautiful Markdown."""
    icon = {
        "active": "✅",
        "dead": "💀",
        "private": "🔒",
        "not_found": "❌",
    }.get(status, "❓")

    lines = [
        f"## {icon} @{username} — **{status.upper()}**",
        "",
        "| Metric | Value |",
        "|:----|----:|",
        f"| 👥 Followers | **{format_followers(followers)}** |",
        f"| 📸 Posts | {posts_count:,} |",
    ]

    if status in ("active", "dead"):
        lines.append(f"| 📅 Last post | **{last_post_days}** days ago |")

    if status == "dead":
        lines.append(f"| ⚠️ Dead threshold | {dead_threshold_days} days |")
        lines.append("")
        lines.append(f"> 💀 This account hasn't posted in **{last_post_days}** days.")
    elif status == "active":
        lines.append("")
        lines.append(f"> ✅ Account is **active** — last post {last_post_days} days ago.")
    elif status == "private":
        lines.append("")
        lines.append("> 🔒 **Private** account — feed data is not visible.")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_diagnostics_markdown(
    cache_stats: CacheStats,
    proxy_statuses: List[ProxyStatus],
    proxy_summary: dict,
    rate_stats: dict,
) -> str:
    """Server diagnostics → structured Markdown."""
    lines = [
        "## 🔧 Instagram MCP Server Status",
        "",
        "### 📦 Cache",
        "",
        "| Metric | Value |",
        "|:----|----:|",
        f"| Enabled | {cache_stats.enabled} |",
        f"| Entries | {cache_stats.total_entries}/{cache_stats.max_entries} |",
        f"| Hit rate | **{cache_stats.hit_rate:.1%}** |",
        f"| Hits / Misses | {cache_stats.hits} / {cache_stats.misses} |",
        f"| Evictions | {cache_stats.evictions} |",
        "",
        "### 🌐 Proxies",
        "",
        f"**{proxy_summary.get('active_proxies', 0)}/{proxy_summary.get('total_proxies', 0)}** active"
        f" · {proxy_summary.get('total_fallbacks', 0)} fallback",
    ]

    if proxy_statuses:
        lines.extend([
            "",
            "| Status | Proxy | Success | Latency | Requests |",
            "|:-------|:------|--------:|--------:|---------:|",
        ])
        for ps in proxy_statuses:
            status_icon = "🟢" if ps.is_active else "🔴"
            cooldown = f" ⏳{ps.cooldown_remaining_s}s" if not ps.is_active and ps.cooldown_remaining_s > 0 else ""
            lines.append(
                f"| {status_icon}{cooldown} | `{ps.url_masked}` | "
                f"{ps.success_rate:.0%} | {ps.avg_latency_ms:.0f}ms | {ps.total_requests} |"
            )

    lines.extend([
        "",
        "### ⚡ Rate Limiter",
        "",
        f"RPS: **{rate_stats.get('current_rps', 0)}** · "
        f"Burst: {rate_stats.get('burst', 0)} · "
        f"Tokens: {rate_stats.get('tokens_available', 0)} · "
        f"Total: {rate_stats.get('total_requests', 0)}",
    ])

    return "\n".join(lines)


def format_diagnostics_json(
    cache_stats: CacheStats,
    proxy_statuses: List[ProxyStatus],
    proxy_summary: dict,
    rate_stats: dict,
) -> str:
    """Server diagnostics → JSON string."""
    return json.dumps({
        "cache": asdict(cache_stats),
        "proxies": {
            "summary": proxy_summary,
            "details": [asdict(ps) for ps in proxy_statuses],
        },
        "rate_limiter": rate_stats,
    }, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# DEEP FEED (PAGINATED) FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_deep_feed_markdown(
    profile: InstagramProfile,
    feed_result: FeedTagResult,
    is_dead: bool = False,
    last_post_days: int = 0,
) -> str:
    """Deep feed analysis → Markdown with pagination stats."""
    sections = [format_profile_markdown(profile)]

    if profile.is_private:
        sections.append("\n⚠️ **Private account** — feed data is not visible.")
        return "\n\n".join(sections)

    # Account status
    if is_dead:
        sections.append(f"💀 **Dead account** — last post {last_post_days} days ago")
    elif last_post_days > 0:
        sections.append(f"✅ **Active** — last post {last_post_days} days ago")

    # Pagination stats
    sections.append("")
    sections.append("### 📊 Feed Analysis Summary")
    sections.append("")
    pinned_count = sum(1 for p in feed_result.posts if p.is_pinned)
    stats_lines = [
        f"- **Posts analyzed**: {feed_result.posts_checked}",
        f"- **Pages fetched**: {feed_result.pages_fetched}",
        f"- **Pinned posts**: {pinned_count} 📌" if pinned_count else f"- **Pinned posts**: 0",
        f"- **Posts with tags**: {feed_result.posts_with_tags}",
        f"- **Unique tags found**: {len(feed_result.tags)}",
    ]
    if feed_result.has_more_posts:
        stats_lines.append("- **More posts available**: ✅ Yes (increase `max_posts` to fetch more)")
    else:
        stats_lines.append("- **More posts available**: ❌ No (all posts fetched)")
    sections.append("\n".join(stats_lines))

    # Tags section
    if feed_result.tags:
        sections.append("")
        sections.append("### 🏷️ Tags & Mentions Found")
        sections.append("")

        # Tag frequency (count how many posts each tag appears in)
        tag_freq: Dict[str, int] = {}
        for post in feed_result.posts:
            for tag in set(post.usertags + post.mentions):
                tag_freq[tag] = tag_freq.get(tag, 0) + 1

        # Sort by frequency
        sorted_tags = sorted(tag_freq.items(), key=lambda x: x[1], reverse=True)

        tag_lines = ["| Tag | Count | First Post |", "|:----|------:|:-----------|"]
        for tag, count in sorted_tags:
            shortcode = feed_result.tag_shortcodes.get(tag, "")
            post_link = f"[link](https://www.instagram.com/p/{shortcode}/)" if shortcode else ""
            tag_lines.append(f"| @{tag} | {count} | {post_link} |")
        sections.append("\n".join(tag_lines))

    # Engagement summary
    if feed_result.posts:
        total_likes = sum(p.likes for p in feed_result.posts)
        total_comments = sum(p.comments for p in feed_result.posts)
        avg_likes = total_likes // len(feed_result.posts) if feed_result.posts else 0
        avg_comments = total_comments // len(feed_result.posts) if feed_result.posts else 0

        sections.append("")
        sections.append("### 📈 Engagement Overview")
        sections.append("")
        sections.append(
            f"| Metric | Total | Average per Post |\n"
            f"|:-------|------:|:----------------:|\n"
            f"| ❤️ Likes | {total_likes:,} | {avg_likes:,} |\n"
            f"| 💬 Comments | {total_comments:,} | {avg_comments:,} |"
        )

    return "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════════
# ENGAGEMENT ANALYSIS FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _engagement_rate_label(er: float) -> str:
    """Human label for engagement rate."""
    if er >= 6.0:
        return "🔥 Excellent (6%+)"
    if er >= 3.0:
        return "✅ Good (3–6%)"
    if er >= 1.0:
        return "⚠️ Average (1–3%)"
    return "❌ Low (<1%)"


def _compute_engagement(posts: List[InstagramPost], followers: int) -> Dict[str, Any]:
    """Shared computation for engagement analytics."""
    if not posts:
        return {}

    num = len(posts)
    total_likes = sum(p.likes for p in posts)
    total_comments = sum(p.comments for p in posts)
    total_views = sum(p.video_view_count for p in posts)
    avg_likes = total_likes / num
    avg_comments = total_comments / num

    er = ((avg_likes + avg_comments) / followers * 100) if followers > 0 else 0.0

    # Content mix
    type_stats: Dict[str, Dict[str, Any]] = {}
    for p in posts:
        t = p.post_type or "image"
        if t not in type_stats:
            type_stats[t] = {"count": 0, "likes": 0, "comments": 0, "views": 0}
        type_stats[t]["count"] += 1
        type_stats[t]["likes"] += p.likes
        type_stats[t]["comments"] += p.comments
        type_stats[t]["views"] += p.video_view_count

    content_mix = {
        t: {
            "count": s["count"],
            "avg_likes": round(s["likes"] / s["count"]),
            "avg_comments": round(s["comments"] / s["count"]),
            "avg_views": round(s["views"] / s["count"]) if s["count"] > 0 else 0,
        }
        for t, s in type_stats.items()
    }

    # Best days (0=Monday … 6=Sunday)
    day_stats: Dict[int, Dict[str, Any]] = {}
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for p in posts:
        if not p.taken_at:
            continue
        try:
            weekday = _dt.fromtimestamp(p.taken_at).weekday()
        except Exception:
            continue
        if weekday not in day_stats:
            day_stats[weekday] = {"count": 0, "likes": 0}
        day_stats[weekday]["count"] += 1
        day_stats[weekday]["likes"] += p.likes

    best_days = sorted(
        [
            {"day": day_names[d], "posts": s["count"], "avg_likes": round(s["likes"] / s["count"])}
            for d, s in day_stats.items()
        ],
        key=lambda x: x["avg_likes"],
        reverse=True,
    )

    # Top 5 posts by likes
    top_posts = sorted(posts, key=lambda p: p.likes, reverse=True)[:5]

    # Top hashtags
    hashtag_counter: Counter = Counter()
    for p in posts:
        hashtag_counter.update(p.hashtags)
    top_hashtags = hashtag_counter.most_common(15)

    return {
        "posts_analyzed": num,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_views": total_views,
        "avg_likes": round(avg_likes),
        "avg_comments": round(avg_comments),
        "engagement_rate": round(er, 2),
        "er_label": _engagement_rate_label(er),
        "content_mix": content_mix,
        "best_days": best_days,
        "top_posts": top_posts,
        "top_hashtags": top_hashtags,
    }


def format_engagement_analysis_markdown(
    profile: InstagramProfile,
    posts: List[InstagramPost],
) -> str:
    """Engagement analytics → structured Markdown."""
    lines = [format_profile_markdown(profile), ""]

    if not posts:
        lines.append("*No posts found for the specified period.*")
        return "\n".join(lines)

    stats = _compute_engagement(posts, profile.followers)

    lines += [
        "### 📈 Engagement Analysis",
        "",
        "| Metric | Value |",
        "|:----|----:|",
        f"| 📸 Posts analyzed | {stats['posts_analyzed']} |",
        f"| ❤️ Avg likes | **{stats['avg_likes']:,}** |",
        f"| 💬 Avg comments | {stats['avg_comments']:,} |",
        f"| 📊 Engagement rate | **{stats['engagement_rate']:.2f}%** |",
        f"| 🏆 ER rating | {stats['er_label']} |",
    ]
    if stats["total_views"]:
        lines.append(f"| 👁️ Total video views | {format_followers(stats['total_views'])} |")

    # Content mix
    lines += ["", "### 🎬 Content Mix", ""]
    lines.append("| Type | Count | Avg ❤️ | Avg 💬 |")
    lines.append("|:-----|------:|------:|------:|")
    for t, s in sorted(stats["content_mix"].items(), key=lambda x: x[1]["count"], reverse=True):
        icon = _POST_TYPE_ICON.get(t, "📸")
        lines.append(f"| {icon} {t.capitalize()} | {s['count']} | {s['avg_likes']:,} | {s['avg_comments']:,} |")

    # Best posting days
    if stats["best_days"]:
        lines += ["", "### 📅 Best Posting Days (by avg likes)", ""]
        lines.append("| Day | Posts | Avg ❤️ |")
        lines.append("|:----|------:|------:|")
        for d in stats["best_days"][:5]:
            lines.append(f"| {d['day']} | {d['posts']} | {d['avg_likes']:,} |")

    # Top hashtags
    if stats["top_hashtags"]:
        lines += ["", "### #️⃣ Top Hashtags", ""]
        tags_str = " · ".join(f"`#{h}` ({c})" for h, c in stats["top_hashtags"][:10])
        lines.append(tags_str)

    # Top 5 posts
    lines += ["", "### 🏆 Top Performing Posts", ""]
    lines.append("| Post | Type | ❤️ Likes | 💬 Comments | Date |")
    lines.append("|:-----|:-----|--------:|----------:|:-----|")
    for p in stats["top_posts"]:
        icon = _POST_TYPE_ICON.get(p.post_type, "📸")
        link = f"[{p.shortcode}]({p.post_url})" if p.post_url else p.shortcode
        lines.append(f"| {link} | {icon} {p.post_type} | {p.likes:,} | {p.comments:,} | {p.taken_at_str} |")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# COLLAB NETWORK FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_collab_network(posts: List[InstagramPost]) -> Dict[str, Any]:
    """Build collaboration network from posts."""
    usertag_counter: Counter = Counter()
    mention_counter: Counter = Counter()
    coauthor_counter: Counter = Counter()
    sponsor_counter: Counter = Counter()

    # First post shortcode per person
    usertag_first: Dict[str, str] = {}
    mention_first: Dict[str, str] = {}
    coauthor_first: Dict[str, str] = {}
    sponsor_first: Dict[str, str] = {}

    for p in posts:
        for u in p.usertags:
            usertag_counter[u] += 1
            if u not in usertag_first:
                usertag_first[u] = p.shortcode
        for m in p.mentions:
            mention_counter[m] += 1
            if m not in mention_first:
                mention_first[m] = p.shortcode
        for c in p.coauthors:
            coauthor_counter[c] += 1
            if c not in coauthor_first:
                coauthor_first[c] = p.shortcode
        for s in p.sponsor_tags:
            sponsor_counter[s] += 1
            if s not in sponsor_first:
                sponsor_first[s] = p.shortcode

    def _serialize(counter: Counter, first_map: Dict[str, str]) -> List[Dict]:
        return [
            {
                "username": u,
                "frequency": c,
                "first_post": first_map.get(u, ""),
                "first_post_url": f"https://www.instagram.com/p/{first_map[u]}/" if first_map.get(u) else "",
            }
            for u, c in counter.most_common()
        ]

    return {
        "usertags": _serialize(usertag_counter, usertag_first),
        "mentions": _serialize(mention_counter, mention_first),
        "coauthors": _serialize(coauthor_counter, coauthor_first),
        "sponsors": _serialize(sponsor_counter, sponsor_first),
        "total_unique_people": len(
            set(usertag_counter) | set(mention_counter) |
            set(coauthor_counter) | set(sponsor_counter)
        ),
        "posts_analyzed": len(posts),
    }


def _network_table(items: List[Dict], min_freq: int = 1) -> List[str]:
    filtered = [i for i in items if i["frequency"] >= min_freq]
    if not filtered:
        return ["*None found.*"]
    lines = ["| # | Username | Times | First Post |", "|:--|:---------|------:|:-----------|"]
    for idx, i in enumerate(filtered, 1):
        link = f"[view]({i['first_post_url']})" if i["first_post_url"] else "—"
        lines.append(f"| {idx} | @{i['username']} | {i['frequency']} | {link} |")
    return lines


def format_collab_network_markdown(
    profile: InstagramProfile,
    posts: List[InstagramPost],
    min_frequency: int = 1,
) -> str:
    """Collaboration network → structured Markdown."""
    lines = [f"## 🤝 @{profile.username} — Collaboration Network", ""]

    if not posts:
        lines.append("*No posts found for the specified period.*")
        return "\n".join(lines)

    net = _build_collab_network(posts)

    lines += [
        f"**{net['posts_analyzed']}** posts analyzed · "
        f"**{net['total_unique_people']}** unique people found",
        "",
    ]

    sections = [
        ("🏷️ Photo Usertags", net["usertags"]),
        ("📣 Caption Mentions", net["mentions"]),
        ("🤝 Official Co-authors", net["coauthors"]),
        ("💼 Paid Sponsors", net["sponsors"]),
    ]
    for title, items in sections:
        filtered = [i for i in items if i["frequency"] >= min_frequency]
        lines.append(f"### {title} ({len(filtered)} people)")
        lines.append("")
        lines.extend(_network_table(filtered))
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# PROFILE COMPARISON FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_compare_profiles_markdown(
    entries: List[Tuple[InstagramProfile, bool, int]],
) -> str:
    """Profile comparison → Markdown table.

    entries: list of (profile, is_dead, last_post_days)
    """
    if not entries:
        return "*No profiles to compare.*"

    headers = ["Metric"] + [f"@{p.username}" for p, _, _ in entries]
    sep = [":----"] + ["----:" for _ in entries]

    def row(label: str, values: List[str]) -> str:
        return "| " + " | ".join([label] + values) + " |"

    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "| " + " | ".join(sep) + " |"
    lines = ["## ⚖️ Profile Comparison", "", header_row, sep_row]

    def col_vals(fn) -> List[str]:
        return [fn(p, dead, days) for p, dead, days in entries]

    # Status
    def status(p, dead, days):
        if not p.username:
            return "❌ Not found"
        if p.is_private:
            return "🔒 Private"
        if dead:
            return f"💀 Dead ({days}d)"
        return f"✅ Active ({days}d)"

    lines.append(row("📊 Status", col_vals(status)))
    lines.append(row("👥 Followers", col_vals(lambda p, *_: f"**{format_followers(p.followers)}**" if p.followers else "—")))
    lines.append(row("📸 Posts", col_vals(lambda p, *_: f"{p.posts_count:,}" if p.posts_count else "—")))
    lines.append(row("👤 Following", col_vals(lambda p, *_: format_followers(p.following) if p.following else "—")))
    lines.append(row("✅ Verified", col_vals(lambda p, *_: "✅ Yes" if p.is_verified else "No")))
    lines.append(row("🏷️ Type", col_vals(lambda p, *_: _ACCOUNT_TYPE_LABEL.get(p.account_type, "—") or ("Business" if p.is_business else "—"))))
    lines.append(row("📂 Category", col_vals(lambda p, *_: p.category or "—")))
    lines.append(row("🎬 Reels", col_vals(lambda p, *_: "✅" if p.has_reels else "—")))
    lines.append(row("🔗 Website", col_vals(lambda p, *_: "✅" if (p.website or p.external_url) else "—")))
    lines.append(row("🆔 User ID", col_vals(lambda p, *_: f"`{p.user_id}`" if p.user_id else "—")))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TAGGED-BY FORMATTER  (instagram_tagged_by — authenticated tool)
# ═══════════════════════════════════════════════════════════════════════════════

_TAGGED_TYPE_ICON = {
    "image": "🖼️",
    "video": "🎬",
    "carousel": "📸",
}


def format_tagged_by_markdown(
    profile: InstagramProfile,
    posts: List[TaggedPost],
    min_poster_followers: int = 0,
) -> str:
    """Format the Tagged-By feed (posts by OTHERS that tag this account)."""
    lines: List[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        f"## 🔐 Tagged-By Feed — @{profile.username}",
        "",
        f"Posts made **by other accounts** that tagged **@{profile.username}**.",
        f"Fetched via authenticated session (Tagged Tab endpoint).",
        "",
    ]

    if not posts:
        lines.append("*No tagged posts found — the account may have no tagged content, "
                     "or the Tagged tab is hidden.*")
        return "\n".join(lines)

    # ── Summary stats ─────────────────────────────────────────────────────────
    total = len(posts)
    total_likes = sum(p.likes for p in posts)
    total_comments = sum(p.comments for p in posts)
    avg_likes = total_likes // total if total else 0
    avg_comments = total_comments // total if total else 0

    type_counts: Counter = Counter(p.post_type for p in posts if p.post_type)
    unique_posters = len({p.poster_username for p in posts if p.poster_username})

    lines += [
        "### Summary",
        "",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Total tagged posts | **{total}** |",
        f"| Unique posters | {unique_posters} |",
        f"| Avg likes per post | {avg_likes:,} |",
        f"| Avg comments per post | {avg_comments:,} |",
    ]
    if type_counts:
        for ptype, cnt in type_counts.most_common():
            icon = _TAGGED_TYPE_ICON.get(ptype, "📄")
            lines.append(f"| {icon} {ptype.capitalize()} | {cnt} |")
    lines.append("")

    # ── Top posters ───────────────────────────────────────────────────────────
    poster_counter: Counter = Counter(p.poster_username for p in posts if p.poster_username)
    if poster_counter:
        lines += ["### Top Posters", ""]
        lines.append("| Poster | Posts tagging you |")
        lines.append("|--------|------------------:|")
        for poster, count in poster_counter.most_common(10):
            lines.append(f"| [@{poster}](https://instagram.com/{poster}) | {count} |")
        lines.append("")

    # ── Post list ─────────────────────────────────────────────────────────────
    lines += ["### Tagged Posts", ""]
    lines.append("| # | Poster | Type | Likes | Comments | Date | Post |")
    lines.append("|---|--------|------|------:|---------:|------|------|")

    for i, p in enumerate(posts, 1):
        icon = _TAGGED_TYPE_ICON.get(p.post_type, "📄")
        poster = f"[@{p.poster_username}](https://instagram.com/{p.poster_username})" if p.poster_username else "—"
        date = p.taken_at_str or "—"
        post_link = f"[View]({p.post_url})" if p.post_url else "—"
        lines.append(
            f"| {i} | {poster} | {icon} {p.post_type or '—'} "
            f"| {p.likes:,} | {p.comments:,} | {date} | {post_link} |"
        )

    lines.append("")

    # ── Caption snippets for top posts ───────────────────────────────────────
    top_by_likes = sorted(posts, key=lambda p: p.likes, reverse=True)[:5]
    caption_posts = [p for p in top_by_likes if p.caption]
    if caption_posts:
        lines += ["### Top Posts (by likes) — Caption Snippets", ""]
        for p in caption_posts:
            caption_preview = p.caption[:120].replace("\n", " ")
            if len(p.caption) > 120:
                caption_preview += "…"
            poster_str = f"@{p.poster_username}" if p.poster_username else "unknown"
            lines += [
                f"**{poster_str}** — {p.likes:,} ♥  [{p.taken_at_str or 'unknown date'}]({p.post_url})",
                f"> {caption_preview}",
                "",
            ]

    if min_poster_followers > 0:
        lines += [
            f"*Note: min_poster_followers={min_poster_followers:,} was requested but follower "
            f"filtering requires additional API calls per poster. The full list is shown above — "
            f"filter manually by checking individual poster profiles.*",
            "",
        ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# REPOSTS FORMATTER  (instagram_reposts — authenticated tool)
# ═══════════════════════════════════════════════════════════════════════════════

_REPOST_TYPE_ICON = {
    "image": "🖼️",
    "video": "🎬",
    "carousel": "📸",
    "reels": "🎥",
}


def format_reposts_markdown(
    profile: InstagramProfile,
    items: List[RepostItem],
) -> str:
    """
    Format the Reposts Tab feed — content this account actively chose to amplify.

    Insight framing: reposts = endorsements. Every item here is a conscious
    decision by the account to surface someone else's content to their audience.
    """
    lines: List[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        f"## 🔐 Reposts — @{profile.username}",
        "",
        f"Content made **by other accounts** that **@{profile.username}** chose to repost.",
        f"Each entry represents an active endorsement — the account decided to share "
        f"this creator's content with their own audience.",
        "",
    ]

    if not items:
        lines.append(
            "*No reposts found — the account may not use the Repost feature, "
            "or reposts are not publicly visible.*"
        )
        return "\n".join(lines)

    # ── Summary stats ─────────────────────────────────────────────────────────
    total = len(items)
    total_likes = sum(p.likes for p in items)
    total_comments = sum(p.comments for p in items)
    avg_likes = total_likes // total if total else 0
    avg_comments = total_comments // total if total else 0
    unique_creators = len({p.orig_username for p in items if p.orig_username})

    type_counts: Counter = Counter(p.post_type for p in items if p.post_type)

    lines += [
        "### Summary",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Total reposts | **{total}** |",
        f"| Unique original creators | {unique_creators} |",
        f"| Avg likes (original post) | {avg_likes:,} |",
        f"| Avg comments (original post) | {avg_comments:,} |",
    ]
    for ptype, cnt in type_counts.most_common():
        icon = _REPOST_TYPE_ICON.get(ptype, "📄")
        lines.append(f"| {icon} {ptype.capitalize()} | {cnt} |")
    lines.append("")

    # ── Top endorsed creators ─────────────────────────────────────────────────
    creator_counter: Counter = Counter(p.orig_username for p in items if p.orig_username)
    if creator_counter:
        lines += [
            "### Top Endorsed Creators",
            "",
            f"Accounts whose content @{profile.username} reposts most frequently:",
            "",
            "| Creator | Times reposted | Avg likes |",
            "|---------|---------------:|----------:|",
        ]
        for creator, count in creator_counter.most_common(10):
            creator_items = [p for p in items if p.orig_username == creator]
            avg = sum(p.likes for p in creator_items) // len(creator_items)
            lines.append(
                f"| [@{creator}](https://instagram.com/{creator}) "
                f"| **{count}** | {avg:,} |"
            )
        lines.append("")

    # ── Repost list ───────────────────────────────────────────────────────────
    lines += ["### All Reposts", ""]
    lines.append("| # | Original Creator | Type | Likes | Comments | Date | Post |")
    lines.append("|---|-----------------|------|------:|---------:|------|------|")

    for i, p in enumerate(items, 1):
        icon = _REPOST_TYPE_ICON.get(p.post_type, "📄")
        creator = (
            f"[@{p.orig_username}](https://instagram.com/{p.orig_username})"
            if p.orig_username else "—"
        )
        date = p.taken_at_str or "—"
        post_link = f"[View]({p.post_url})" if p.post_url else "—"
        lines.append(
            f"| {i} | {creator} | {icon} {p.post_type or '—'} "
            f"| {p.likes:,} | {p.comments:,} | {date} | {post_link} |"
        )
    lines.append("")

    # ── High-performing reposts (caption snippets) ────────────────────────────
    top_by_likes = sorted(items, key=lambda p: p.likes, reverse=True)[:5]
    caption_items = [p for p in top_by_likes if p.caption]
    if caption_items:
        lines += [
            f"### Top Reposts (by original likes) — Caption Snippets",
            "",
        ]
        for p in caption_items:
            preview = p.caption[:140].replace("\n", " ")
            if len(p.caption) > 140:
                preview += "…"
            creator_str = f"@{p.orig_username}" if p.orig_username else "unknown"
            lines += [
                f"**{creator_str}** — {p.likes:,} ♥  [{p.taken_at_str or 'unknown date'}]({p.post_url})",
                f"> {preview}",
                "",
            ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# REELS FORMATTER  (instagram_reels — authenticated tool)
# ═══════════════════════════════════════════════════════════════════════════════

def format_reels_markdown(
    profile: InstagramProfile,
    reels: List[ReelItem],
) -> str:
    """
    Format the Reels Tab — account's own reels with play counts.

    play_count is the headline metric because view_count is always null
    in the Reels Tab API response. This is the only endpoint that exposes
    true play counts for individual reels.
    """
    lines: List[str] = []

    lines += [
        f"## 🔐 Reels — @{profile.username}",
        "",
        f"Reels posted by **@{profile.username}** with play counts.",
        f"Fetched via authenticated session (Reels Tab endpoint).",
        "",
    ]

    if not reels:
        lines.append(
            "*No reels found — the account may not have posted reels, "
            "or the Reels tab is empty.*"
        )
        return "\n".join(lines)

    total = len(reels)
    total_plays = sum(r.play_count for r in reels)
    total_likes = sum(r.like_count for r in reels)
    total_comments = sum(r.comment_count for r in reels)
    avg_plays = total_plays // total if total else 0
    avg_likes = total_likes // total if total else 0
    avg_comments = total_comments // total if total else 0

    lines += [
        "### Summary",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Total reels | **{total}** |",
        f"| Total plays | **{format_followers(total_plays)}** |",
        f"| Avg plays | {format_followers(avg_plays)} |",
        f"| Avg likes | {avg_likes:,} |",
        f"| Avg comments | {avg_comments:,} |",
        "",
    ]

    # Top 5 by play_count
    top_reels = sorted(reels, key=lambda r: r.play_count, reverse=True)[:5]
    lines += ["### Top Reels (by plays)", ""]
    lines.append("| # | Post | Plays | Likes | Comments | Date |")
    lines.append("|---|------|------:|------:|---------:|------|")
    for i, r in enumerate(top_reels, 1):
        post_link = f"[{r.shortcode}]({r.post_url})" if r.post_url else r.shortcode or "—"
        lines.append(
            f"| {i} | {post_link} | **{format_followers(r.play_count)}** "
            f"| {r.like_count:,} | {r.comment_count:,} | {r.taken_at_str or '—'} |"
        )
    lines.append("")

    # All reels table
    lines += ["### All Reels", ""]
    lines.append("| # | Post | Plays | Likes | Comments | Date |")
    lines.append("|---|------|------:|------:|---------:|------|")
    for i, r in enumerate(reels, 1):
        post_link = f"[{r.shortcode}]({r.post_url})" if r.post_url else r.shortcode or "—"
        pinned = " 📌" if r.is_pinned else ""
        lines.append(
            f"| {i} | {post_link}{pinned} | {format_followers(r.play_count)} "
            f"| {r.like_count:,} | {r.comment_count:,} | {r.taken_at_str or '—'} |"
        )
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# POST FORMATTER  (instagram_post — anonymous tool)
# ═══════════════════════════════════════════════════════════════════════════════

_POST_TYPE_ICON = {
    "image":    "🖼️",
    "video":    "🎬",
    "carousel": "📸",
    "reels":    "🎥",
    "unknown":  "📄",
}


def format_post_markdown(info: PostInfo) -> str:
    """Format a single PostInfo into a rich, LLM-friendly Markdown report."""
    lines: List[str] = []

    type_icon = _POST_TYPE_ICON.get(info.post_type, "📄")
    verified = " ✅" if info.is_verified else ""
    author = f"@{info.username}{verified}" if info.username else "unknown"

    lines += [
        f"## {type_icon} Instagram Post — [{author}]({info.post_url})",
        "",
    ]

    # ── Location (hero section — most valuable for this tool) ─────────────────
    if info.location.has_location:
        loc = info.location
        lines += ["### 📍 Location", ""]
        lines.append(f"**{loc.name}**" if loc.name else "*(name not available)*")
        if loc.lat and loc.lng:
            lines.append(f"Coordinates: `{loc.lat:.6f}, {loc.lng:.6f}`")
        if loc.maps_url:
            lines.append(f"[Open in Google Maps]({loc.maps_url})")
        lines.append("")
    else:
        lines += ["### 📍 Location", "", "*No location tag on this post.*", ""]

    # ── Post metadata ─────────────────────────────────────────────────────────
    lines += ["### Post Details", ""]
    lines.append(f"| Field | Value |")
    lines.append(f"|-------|-------|")
    lines.append(f"| Post | [{info.shortcode}]({info.post_url}) |")
    lines.append(f"| Author | [{author}](https://instagram.com/{info.username}) |")
    lines.append(f"| Type | {type_icon} {info.post_type.capitalize()} |")
    if info.taken_at_str:
        lines.append(f"| Posted | {info.taken_at_str} |")
    if info.likes:
        lines.append(f"| Likes | {info.likes:,} |")
    if info.comments:
        lines.append(f"| Comments | {info.comments:,} |")
    if info.view_count:
        lines.append(f"| Views | {info.view_count:,} |")
    if info.play_count:
        lines.append(f"| Plays | {info.play_count:,} |")
    if info.carousel_count:
        lines.append(f"| Slides | {info.carousel_count} |")
    if info.width and info.height:
        lines.append(f"| Dimensions | {info.width}×{info.height} |")
    if info.duration_secs:
        lines.append(f"| Duration | {info.duration_secs:.1f}s |")
    lines.append("")

    # ── Caption ───────────────────────────────────────────────────────────────
    if info.caption:
        lines += ["### Caption", ""]
        # Show full caption wrapped in blockquote, max 600 chars
        cap_display = info.caption[:600]
        if len(info.caption) > 600:
            cap_display += "…"
        for cap_line in cap_display.split("\n"):
            lines.append(f"> {cap_line}" if cap_line.strip() else ">")
        lines.append("")

    # ── Tags & mentions ───────────────────────────────────────────────────────
    if info.hashtags:
        lines += [
            "### Hashtags",
            "",
            " ".join(f"[#{h}](https://instagram.com/explore/tags/{h}/)" for h in info.hashtags[:30]),
            "",
        ]

    people: List[str] = []
    if info.usertags:
        people += [f"[@{u}](https://instagram.com/{u}/) *(tagged in photo)*" for u in info.usertags]
    if info.mentions:
        seen = set(info.usertags)
        for u in info.mentions:
            if u not in seen:
                people.append(f"[@{u}](https://instagram.com/{u}/) *(caption mention)*")
                seen.add(u)
    if info.coauthors:
        for u in info.coauthors:
            people.append(f"[@{u}](https://instagram.com/{u}/) *(co-author)*")
    if people:
        lines += ["### People", "", *people, ""]

    # ── Music (reels) ─────────────────────────────────────────────────────────
    if info.music_artist or info.music_title:
        music = " — ".join(filter(None, [info.music_artist, info.music_title]))
        lines += ["### Music", "", f"🎵 {music}", ""]

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# COMMENTS FORMATTER
# ═════════════════════════════════════════════════════════════════════════════

def format_comments_markdown(
    shortcode: str,
    post_url: str,
    comment_count: int,
    comments: List[CommentItem],
    pages_fetched: int,
    sort_order: str,
) -> str:
    """Render a post's comments as structured Markdown."""
    lines: List[str] = []
    post_link = f"[{shortcode}]({post_url})"

    caption = next((c for c in comments if c.is_caption), None)
    actual = [c for c in comments if not c.is_caption]

    lines += [f"## Comments — {post_link}", ""]

    # ── Summary ───────────────────────────────────────────────────────────────
    total_likes = sum(c.comment_like_count for c in actual)
    avg_likes = total_likes / len(actual) if actual else 0
    with_replies = sum(1 for c in actual if c.child_comment_count > 0)
    translated = sum(1 for c in actual if c.has_translation)
    gif_count = sum(1 for c in actual if c.has_gif)
    verified_count = sum(1 for c in actual if c.is_verified)

    lines += ["### Summary", ""]
    lines += ["| Stat | Value |", "|------|-------|"]
    lines.append(f"| Total comments | {comment_count:,} |")
    lines.append(f"| Fetched | {len(actual)} ({sort_order}) |")
    lines.append(f"| Total likes on comments | {total_likes:,} |")
    lines.append(f"| Avg likes / comment | {avg_likes:.1f} |")
    if with_replies:
        lines.append(f"| With threaded replies | {with_replies} |")
    if translated:
        pct = translated * 100 // len(actual) if actual else 0
        lines.append(f"| Non-English (auto-detected) | {translated} ({pct}%) |")
    if gif_count:
        lines.append(f"| GIF comments | {gif_count} |")
    if verified_count:
        lines.append(f"| Verified commenters | {verified_count} |")
    lines.append("")

    # ── Caption ───────────────────────────────────────────────────────────────
    if caption and caption.text:
        tick = " ✓" if caption.is_verified else ""
        cap_text = caption.text[:500] + ("…" if len(caption.text) > 500 else "")
        lines += ["### Caption", ""]
        lines.append(f"> **@{caption.username}{tick}** — {caption.created_at_str}")
        for line in cap_text.split("\n"):
            lines.append(f"> {line}" if line.strip() else ">")
        lines.append("")

    # ── Top comments by likes ─────────────────────────────────────────────────
    top = sorted(actual, key=lambda c: c.comment_like_count, reverse=True)[:5]
    if top and top[0].comment_like_count > 0:
        lines += ["### Top Comments by Likes", ""]
        lines += ["| # | Author | Comment | Likes | Replies |",
                  "|---|--------|---------|-------|---------|"]
        for i, c in enumerate(top, 1):
            tick = " ✓" if c.is_verified else ""
            text = (c.text[:70].replace("|", "\\|") + ("…" if len(c.text) > 70 else "")) if c.text else "[GIF]"
            replies = str(c.child_comment_count) if c.child_comment_count else "—"
            lines.append(f"| {i} | @{c.username}{tick} | {text} | {c.comment_like_count:,} | {replies} |")
        lines.append("")

    # ── Most frequent commenters ──────────────────────────────────────────────
    freq = Counter(c.username for c in actual if c.username)
    top_users = freq.most_common(5)
    if top_users and top_users[0][1] > 1:
        lines += ["### Most Frequent Commenters", ""]
        lines += ["| Username | Comments |", "|----------|----------|"]
        for uname, cnt in top_users:
            if cnt > 1:
                lines.append(f"| @{uname} | {cnt} |")
        lines.append("")

    # ── Full comment list ─────────────────────────────────────────────────────
    lines += [f"### All Comments ({len(actual)})", ""]
    lines += ["| # | Author | Comment | ❤ | 💬 | Time |",
              "|---|--------|---------|---|---|------|"]
    for c in actual:
        tick = " ✓" if c.is_verified else ""
        if c.has_gif:
            text = "[GIF]"
        elif c.text:
            text = c.text[:55].replace("|", "\\|") + ("…" if len(c.text) > 55 else "")
        else:
            text = "*(empty)*"
        likes = str(c.comment_like_count) if c.comment_like_count else "—"
        replies = str(c.child_comment_count) if c.child_comment_count else "—"
        idx = str(c.comment_index) if c.comment_index >= 0 else "—"
        lines.append(f"| {idx} | @{c.username}{tick} | {text} | {likes} | {replies} | {c.created_at_str} |")

    if pages_fetched > 1:
        lines += ["", f"*Fetched across {pages_fetched} pages.*"]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# HASHTAG FORMATTER
# ═══════════════════════════════════════════════════════════════════════════════

def format_search_markdown(
    query: str,
    users: list,
    hashtags: list,
    context: str,
    has_more: bool,
) -> str:
    """Format instagram_search results as readable markdown."""
    _fmt = format_followers
    lines = [
        f"# Instagram Search — \"{query}\"",
        "",
        f"**Context:** {context}  |  **Users:** {len(users)}  |  **Hashtags:** {len(hashtags)}",
        "",
    ]

    if not users and not hashtags:
        lines += ["*No results found.*"]
        return "\n".join(lines)

    if users:
        lines += [
            "## Accounts",
            "",
            "| # | Username | Full Name | ✓ | 🔒 | Social | Relation | 🎬 | 🧵 |",
            "|---|----------|-----------|---|----|----|-------|----|-----|",
        ]
        for u in users:
            pos      = u.get("position", "")
            username = u.get("username", "")
            full     = u.get("full_name", "")[:26]
            verified = "✓" if u.get("is_verified") else ""
            private  = "🔒" if u.get("is_private") else ""
            followers = u.get("follower_count_text", "—")
            # relation column
            if u.get("you_follow_them") and u.get("they_follow_you"):
                relation = "mutual"
            elif u.get("you_follow_them"):
                relation = "you follow"
            elif u.get("they_follow_you"):
                relation = "follows you"
            elif u.get("follow_request_sent"):
                relation = "req sent"
            else:
                relation = "—"
            if u.get("is_bestie"):
                relation += " ⭐"
            reel   = "🎬" if u.get("has_recent_reel") else "—"
            threads = "🧵" if u.get("has_threads") else "—"
            lines.append(
                f"| {pos} | [@{username}](https://www.instagram.com/{username}/) | {full} | {verified} | {private} | {followers} | {relation} | {reel} | {threads} |"
            )

    if hashtags:
        lines += [
            "",
            "## Hashtags",
            "",
            "| # | Hashtag | Posts |",
            "|---|---------|-------|",
        ]
        for ht in hashtags:
            pos   = ht.get("position", "")
            name  = ht.get("name", "")
            count = ht.get("subtitle") or f"{ht.get('media_count', 0):,} posts"
            lines.append(f"| {pos} | [#{name}](https://www.instagram.com/explore/tags/{name}/) | {count} |")

    if has_more:
        lines += ["", "*More results available.*"]

    lines += ["", "---", "*Data: Instagram topsearch API — authenticated session.*"]
    return "\n".join(lines)


def _account_type_icon(at: int) -> str:
    return {1: "👤", 2: "🎨", 3: "🏢"}.get(at, "")


def _media_type_label(post: dict) -> str:
    mt = post.get("media_type", 1)
    pt = post.get("product_type", "")
    if mt == 8:
        count = post.get("carousel_count") or ""
        return f"🎠{count}"
    if mt == 2:
        dur = post.get("video_duration")
        dur_s = f" {dur:.0f}s" if dur else ""
        return f"🎬{dur_s}"
    return "🖼"


def format_hashtag_markdown(
    tag: str,
    posts: list,
    related_searches: list,
    has_more: bool,
    auth_used: bool = False,
) -> str:
    """Format hashtag top-posts result as readable markdown."""
    _fmt = format_followers

    mode_label = "🔐 auth — paginated" if auth_used else "🌐 anon — up to 12 posts"
    lines = [
        f"# #{tag} — Top Posts",
        "",
        f"**Mode:** {mode_label}  |  **Posts shown:** {len(posts)}",
        "",
    ]

    if not posts:
        lines += ["*No posts found for this hashtag.*"]
        return "\n".join(lines)

    # Auth mode: flat dicts with like_count; anon mode: nested node dicts
    is_auth_format = auth_used or ("shortcode" in (posts[0] if posts else {}))

    lines += ["## Posts", ""]
    if is_auth_format:
        lines += [
            "| # | Author | ✓ | Acct | ❤ Likes | 👁 Views | ♻ | 💬 | Type | 🎵 | 👥 | Link | Caption |",
            "|---|--------|---|------|---------|---------|---|-----|------|----|-----|------|---------|",
        ]
    else:
        lines += [
            "| # | Author | ✓ | 👁 Views | Type | Link | Caption |",
            "|---|--------|---|---------|------|------|---------|",
        ]

    for i, post in enumerate(posts, 1):
        if is_auth_format:
            username  = post.get("username", "")
            verified  = "✓" if post.get("verified") else ""
            acct_icon = _account_type_icon(post.get("account_type", 0))
            code      = post.get("shortcode", "")
            likes     = _fmt(post.get("like_count") or 0) if post.get("like_count") is not None else "—"
            views     = _fmt(post.get("play_count") or 0) if post.get("play_count") else "—"
            reposts   = _fmt(post.get("repost_count") or 0) if post.get("repost_count") else "—"
            comments  = _fmt(post.get("comment_count") or 0) if post.get("comment_count") else "—"
            mtype     = _media_type_label(post)
            music_title = post.get("music_title") or ""
            music_cell  = music_title[:20].replace("|", "\\|") + ("…" if len(music_title) > 20 else "") if music_title else "—"
            tagged    = post.get("tagged_users") or []
            tag_cell  = str(len(tagged)) if tagged else "—"
            cap_raw   = post.get("caption", "") or ""
            caption   = cap_raw[:50].replace("|", "\\|").replace("\n", " ")
            if len(cap_raw) > 50: caption += "…"
            lines.append(
                f"| {i} | @{username} | {verified} | {acct_icon} | {likes} | {views} | {reposts} | {comments} | {mtype} | "
                f"{music_cell} | {tag_cell} | [{code}](https://www.instagram.com/p/{code}/) | {caption} |"
            )
        else:
            node = post.get("node", {}) if "node" in post else post
            user = node.get("user") or {}
            username   = user.get("username", "")
            verified   = "✓" if user.get("is_verified") else ""
            code       = node.get("code", "")
            play_count = node.get("play_count") or node.get("view_count")
            views      = _fmt(int(play_count)) if play_count else "—"
            typename   = node.get("__typename", "")
            mtype      = "🎬" if "Video" in typename else "🖼"
            cap_obj    = node.get("caption") or {}
            cap_raw    = (cap_obj.get("text") or "") if isinstance(cap_obj, dict) else ""
            caption    = cap_raw[:55].replace("|", "\\|").replace("\n", " ")
            if len(cap_raw) > 55: caption += "…"
            lines.append(
                f"| {i} | @{username} | {verified} | {views} | {mtype} | "
                f"[{code}](https://www.instagram.com/p/{code}/) | {caption} |"
            )

    if has_more:
        lines += ["", f"*More posts available — increase `max_posts` to fetch more.*"]

    # Auth mode: show aggregate stats
    if is_auth_format and posts:
        music_count  = sum(1 for p in posts if p.get("music_title"))
        tagged_count = sum(1 for p in posts if p.get("tagged_users"))
        paid_count   = sum(1 for p in posts if p.get("is_paid_partnership"))
        collab_count = sum(1 for p in posts if p.get("coauthors"))
        acct_types   = Counter(p.get("account_type", 0) for p in posts)
        media_types  = Counter(p.get("media_type", 1) for p in posts)
        lines += [
            "",
            "## Summary",
            "",
            f"- 🎬 Videos: {media_types.get(2, 0)}  |  🎠 Carousels: {media_types.get(8, 0)}  |  🖼 Photos: {media_types.get(1, 0)}",
            f"- 👤 Personal: {acct_types.get(1, 0)}  |  🎨 Creator: {acct_types.get(2, 0)}  |  🏢 Business: {acct_types.get(3, 0)}",
            f"- 🎵 With music: {music_count}  |  👥 With tagged users: {tagged_count}  |  🤝 Collabs: {collab_count}  |  💼 Paid partnerships: {paid_count}",
        ]

    if related_searches:
        lines += ["", "## Related Searches", ""]
        for term in related_searches:
            lines.append(f"- {term}")

    lines += ["", "---"]
    if auth_used:
        lines.append("*Data: Instagram /api/v1/tags/sections/ — authenticated session. Columns: ♻=reposts, 🎵=music, 👥=tagged users count.*")
    else:
        lines.append("*Data: public Instagram explore page (logged-out). Like counts unavailable without auth.*")

    return "\n".join(lines)


def _follow_user_row(i: int, u: dict, extra_cols: list = None) -> str:
    """Render one user row for followers/following/likers tables."""
    username  = u.get("username", "")
    full      = (u.get("full_name", "") or "")[:24]
    verified  = "✓" if u.get("is_verified") else ""
    private   = "🔒" if u.get("is_private") else ""
    reel      = "🎬" if u.get("has_recent_reel") else "—"
    you_fw    = u.get("you_follow_them", False)
    they_fw   = u.get("they_follow_you", False)
    if you_fw and they_fw:
        rel = "mutual"
    elif you_fw:
        rel = "you follow"
    elif they_fw:
        rel = "follows you"
    elif u.get("follow_req_sent"):
        rel = "req sent"
    else:
        rel = "—"
    if u.get("is_bestie"):
        rel += " ⭐"

    base = f"| {i} | [@{username}](https://www.instagram.com/{username}/) | {full} | {verified} | {private} | {rel} | {reel} |"
    if extra_cols:
        base += " " + " | ".join(str(c) for c in extra_cols) + " |"
    return base


def format_followers_markdown(
    username: str,
    user_pk: str,
    users: list,
    has_more: bool,
    should_limit: bool,
    pages_fetched: int = 1,
) -> str:
    lines = [
        f"# @{username} — Recent Followers",
        "",
        f"**Users shown:** {len(users)}  |  **Pages fetched:** {pages_fetched}",
    ]
    if has_more and not should_limit:
        lines.append("*More followers available — increase `max_users` to fetch more.*")
    if should_limit:
        lines.append(
            "> ⚠️ Instagram limits follower lists for public accounts — only ~50 recent followers visible. "
            "Full pagination is unavailable for others' accounts."
        )
    lines.append("")

    if not users:
        lines += ["*No followers found.*"]
        return "\n".join(lines)

    lines += [
        "| # | Username | Full Name | ✓ | 🔒 | Relation | 🎬 |",
        "|---|----------|-----------|---|----|-----------|----|",
    ]
    for i, u in enumerate(users, 1):
        lines.append(_follow_user_row(i, u))

    verified_count = sum(1 for u in users if u.get("is_verified"))
    private_count  = sum(1 for u in users if u.get("is_private"))
    reel_count     = sum(1 for u in users if u.get("has_recent_reel"))
    lines += [
        "",
        f"**Verified:** {verified_count}  |  **Private:** {private_count}  |  **Active reels:** {reel_count}",
        "",
        "---",
        "*Data: Instagram /api/v1/friendships/{pk}/followers/ — authenticated session.*",
    ]
    return "\n".join(lines)


def format_following_markdown(
    username: str,
    users: list,
    has_more: bool,
    pages_fetched: int,
) -> str:
    lines = [
        f"# @{username} — Following",
        "",
        f"**Users shown:** {len(users)}  |  **Pages fetched:** {pages_fetched}",
    ]
    if has_more:
        lines.append("*More accounts available — increase `max_users` to fetch more.*")
    lines.append("")

    if not users:
        lines += ["*Not following anyone (or private account).*"]
        return "\n".join(lines)

    lines += [
        "| # | Username | Full Name | ✓ | 🔒 | Relation | 🎬 | ⭐ |",
        "|---|----------|-----------|---|----|-----------|----|-----|",
    ]
    for i, u in enumerate(users, 1):
        fav = "⭐" if u.get("is_favorite") else "—"
        lines.append(_follow_user_row(i, u, extra_cols=[fav]))

    verified_count = sum(1 for u in users if u.get("is_verified"))
    fav_count      = sum(1 for u in users if u.get("is_favorite"))
    reel_count     = sum(1 for u in users if u.get("has_recent_reel"))
    lines += [
        "",
        f"**Verified:** {verified_count}  |  **Favorites:** {fav_count}  |  **Active reels:** {reel_count}",
        "",
        "---",
        "*Data: Instagram /api/v1/friendships/{pk}/following/ — authenticated session.*",
    ]
    return "\n".join(lines)


def format_stories_markdown(
    username: str,
    items: list,
    story_count: int,
    expiring_at: int,
    is_verified: bool = False,
) -> str:
    tick = " ✅" if is_verified else ""
    lines = [f"# 📖 Stories — @{username}{tick}", ""]

    if not story_count:
        expiry_str = ""
        if expiring_at:
            try:
                expiry_str = f" — expires {_dt.fromtimestamp(expiring_at, tz=_tz.utc).strftime('%Y-%m-%d %H:%M')} UTC"
            except Exception:
                pass
        lines.append(f"*No active stories.*")
        return "\n".join(lines)

    expiry_str = ""
    if expiring_at:
        try:
            expiry_str = f" — expires {_dt.fromtimestamp(expiring_at, tz=_tz.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        except Exception:
            pass
    lines.append(f"**{story_count} active stories**{expiry_str}")
    lines.append("")

    lines += [
        "| # | Type | Time | Duration | 🎵 | 📎 | 👥 Mentions | #️⃣ | 🔗 Link | 📊 Poll | Caption |",
        "|---|------|------|----------|----|----|----|---|--------|------|---------|",
    ]
    for i, item in enumerate(items, 1):
        mtype = item.get("media_type", 1)
        type_icon = "🎬" if mtype == 2 else "🖼"
        taken = item.get("taken_at_str", "—")
        dur_secs = item.get("duration_secs") or 0.0
        dur = f"{int(dur_secs)}s" if mtype == 2 else "—"

        music_title = item.get("music_title", "") or ""
        music_cell = (music_title[:18] + ("…" if len(music_title) > 18 else "")) if music_title else "—"

        linked = item.get("linked_post_code", "") or ""
        linked_cell = f"[post](https://www.instagram.com/p/{linked}/)" if linked else "—"

        mentions = item.get("mentions") or []
        mention_cell = " ".join(f"@{m}" for m in mentions[:3]) if mentions else "—"

        hashtags = item.get("hashtags") or []
        hashtag_cell = " ".join(f"#{h}" for h in hashtags[:2]) if hashtags else "—"

        link_stickers = item.get("link_stickers") or []
        if link_stickers:
            ls = link_stickers[0]
            disp = ls.get("display_url", "") or ls.get("url", "")
            link_cell = f"[{disp[:25]}]({ls.get('url', '')})" if disp else "🔗"
        else:
            link_cell = "—"

        polls = item.get("polls") or []
        if polls:
            p = polls[0]
            q = p.get("question", "") or "Poll"
            tallies = p.get("tallies", [])
            total = sum(t.get("count", 0) for t in tallies)
            poll_cell = f"📊{total}" if total else "📊"
        else:
            poll_cell = "—"

        cap = item.get("caption", "") or ""
        if not cap:
            cap = item.get("accessibility_caption", "") or ""
        caption_cell = (cap[:35].replace("|", "\\|") + ("…" if len(cap) > 35 else "")) if cap else "—"

        lines.append(
            f"| {i} | {type_icon} | {taken} | {dur} | {music_cell} | {linked_cell} | {mention_cell} | {hashtag_cell} | {link_cell} | {poll_cell} | {caption_cell} |"
        )

    images = sum(1 for i in items if i.get("media_type") == 1)
    videos = sum(1 for i in items if i.get("media_type") == 2)
    with_music = sum(1 for i in items if i.get("music_title"))
    with_mentions = sum(1 for i in items if i.get("mentions"))
    with_hashtags = sum(1 for i in items if i.get("hashtags"))
    with_links = sum(1 for i in items if i.get("link_stickers"))
    with_polls = sum(1 for i in items if i.get("polls"))
    with_linked = sum(1 for i in items if i.get("linked_post_code"))
    paid = sum(1 for i in items if i.get("is_paid_partnership"))

    lines += [
        "",
        f"**🖼** {images}  |  **🎬** {videos}  |  **🎵** {with_music}  |  "
        f"**👥** {with_mentions}  |  **#️⃣** {with_hashtags}  |  **🔗** {with_links}  |  "
        f"**📊** {with_polls}  |  **📎** {with_linked}  |  **💼** {paid}",
        "",
        "---",
        "*Data: Instagram /api/v1/feed/user/{pk}/story/ — authenticated session. Stories cached 2 min.*",
    ]
    return "\n".join(lines)


def format_highlights_markdown(
    username: str,
    highlights: list,
    highlight_count: int,
    is_verified: bool = False,
) -> str:
    """Format Highlights tray (and optionally media items) as structured Markdown."""
    tick = " ✅" if is_verified else ""
    lines = [f"# 🎭 Highlights — @{username}{tick}", ""]

    lines.append(f"**{highlight_count} highlights**")
    lines.append("")

    if not highlights:
        lines.append("*No highlights found.*")
        return "\n".join(lines)

    # Tray table
    lines += [
        "| # | 📌 | Title | Stories | Created | Updated |",
        "|---|-------|-------|--------:|---------|---------|",
    ]
    for i, h in enumerate(highlights, 1):
        pinned = "📌" if h.get("is_pinned") else ""
        arch = " 🗄" if h.get("is_archived") else ""
        title = f"`{h.get('title', '')}`{arch}"
        media_count = h.get("media_count", 0)
        created = h.get("created_at_str", "")
        latest = h.get("latest_reel_media", 0)
        try:
            updated = _dt.fromtimestamp(latest, tz=_tz.utc).strftime("%Y-%m-%d") if latest else "—"
        except Exception:
            updated = "—"
        lines.append(f"| {i} | {pinned} | {title} | {media_count} | {created} | {updated} |")

    # Per-highlight media sub-sections (if items were fetched)
    highlights_with_items = [h for h in highlights if h.get("items")]
    if highlights_with_items:
        for h in highlights_with_items:
            items = h.get("items") or []
            lines.append("")
            lines.append(f"### \"{h.get('title', '')}\" — {h.get('media_count', 0)} stories")
            lines.append("")
            lines += [
                "| # | Type | Time | Duration | Capture | Cam | 👥 | 🔗 | 📊 |",
                "|---|------|------|----------|---------|-----|----|----|----|",
            ]
            for j, item in enumerate(items, 1):
                mtype = item.get("media_type", 1)
                type_icon = "🎬" if mtype == 2 else "🖼"
                taken = item.get("taken_at_str", "—")
                dur_secs = item.get("duration_secs") or 0.0
                dur = f"{int(dur_secs)}s" if mtype == 2 else "—"

                capture = item.get("capture_type", "") or "—"
                cam = item.get("camera_facing", "") or "—"

                mentions = item.get("mentions") or []
                mention_cell = " ".join(f"@{m}" for m in mentions[:3]) if mentions else "—"

                link_stickers = item.get("link_stickers") or []
                if link_stickers:
                    ls = link_stickers[0]
                    disp = ls.get("display_url", "") or ls.get("url", "")
                    link_cell = f"[{disp[:20]}]({ls.get('url', '')})" if disp else "🔗"
                else:
                    link_cell = "—"

                polls = item.get("polls") or []
                if polls:
                    p = polls[0]
                    tallies = p.get("tallies", [])
                    total = sum(t.get("count", 0) for t in tallies)
                    poll_cell = f"📊{total}" if total else "📊"
                else:
                    poll_cell = "—"

                lines.append(
                    f"| {j} | {type_icon} | {taken} | {dur} | {capture} | {cam} | {mention_cell} | {link_cell} | {poll_cell} |"
                )

    # Summary stats
    lines.append("")
    total_stories = sum(h.get("media_count", 0) for h in highlights)
    lines.append(f"Total stories across all highlights: {total_stories}")

    if highlights_with_items:
        all_items = [item for h in highlights_with_items for item in (h.get("items") or [])]
        if all_items:
            images = sum(1 for i in all_items if i.get("media_type") == 1)
            videos = sum(1 for i in all_items if i.get("media_type") == 2)
            boomerangs = sum(1 for i in all_items if i.get("capture_type") == "boomerang")
            selfies = sum(1 for i in all_items if i.get("camera_facing") == "front")
            with_mentions = sum(1 for i in all_items if i.get("mentions"))
            with_links = sum(1 for i in all_items if i.get("link_stickers"))
            lines.append(
                f"**🖼** {images}  |  **🎬** {videos}  |  **🔄 Boomerang:** {boomerangs}  |  "
                f"**🤳 Selfie:** {selfies}  |  **👥** {with_mentions}  |  **🔗** {with_links}"
            )

    return "\n".join(lines)


def format_post_likers_markdown(
    shortcode: str,
    users: list,
    user_count: int,
) -> str:
    _fmt = format_followers
    lines = [
        f"# Post Likers — [{shortcode}](https://www.instagram.com/p/{shortcode}/)",
        "",
        f"**Total likes:** {_fmt(user_count)}  |  **Shown here:** {len(users)}",
        "",
    ]

    if not users:
        lines += ["*No likers found or post is private.*"]
        return "\n".join(lines)

    lines += [
        "| # | Username | Full Name | ✓ | 🔒 | Relation | 🎬 |",
        "|---|----------|-----------|---|----|-----------|----|",
    ]
    for i, u in enumerate(users, 1):
        lines.append(_follow_user_row(i, u))

    verified_count  = sum(1 for u in users if u.get("is_verified"))
    you_follow      = sum(1 for u in users if u.get("you_follow_them"))
    they_follow     = sum(1 for u in users if u.get("they_follow_you"))
    lines += [
        "",
        f"**Verified likers:** {verified_count}  |  **You follow:** {you_follow}  |  **Follow you:** {they_follow}",
        "",
        "---",
        "*Data: Instagram /api/v1/media/{id}/likers/ — authenticated session. ~98 likers returned (Instagram API limit).*",
    ]
    return "\n".join(lines)


def format_location_posts_markdown(
    location_id: str,
    location_name: str,
    posts: list,
    post_count: int,
    more_available: bool,
) -> str:
    """Format location top-posts result as readable Markdown."""
    _fmt = format_followers

    display_name = location_name or location_id
    lines = [
        f"# \U0001f4cd Location Posts — {display_name}",
        "",
        f"**Location ID:** `{location_id}`  |  **Posts shown:** {post_count}",
        "",
    ]

    if not posts:
        lines.append("*No posts found for this location.*")
        return "\n".join(lines)

    lines += [
        "| # | Type | User | ✔ | ❤ Likes | \U0001f4ac Comments | \U0001f441 Plays | Caption |",
        "|---|------|------|---|---------|----------|-------|---------|",
    ]
    for i, post in enumerate(posts, 1):
        mtype = post.get("media_type", 1)
        if mtype == 2:
            type_icon = "\U0001f3ac"
        elif mtype == 8:
            type_icon = "\U0001f3a0"
        else:
            type_icon = "\U0001f5bc"
        username   = post.get("username", "")
        verified   = "✔" if post.get("is_verified") else ""
        likes      = _fmt(post.get("like_count") or 0)
        comments   = _fmt(post.get("comment_count") or 0)
        plays      = _fmt(post.get("play_count") or 0) if post.get("play_count") else "—"
        code       = post.get("shortcode", "")
        cap_raw    = post.get("caption", "") or ""
        caption    = cap_raw[:55].replace("|", "\\|").replace("\n", " ")
        if len(cap_raw) > 55:
            caption += "…"
        lines.append(
            f"| {i} | {type_icon} | [@{username}](https://www.instagram.com/{username}/) "
            f"| {verified} | {likes} | {comments} | {plays} "
            f"| [{caption}](https://www.instagram.com/p/{code}/) |"
        )

    if more_available:
        lines += ["", "*More posts available — increase `max_posts` to fetch more.*"]

    # Summary stats
    if posts:
        videos    = sum(1 for p in posts if p.get("media_type") == 2)
        carousels = sum(1 for p in posts if p.get("media_type") == 8)
        images    = sum(1 for p in posts if p.get("media_type") == 1)
        verified_count = sum(1 for p in posts if p.get("is_verified"))
        lines += [
            "",
            "## Summary",
            "",
            f"- \U0001f3ac Videos: {videos}  |  \U0001f3a0 Carousels: {carousels}  |  \U0001f5bc Photos: {images}",
            f"- ✔ Verified creators: {verified_count}",
        ]

    return "\n".join(lines)


def format_audio_reels_markdown(
    audio_cluster_id: str,
    music_title: str,
    music_artist: str,
    posts: list,
    total_reels_str: str,
    more_available: bool,
) -> str:
    """Format audio reels result as readable Markdown."""
    _fmt = format_followers

    title_display  = music_title or audio_cluster_id
    artist_display = music_artist or "Unknown"
    lines = [
        f"# \U0001f3b5 Audio Reels — {title_display} by {artist_display}",
        "",
    ]
    if total_reels_str:
        lines.append(f"**Total reels using this audio:** {total_reels_str}  |  **Shown here:** {len(posts)}")
    else:
        lines.append(f"**Reels shown:** {len(posts)}")
    lines.append(f"**Audio cluster ID:** `{audio_cluster_id}`")
    lines.append("")

    if not posts:
        lines.append("*No reels found for this audio.*")
        return "\n".join(lines)

    lines += [
        "| # | User | ✔ | ❤ Likes | \U0001f441 Plays | Date | Caption |",
        "|---|------|---|---------|-------|------|---------|",
    ]
    for i, post in enumerate(posts, 1):
        username  = post.get("username", "")
        verified  = "✔" if post.get("is_verified") else ""
        likes     = _fmt(post.get("like_count") or 0)
        plays     = _fmt(post.get("play_count") or 0) if post.get("play_count") else "—"
        date      = (post.get("taken_at_str", "") or "")[:10]
        code      = post.get("shortcode", "")
        cap_raw   = post.get("caption", "") or ""
        caption   = cap_raw[:55].replace("|", "\\|").replace("\n", " ")
        if len(cap_raw) > 55:
            caption += "…"
        lines.append(
            f"| {i} | [@{username}](https://www.instagram.com/{username}/) "
            f"| {verified} | {likes} | {plays} | {date} "
            f"| [{caption}](https://www.instagram.com/reel/{code}/) |"
        )

    if more_available:
        lines += ["", "*More reels available — increase `max_reels` to fetch more.*"]

    # Summary stats
    if posts:
        verified_count = sum(1 for p in posts if p.get("is_verified"))
        total_likes    = sum(p.get("like_count") or 0 for p in posts)
        total_plays    = sum(p.get("play_count") or 0 for p in posts)
        lines += [
            "",
            "## Summary",
            "",
            f"- ✔ Verified creators: {verified_count}",
            f"- ❤ Total likes: {_fmt(total_likes)}  |  \U0001f441 Total plays: {_fmt(total_plays)}",
        ]

    return "\n".join(lines)
