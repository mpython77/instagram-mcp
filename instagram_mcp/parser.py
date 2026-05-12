"""
Instagram data parsing — raw JSON → structured data.

All parsing logic is here:
  - parse_profile: user JSON → InstagramProfile
  - parse_feed_tags: edges → FeedTagResult (with pinned detection)
  - check_dead_account: logic for dead account
  - filter_bio_links: filters social/aggregator from bio links
  - detect_pinned_posts: detects old (pinned) posts at the start of feed
"""

from __future__ import annotations

import re
import time
from datetime import datetime as _dt
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import MCPConfig
from .models import CommentItem, DateRange, FeedTagResult, InstagramPost, InstagramProfile, PostInfo, PostLocation, ReelItem, RepostItem, TaggedPost

# Pre-compiled patterns — avoid recompilation on every post
_MENTION_RE = re.compile(r"@([a-zA-Z0-9_.]+)")
_HASHTAG_RE = re.compile(r"#([a-zA-Z0-9_]+)")


def _get_taken_at(node: dict) -> int:
    """Extract post timestamp — handles both old (taken_at_timestamp) and new (taken_at) API."""
    ts = node.get("taken_at_timestamp") or node.get("taken_at", 0)
    if not ts:
        cap = node.get("caption")
        if isinstance(cap, dict):
            ts = cap.get("created_at", 0)
    return int(ts or 0)


def _get_shortcode(node: dict) -> str:
    """Extract post shortcode — old API uses 'shortcode', new API uses 'code'."""
    return node.get("shortcode", "") or node.get("code", "")


def _get_likes(node: dict) -> int:
    """Extract like count — old: edge_media_preview_like.count, new: like_count."""
    old = node.get("edge_media_preview_like") or {}
    if isinstance(old, dict) and old.get("count"):
        return int(old["count"])
    return int(node.get("like_count") or 0)


def _get_comments(node: dict) -> int:
    """Extract comment count — old: edge_media_to_comment.count, new: comment_count."""
    old = node.get("edge_media_to_comment") or {}
    if isinstance(old, dict) and old.get("count"):
        return int(old["count"])
    return int(node.get("comment_count") or 0)


def _get_video_views(node: dict) -> int:
    """Extract video view count — handles video_view_count, play_count, view_count."""
    return int(
        node.get("video_view_count")
        or node.get("play_count")
        or node.get("view_count")
        or 0
    )


def _get_caption_text(node: dict) -> str:
    """
    Extract caption text — handles both API formats.
    Old: edge_media_to_caption.edges[0].node.text
    New: caption.text (dict) or caption (string)
    """
    cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
    if cap_edges:
        return (cap_edges[0].get("node") or {}).get("text", "")
    cap = node.get("caption")
    if isinstance(cap, dict):
        return cap.get("text", "")
    if isinstance(cap, str):
        return cap
    return ""


def _get_post_type_and_carousel(node: dict) -> Tuple[str, int]:
    """
    Determine post type and carousel count — handles both API formats.
    Old: __typename (GraphSidecar/GraphVideo), product_type
    New: media_type (1=image, 2=video, 8=carousel), product_type
    """
    typename = node.get("__typename", "")
    product_type = node.get("product_type", "") or ""
    media_type = int(node.get("media_type") or 0)  # new API: 1=image, 2=video, 8=carousel

    if typename == "GraphSidecar" or media_type == 8:
        count = (
            len((node.get("edge_sidecar_to_children") or {}).get("edges") or [])
            or int(node.get("carousel_media_count") or 0)
        )
        return "carousel", count

    if product_type in ("clips", "reel"):
        return "reel", 0
    if product_type == "igtv":
        return "igtv", 0
    if typename == "GraphVideo" or node.get("is_video") or media_type == 2:
        return "video", 0
    return "image", 0


def _get_is_pinned(node: dict) -> bool:
    """Check pinned status — works with both API formats.
    Feed: timeline_pinned_user_ids (non-empty list = pinned)
    Web:  pinned_for_users (non-empty list = pinned)
    """
    return bool(node.get("timeline_pinned_user_ids") or node.get("pinned_for_users"))


def _get_usertags(node: dict) -> List[str]:
    """
    Extract usertags — handles both API formats + fb_user_tags.
    Old: edge_media_to_tagged_user.edges[*].node.user.username
    New: usertags.in[*].user.username  OR  fb_user_tags.in[*].user.username
    """
    old_edges = (node.get("edge_media_to_tagged_user") or {}).get("edges") or []
    if old_edges:
        return [
            (te.get("node") or {}).get("user", {}).get("username", "").lower()
            for te in old_edges
            if (te.get("node") or {}).get("user", {}).get("username", "")
        ]
    # New format — check both usertags and fb_user_tags
    seen: Set[str] = set()
    result = []
    for key in ("usertags", "fb_user_tags"):
        for u in (node.get(key) or {}).get("in") or []:
            username = (u.get("user") or {}).get("username", "").lower()
            if username and username not in seen:
                seen.add(username)
                result.append(username)
    return result


def _get_display_url(node: dict) -> str:
    """
    Extract display/thumbnail URL — handles both API formats.
    Old: display_url
    New: image_versions2.candidates[0].url  OR  display_uri
    """
    url = node.get("display_url", "")
    if not url:
        iv2 = node.get("image_versions2") or {}
        candidates = iv2.get("candidates") or []
        if candidates:
            url = candidates[0].get("url", "")
    if not url:
        url = node.get("display_uri", "")
    return url


def _get_dimensions(node: dict) -> dict:
    """Extract dimensions — falls back to original_width/height in new API."""
    dims = node.get("dimensions") or {}
    if not dims:
        w = node.get("original_width") or node.get("width", 0)
        h = node.get("original_height") or node.get("height", 0)
        if w or h:
            return {"width": int(w or 0), "height": int(h or 0)}
    return dims


def _extract_location(node: dict) -> Optional[dict]:
    """Extract location from a post node, normalizing all API shapes."""
    try:
        loc = node.get("location") or node.get("location_info") or {}
        if not loc:
            return None

        result = {}

        # Name (required to be useful)
        name = loc.get("name") or loc.get("location_name", "")
        if not name:
            return None
        result["name"] = name

        # ID
        loc_id = loc.get("pk") or loc.get("id") or loc.get("location_id")
        if loc_id:
            result["id"] = str(loc_id)

        # Coordinates (priority: lat/lng direct, then address_json)
        lat = loc.get("lat") or loc.get("latitude")
        lng = loc.get("lng") or loc.get("longitude")
        if lat is not None and lng is not None:
            try:
                result["lat"] = float(lat)
                result["lng"] = float(lng)
            except (TypeError, ValueError):
                pass

        # Slug
        slug = loc.get("slug") or loc.get("location_slug")
        if slug:
            result["slug"] = slug

        return result if result.get("name") else None
    except Exception:
        return None


def _extract_music(node: dict) -> Tuple[str, str]:
    """Extract music artist and title from Reels/video metadata."""
    music_meta = node.get("music_metadata") or {}
    if not music_meta:
        clips_meta = node.get("clips_metadata") or {}
        music_meta = clips_meta.get("music_info") or {}
    if not music_meta:
        return "", ""
    mi = music_meta.get("music_info") or music_meta
    artist = str(mi.get("artist_name") or mi.get("artist") or "")
    title = str(mi.get("song_name") or mi.get("title") or "")
    return artist, title


def filter_bio_links(links: Any, social_domains: Set[str]) -> str:
    """
    Filter social/aggregator domains from bio links.
    Returns only personal website.
    """
    filtered = []
    for link_obj in (links or []):
        url = link_obj.get("url", "") if isinstance(link_obj, dict) else str(link_obj)
        if not url:
            continue
        domain = url.lower().replace("https://", "").replace("http://", "").split("/")[0]
        if not any(sd in domain for sd in social_domains):
            filtered.append(url)

    if not filtered:
        return ""

    # Prioritize main domain extensions
    for url in filtered:
        if any(kw in url.lower() for kw in [".com", ".co", ".shop", ".store", ".io"]):
            return url
    return filtered[0]


def detect_pinned_posts(
    items: List[Dict], now: float, max_age: float
) -> int:
    """
    Detect the number of old (pinned) posts at the top of the feed.

    Logic: if the first N posts are older than max_age, but the
    next one is newer, the first ones are pinned.
    """
    if len(items) < 2:
        return 0

    # Check first 10 posts (max 3 pinned + buffer) to detect pinned posts
    ages = [now - item.get("taken_at", 0) for item in items[:min(10, len(items))]]

    for pinned in range(1, min(4, len(ages))):
        if (
            all(a > max_age for a in ages[:pinned])
            and pinned < len(ages)
            and ages[pinned] <= max_age
        ):
            return pinned
    return 0


def check_dead_account(
    user: Dict, dead_threshold_days: int = 365
) -> Tuple[bool, int]:
    """
    Check if account is dead or active.

    Returns:
        Tuple[bool, int]: (is_dead, last_post_days)
    """
    media = user.get("edge_owner_to_timeline_media", {})
    edges = media.get("edges", [])

    if not edges:
        posts_count = media.get("count", 0)
        return (True, 9999) if posts_count > 0 else (False, 0)

    now = time.time()
    newest_days = float("inf")

    for edge in edges:
        taken_at = _get_taken_at(edge.get("node") or {})
        if taken_at > 0:
            days = (now - taken_at) / 86400
            newest_days = min(newest_days, days)

    if newest_days == float("inf"):
        return True, 9999

    return newest_days > dead_threshold_days, int(newest_days)


def parse_profile(user: Dict, username: str, config: MCPConfig) -> InstagramProfile:
    """Instagram user JSON → InstagramProfile."""
    # Always produce a usable profile; fall back to argument when payload omits username
    resolved_username = (user.get("username") or username or "").strip()
    if not resolved_username:
        # Defensive default — caller may then check `.user_id` or `.username == ""`
        resolved_username = "unknown"

    return InstagramProfile(
        user_id=str(user.get("id", "")),
        username=resolved_username,
        full_name=user.get("full_name", "") or "",
        biography=user.get("biography", "") or "",
        followers=user.get("edge_followed_by", {}).get("count", 0),
        following=user.get("edge_follow", {}).get("count", 0),
        posts_count=user.get("edge_owner_to_timeline_media", {}).get("count", 0),
        category=user.get("category_name", "") or "",
        website=filter_bio_links(
            user.get("bio_links", []), config.social_domains
        ),
        external_url=user.get("external_url", "") or "",
        is_private=bool(user.get("is_private", False)),
        is_verified=bool(user.get("is_verified", False)),
        is_business=bool(user.get("is_business_account", False)),
        profile_pic_url=(
            user.get("profile_pic_url_hd", "") or user.get("profile_pic_url", "") or ""
        ),
        # Extended
        highlight_count=int(user.get("highlight_reel_count") or 0),
        pronouns=list(user.get("pronouns") or []),
        is_professional=bool(user.get("is_professional_account", False)),
        account_type=int(user.get("account_type") or 0),
        has_reels=bool(user.get("has_clips", False)),
        has_guides=bool(user.get("has_guides", False)),
        contact_phone=user.get("contact_phone_number", "") or "",
        public_email=user.get("public_email", "") or "",
        city=user.get("city_name", "") or "",
        usertags_count=int(user.get("usertags_count") or 0),
        is_new_account=bool(user.get("is_joined_recently", False)),
        overall_category=user.get("overall_category_name", "") or "",
    )


def parse_feed_tags(
    user: Dict,
    max_posts: int = 12,
    max_age_days: int = 4,
    date_range: Optional[DateRange] = None,
) -> FeedTagResult:
    """
    edge_owner_to_timeline_media.edges → FeedTagResult.

    - Automatically skips pinned posts
    - Stops at posts older than max_age_days
    - Usertag + @mention extraction for each post
    - Optionally filters by date_range (Unix timestamp bounds)
    """
    edges = user.get("edge_owner_to_timeline_media", {}).get("edges", [])
    if not edges:
        return FeedTagResult()

    return parse_feed_tags_from_edges(
        edges=edges,
        max_posts=max_posts,
        max_age_days=max_age_days,
        detect_pinned=True,
        date_range=date_range,
    )


def extract_page_info(user: Dict) -> Dict[str, Any]:
    """
    Extract pagination info from web_profile_info response.

    Returns:
        {"end_cursor": str, "has_next_page": bool, "first_page_edges": list}
    """
    media = user.get("edge_owner_to_timeline_media", {})
    page_info = media.get("page_info", {})
    return {
        "end_cursor": page_info.get("end_cursor", ""),
        "has_next_page": page_info.get("has_next_page", False),
        "first_page_edges": media.get("edges", []),
    }


def parse_feed_tags_from_edges(
    edges: List[Dict],
    max_posts: int = 50,
    max_age_days: int = 30,
    detect_pinned: bool = False,
    pages_fetched: int = 1,
    has_more_posts: bool = False,
    date_range: Optional[DateRange] = None,
) -> FeedTagResult:
    """
    Unified edge parser — works with edges from any source.

    Accepts a flat list of edge dicts from:
    - web_profile_info (first 12 posts)
    - GraphQL pagination (additional pages)
    - Combined list from both sources

    Args:
        edges: List of edge dicts with "node" inside
        max_posts: Maximum posts to process
        max_age_days: Skip posts older than this
        detect_pinned: Whether to detect and skip pinned posts at start
        pages_fetched: Number of API pages that were fetched
        has_more_posts: Whether more posts are available
        date_range: Optional DateRange for timestamp-based filtering

    Returns:
        FeedTagResult with extracted tags, mentions, and post data
    """
    result = FeedTagResult(
        pages_fetched=pages_fetched,
        has_more_posts=has_more_posts,
    )

    if not edges:
        return result

    now = time.time()
    max_age_seconds = max_age_days * 86400

    # Pinned detection (only for first-page data)
    pinned_count = 0
    if detect_pinned:
        items_for_pinned = [
            {"taken_at": _get_taken_at(e.get("node") or {})}
            for e in edges[:min(10, len(edges))]
        ]
        pinned_count = detect_pinned_posts(items_for_pinned, now, max_age_seconds)
        # When date_range is set, old pinned posts appear at top of feed
        # and confuse age-based stop — assume at least 3 pinned in that case
        if date_range and date_range.since:
            pinned_count = max(3, pinned_count)

    tags_set: Set[str] = set()
    seen_codes: Set[str] = set()

    for idx, edge in enumerate(edges):
        node = edge.get("node") or {}

        taken_at = _get_taken_at(node)
        code = _get_shortcode(node)

        # Deduplicate — first page edges sometimes overlap with paginated edges
        if code and code in seen_codes:
            continue

        age_seconds = now - taken_at if taken_at else 0
        age_days = age_seconds / 86400 if taken_at else 0

        if detect_pinned and idx < pinned_count:
            continue

        # Age-based stop — disabled when date_range is set
        # (date_range has its own smart-stop logic in the client)
        if not date_range and taken_at and age_seconds > max_age_seconds:
            break

        # Date-range filtering — skip posts outside the timestamp window but keep paginating
        if date_range and taken_at and not date_range.contains(taken_at):
            continue

        result.posts_checked += 1

        # Post type + carousel count — dual-format aware
        post_type, carousel_count = _get_post_type_and_carousel(node)
        product_type = node.get("product_type", "") or ""

        # Usertags — dual-format aware
        usertags_list = _get_usertags(node)

        # Caption + @mentions + hashtags — dual-format aware
        caption_text = _get_caption_text(node)
        mentions_list = [m.lower() for m in _MENTION_RE.findall(caption_text)]
        hashtags_list = [h.lower() for h in _HASHTAG_RE.findall(caption_text)]

        # Collab co-authors
        coauthors_list = [
            p.get("username", "").lower()
            for p in (node.get("coauthor_producers") or [])
            if p.get("username")
        ]

        # Paid partnership / sponsor tags
        sponsor_tags_list = [
            (e.get("node") or {}).get("sponsor", {}).get("username", "").lower()
            for e in (node.get("edge_media_to_sponsor_user") or {}).get("edges", [])
            if (e.get("node") or {}).get("sponsor", {}).get("username")
        ]

        # Dimensions — dual-format aware
        dims = _get_dimensions(node)

        # Music (Reels)
        music_artist, music_title = _extract_music(node)

        # Timestamp string — guard against invalid values
        ts_str = ""
        if taken_at:
            try:
                ts_str = _dt.fromtimestamp(taken_at).strftime("%Y-%m-%d %H:%M")
            except (OSError, OverflowError, ValueError):
                ts_str = ""

        post_tags_set = set(usertags_list) | set(mentions_list)

        post_info = InstagramPost(
            shortcode=code,
            post_url=f"https://www.instagram.com/p/{code}/" if code else "",
            post_type=post_type,
            taken_at=taken_at,
            taken_at_str=ts_str,
            age_days=round(age_days, 1),
            display_url=_get_display_url(node),
            thumbnail_url=node.get("thumbnail_src", "") or "",
            is_video=bool(node.get("is_video") or post_type in ("video", "reel", "igtv")),
            likes=_get_likes(node),
            comments=_get_comments(node),
            video_view_count=_get_video_views(node),
            caption=caption_text,
            accessibility_caption=node.get("accessibility_caption", "") or "",
            product_type=product_type,
            usertags=usertags_list,
            mentions=mentions_list,
            coauthors=coauthors_list,
            sponsor_tags=sponsor_tags_list,
            carousel_count=carousel_count,
            width=int(dims.get("width") or 0),
            height=int(dims.get("height") or 0),
            music_artist=music_artist,
            music_title=music_title,
            location=_extract_location(node),
            hashtags=hashtags_list,
            is_pinned=_get_is_pinned(node),
        )
        result.posts.append(post_info)
        if code:
            seen_codes.add(code)
        if result.posts_checked >= max_posts:
            break

        if post_tags_set:
            result.posts_with_tags += 1
            for tag in post_tags_set:
                tags_set.add(tag)
                if tag not in result.tag_shortcodes:
                    result.tag_shortcodes[tag] = code
                if tag not in result.tag_timestamps:
                    result.tag_timestamps[tag] = ts_str

    result.tags = sorted(tags_set)
    return result


def parse_feed_items(
    items: List[Dict],
    max_posts: int = 12,
    max_age_days: int = 4,
    since_timestamp: Optional[int] = None,
    until_timestamp: Optional[int] = None,
) -> FeedTagResult:
    """
    Parse v1/feed/user items into FeedTagResult.

    Uses native feed item format with direct pinned detection via
    timeline_pinned_user_ids — no timestamp heuristic needed.
    """
    result = FeedTagResult()
    now = time.time()
    max_age_seconds = float(max_age_days) * 86400.0
    process_limit = len(items) if since_timestamp else max_posts
    seen_codes: Set[str] = set()
    tags_set: Set[str] = set()

    for item in items[:process_limit]:
        code = _get_shortcode(item)
        if not code or code in seen_codes:
            continue

        taken_at = _get_taken_at(item)
        if not taken_at:
            continue

        is_pinned = _get_is_pinned(item)

        # Date range filtering
        in_range = True
        if until_timestamp and taken_at > until_timestamp:
            in_range = False
        if since_timestamp and taken_at < since_timestamp:
            in_range = False

        if not in_range:
            if not is_pinned:
                continue

        seen_codes.add(code)
        age_sec = now - float(taken_at)

        if not since_timestamp and not is_pinned and age_sec > max_age_seconds:
            break

        result.posts_checked += 1

        post_type, carousel_count = _get_post_type_and_carousel(item)
        product_type = item.get("product_type", "") or ""

        usertags_list = _get_usertags(item)

        caption_text = _get_caption_text(item)
        mentions_list = [m.lower() for m in _MENTION_RE.findall(caption_text)]
        hashtags_list = [h.lower() for h in _HASHTAG_RE.findall(caption_text)]

        coauthors_list = [
            p.get("username", "").lower()
            for p in (item.get("coauthor_producers") or [])
            if p.get("username")
        ]

        sponsor_tags_list = [
            (e.get("node") or {}).get("sponsor", {}).get("username", "").lower()
            for e in (item.get("edge_media_to_sponsor_user") or {}).get("edges", [])
            if (e.get("node") or {}).get("sponsor", {}).get("username")
        ]

        dims = _get_dimensions(item)
        music_artist, music_title = _extract_music(item)

        ts_str = ""
        if taken_at:
            try:
                ts_str = _dt.fromtimestamp(taken_at).strftime("%Y-%m-%d %H:%M")
            except (OSError, OverflowError, ValueError):
                ts_str = ""

        age_days = age_sec / 86400.0 if taken_at else 0
        post_tags_set = set(usertags_list) | set(mentions_list)

        post_info = InstagramPost(
            shortcode=code,
            post_url=f"https://www.instagram.com/p/{code}/" if code else "",
            post_type=post_type,
            taken_at=taken_at,
            taken_at_str=ts_str,
            age_days=round(age_days, 1),
            display_url=_get_display_url(item),
            thumbnail_url=item.get("thumbnail_src", "") or "",
            is_video=bool(item.get("is_video") or post_type in ("video", "reel", "igtv")),
            likes=_get_likes(item),
            comments=_get_comments(item),
            video_view_count=_get_video_views(item),
            caption=caption_text,
            accessibility_caption=item.get("accessibility_caption", "") or "",
            product_type=product_type,
            usertags=usertags_list,
            mentions=mentions_list,
            coauthors=coauthors_list,
            sponsor_tags=sponsor_tags_list,
            carousel_count=carousel_count,
            width=int(dims.get("width") or 0),
            height=int(dims.get("height") or 0),
            music_artist=music_artist,
            music_title=music_title,
            location=_extract_location(item),
            hashtags=hashtags_list,
            is_pinned=is_pinned,
        )
        result.posts.append(post_info)

        if post_tags_set:
            result.posts_with_tags += 1
            for tag in post_tags_set:
                tags_set.add(tag)
                if tag not in result.tag_shortcodes:
                    result.tag_shortcodes[tag] = code
                if tag not in result.tag_timestamps:
                    result.tag_timestamps[tag] = ts_str

        if result.posts_checked >= max_posts:
            break

    result.tags = sorted(tags_set)
    return result


def check_dead_account_from_items(
    items: List[Dict], posts_count: int, dead_threshold_days: int = 365
) -> Tuple[bool, int]:
    """
    Check if account is dead using v1/feed/user items.

    Returns:
        Tuple[bool, int]: (is_dead, last_post_days)
    """
    if not items:
        return (posts_count > 0, 9999)

    now = time.time()
    times = [it.get("taken_at", 0) for it in items if it.get("taken_at", 0) > 0]
    if not times:
        return True, 9999

    newest_days = int(min((now - t) / 86400 for t in times))
    return newest_days > dead_threshold_days, newest_days


# ── Tagged Tab parser ────────────────────────────────────────────────────────

# Instagram media_type → human label
_MEDIA_TYPE_LABEL: Dict[int, str] = {1: "image", 2: "video", 8: "carousel"}

# Instagram IDs encode timestamps. Formula (approximate):
# Instagram pk encoding (all modern pks):
#   pk = (taken_at_ms - _IG_EPOCH_MS) << 23 | device_id << 10 | sequence
#   taken_at_ms = milliseconds since _IG_EPOCH_MS (Aug 25, 2011)
#
# To decode:   taken_at_s = ((pk >> 23) + _IG_EPOCH_MS) // 1000
#
# NOTE: The shift extracts *milliseconds*, not seconds — the epoch offset
# must be in milliseconds, and the final value divided by 1000 for Unix seconds.
_IG_EPOCH_SHIFT = 23
_IG_EPOCH_MS = 1_314_220_021_000   # 2011-08-25 00:00:00 UTC in milliseconds

# Sanity bounds: Instagram launched Oct 2010; 2100 = far future ceiling
_IG_TS_MIN = 1_286_000_000   # ~ Oct 2010
_IG_TS_MAX = 4_102_444_800   # ~ Jan 2100


def _pk_to_timestamp(pk: str) -> int:
    """Estimate Unix timestamp (seconds) from an Instagram media pk (best-effort)."""
    try:
        ts = ((int(pk) >> _IG_EPOCH_SHIFT) + _IG_EPOCH_MS) // 1000
        return ts if _IG_TS_MIN <= ts <= _IG_TS_MAX else 0
    except (ValueError, TypeError):
        return 0


def parse_tagged_tab_edges(
    edges: List[Dict[str, Any]],
    max_posts: int = 50,
) -> List[TaggedPost]:
    """
    Parse edges from PolarisProfileTaggedTabContentQuery_connection response
    into a list of TaggedPost objects.

    Each edge node represents a post made by SOMEONE ELSE that tags the
    queried account. The `user` field is the poster (not the tagged account).
    """
    result: List[TaggedPost] = []

    for edge in edges:
        if len(result) >= max_posts:
            break

        if not isinstance(edge, dict):
            continue
        node = edge.get("node") or edge  # edges may already be unwrapped
        if not isinstance(node, dict):
            continue

        code = node.get("code") or ""
        pk = str(node.get("pk") or "")
        media_type = int(node.get("media_type") or 0)

        user = node.get("user") or {}
        poster_username = str(user.get("username") or "")
        poster_id = str(user.get("pk") or user.get("id") or "")

        taken_at = _pk_to_timestamp(pk)
        taken_at_str = (
            _dt.utcfromtimestamp(taken_at).strftime("%Y-%m-%d %H:%M")
            if taken_at
            else ""
        )

        # Best display URL: first candidate of first carousel item or top-level
        display_url = ""
        candidates: List[Dict] = []
        iv2 = node.get("image_versions2") or {}
        if iv2:
            candidates = iv2.get("candidates") or []
        elif node.get("carousel_media"):
            first_item = (node["carousel_media"] or [{}])[0]
            candidates = (first_item.get("image_versions2") or {}).get("candidates") or []
        if candidates:
            display_url = str(candidates[0].get("url") or "")
            width = int(candidates[0].get("width") or 0)
            height = int(candidates[0].get("height") or 0)
        else:
            width = int(node.get("original_width") or 0)
            height = int(node.get("original_height") or 0)

        caption_obj = node.get("caption") or {}
        caption = str(caption_obj.get("text") or "") if isinstance(caption_obj, dict) else ""

        post = TaggedPost(
            shortcode=code,
            post_url=f"https://www.instagram.com/p/{code}/" if code else "",
            media_type=media_type,
            post_type=_MEDIA_TYPE_LABEL.get(media_type, "unknown"),
            poster_username=poster_username,
            poster_id=poster_id,
            likes=int(node.get("like_count") or 0),
            comments=int(node.get("comment_count") or 0),
            view_count=int(node.get("view_count") or 0),
            carousel_count=int(node.get("carousel_media_count") or 0),
            caption=caption[:500],  # cap length for safety
            display_url=display_url,
            width=width,
            height=height,
            taken_at=taken_at,
            taken_at_str=taken_at_str,
        )
        result.append(post)

    return result


def parse_repost_items(
    items: List[Dict[str, Any]],
    max_posts: int = 50,
) -> List[RepostItem]:
    """
    Parse repost_grid_items from PolarisProfileRepostsTabContentRefetchQuery response.

    Each item is  {media: {...}}  where `media.user` is the ORIGINAL POSTER —
    i.e. the account whose content was reposted, NOT the account that reposted.

    Notable structural differences vs Tagged Tab edges:
    - Items are NOT wrapped in {node: ...} — they are {media: ...} directly
    - Pagination uses max_id (not GraphQL cursor)
    - No taken_at field → estimated from pk via bit-shift
    """
    result: List[RepostItem] = []

    for item in items:
        if len(result) >= max_posts:
            break

        media = item.get("media") or {}
        if not isinstance(media, dict):
            continue

        code = str(media.get("code") or "")
        pk = str(media.get("pk") or "")
        media_type = int(media.get("media_type") or 0)
        product_type = str(media.get("product_type") or "")

        user = media.get("user") or {}
        orig_username = str(user.get("username") or "")
        orig_user_id = str(user.get("pk") or user.get("id") or "")

        taken_at = _pk_to_timestamp(pk)
        taken_at_str = (
            _dt.utcfromtimestamp(taken_at).strftime("%Y-%m-%d")
            if taken_at
            else ""
        )

        # Display URL — top-level image_versions2, or first carousel slide
        display_url = ""
        width = height = 0
        iv2 = media.get("image_versions2") or {}
        candidates: List[Dict] = iv2.get("candidates") or []
        if not candidates:
            carousel = media.get("carousel_media") or []
            if carousel and isinstance(carousel[0], dict):
                first_slide = carousel[0]
                candidates = (first_slide.get("image_versions2") or {}).get("candidates") or []
        if candidates:
            best = candidates[0]
            display_url = str(best.get("url") or "")
            width = int(best.get("width") or 0)
            height = int(best.get("height") or 0)
        if not width:
            width = int(media.get("original_width") or 0)
        if not height:
            height = int(media.get("original_height") or 0)

        caption_obj = media.get("caption") or {}
        caption = str(caption_obj.get("text") or "") if isinstance(caption_obj, dict) else ""

        # Determine human-readable post_type
        if product_type == "clips":
            post_type = "reels"
        else:
            post_type = _MEDIA_TYPE_LABEL.get(media_type, "unknown")

        result.append(RepostItem(
            shortcode=code,
            post_url=f"https://www.instagram.com/p/{code}/" if code else "",
            media_type=media_type,
            post_type=post_type,
            product_type=product_type,
            orig_username=orig_username,
            orig_user_id=orig_user_id,
            likes=int(media.get("like_count") or 0),
            comments=int(media.get("comment_count") or 0),
            view_count=int(media.get("view_count") or 0),
            carousel_count=int(media.get("carousel_media_count") or 0),
            caption=caption[:500],
            display_url=display_url,
            width=width,
            height=height,
            taken_at=taken_at,
            taken_at_str=taken_at_str,
        ))

    return result


# ── Reels Tab parser ─────────────────────────────────────────────────────────

def parse_reels_edges(
    edges: List[Dict[str, Any]],
    max_reels: int = 50,
) -> List[ReelItem]:
    """
    Parse edges from PolarisProfileReelsTabContentQuery_connection response.

    Each edge has the shape: {"node": {"media": {...}}}
    The `media` dict contains all reel fields.

    play_count is the primary metric — view_count is always null in this API.
    Taken_at is present directly (unlike Tagged Tab which requires pk decoding).
    """
    result: List[ReelItem] = []

    for edge in edges:
        if len(result) >= max_reels:
            break

        if not isinstance(edge, dict):
            continue
        node = edge.get("node") or edge
        if not isinstance(node, dict):
            continue

        # Reels Tab: node.media holds the actual reel media object
        media = node.get("media") or node
        if not isinstance(media, dict):
            continue

        code = str(media.get("code") or "")
        pk = str(media.get("pk") or "")

        # Timestamp: taken_at is directly present in Reels Tab
        taken_at = int(media.get("taken_at") or 0)
        if not taken_at and pk:
            taken_at = _pk_to_timestamp(pk)
        taken_at_str = (
            _dt.utcfromtimestamp(taken_at).strftime("%Y-%m-%d %H:%M")
            if taken_at
            else ""
        )

        # Thumbnail — best candidate from image_versions2
        thumbnail_url = ""
        width = height = 0
        iv2 = media.get("image_versions2") or {}
        candidates: List[Dict] = iv2.get("candidates") or []
        if candidates:
            best = candidates[0]
            thumbnail_url = str(best.get("url") or "")
            width = int(best.get("width") or 0)
            height = int(best.get("height") or 0)
        if not width:
            width = int(media.get("original_width") or 0)
        if not height:
            height = int(media.get("original_height") or 0)

        # Co-authors
        coauthor_ids = [
            str(p.get("id") or p.get("pk") or "")
            for p in (media.get("coauthor_producers") or [])
            if p.get("id") or p.get("pk")
        ]

        result.append(ReelItem(
            shortcode=code,
            post_url=f"https://www.instagram.com/p/{code}/" if code else "",
            pk=pk,
            play_count=int(media.get("play_count") or 0),
            like_count=int(media.get("like_count") or 0),
            comment_count=int(media.get("comment_count") or 0),
            coauthor_ids=coauthor_ids,
            thumbnail_url=thumbnail_url,
            width=width,
            height=height,
            taken_at=taken_at,
            taken_at_str=taken_at_str,
            is_pinned=bool(media.get("is_pinned") or False),
        ))

    return result


# ── Shortcode ↔ media_id conversion ──────────────────────────────────────────
# Instagram shortcodes encode the numeric media pk in base64-ish alphabet.
# This is a pure conversion — no API call required.

_SC_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_SC_INDEX = {c: i for i, c in enumerate(_SC_ALPHABET)}


def shortcode_to_media_id(shortcode: str) -> str:
    """Convert an Instagram shortcode to its numeric media_id string.

    Example: 'DXjuqH9nDVE' → '3704148491870169581'
    Raises ValueError if the shortcode contains characters outside the alphabet.
    """
    n = 0
    for c in shortcode:
        if c not in _SC_INDEX:
            raise ValueError(f"Invalid shortcode character: {c!r}")
        n = n * 64 + _SC_INDEX[c]
    return str(n)


# ── Comments parser ───────────────────────────────────────────────────────────

def parse_comments(
    raw_comments: List[Dict[str, Any]],
    caption_raw: Optional[Dict[str, Any]] = None,
    max_comments: int = 100,
) -> List[CommentItem]:
    """
    Parse raw comment list from /api/v1/media/{id}/comments/ response.

    The API returns:
    - caption as a separate top-level field (type=1, is_created_by_media_owner=True)
    - comments[] as an array, each with user, text, comment_like_count, etc.
    - GIF-only comments have text="" and giphy_media_info present
    - has_translation=True on non-English comments (auto-detected by Instagram)

    Returns caption first (is_caption=True) followed by regular comments.
    """
    result: List[CommentItem] = []

    if caption_raw and isinstance(caption_raw, dict):
        cap_user = caption_raw.get("user") or {}
        cap_ts = int(caption_raw.get("created_at") or 0)
        result.append(CommentItem(
            pk=str(caption_raw.get("pk") or ""),
            text=str(caption_raw.get("text") or ""),
            comment_index=-1,
            created_at=cap_ts,
            created_at_str=_dt.utcfromtimestamp(cap_ts).strftime("%Y-%m-%d %H:%M") if cap_ts else "",
            username=str(cap_user.get("username") or ""),
            user_id=str(cap_user.get("pk") or cap_user.get("id") or ""),
            full_name=str(cap_user.get("full_name") or ""),
            is_verified=bool(cap_user.get("is_verified")),
            is_private=bool(cap_user.get("is_private")),
            is_caption=True,
        ))

    for raw in raw_comments:
        if len(result) - (1 if caption_raw else 0) >= max_comments:
            break
        if not isinstance(raw, dict):
            continue

        user = raw.get("user") or {}
        ts = int(raw.get("created_at") or 0)

        gif_url = ""
        has_gif = False
        giphy = raw.get("giphy_media_info")
        if isinstance(giphy, dict):
            has_gif = True
            imgs = giphy.get("images") or {}
            fh = imgs.get("fixed_height") or {}
            gif_url = str(fh.get("url") or "")

        result.append(CommentItem(
            pk=str(raw.get("pk") or ""),
            text=str(raw.get("text") or ""),
            comment_index=int(raw.get("comment_index") or 0),
            comment_like_count=int(raw.get("comment_like_count") or 0),
            child_comment_count=int(raw.get("child_comment_count") or 0),
            created_at=ts,
            created_at_str=_dt.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
            username=str(user.get("username") or ""),
            user_id=str(user.get("pk") or user.get("id") or ""),
            full_name=str(user.get("full_name") or ""),
            is_verified=bool(user.get("is_verified")),
            is_private=bool(user.get("is_private")),
            has_translation=bool(raw.get("has_translation")),
            has_gif=has_gif,
            gif_url=gif_url,
        ))

    return result


# ── Post HTML parser ─────────────────────────────────────────────────────────

# All patterns target the JSON blobs Instagram embeds in every post page.
# They are intentionally narrow — only match well-known keys so we never
# accidentally pick up ad-tech or unrelated script tags.
_POST_LOCATION_RE  = re.compile(r'"location"\s*:\s*(\{[^}]{5,600}\})')
_POST_TAKEN_AT_RE  = re.compile(r'"taken_at"\s*:\s*(\d{10})')
_POST_USERNAME_RE  = re.compile(r'"username"\s*:\s*"([A-Za-z0-9._]+)"')
_POST_FULLNAME_RE  = re.compile(r'"full_name"\s*:\s*"([^"]{1,100})"')
_POST_USER_ID_RE   = re.compile(r'"owner"\s*:\s*\{[^}]*"id"\s*:\s*"(\d+)"')
_POST_VERIFIED_RE  = re.compile(r'"is_verified"\s*:\s*(true|false)')
_POST_LIKES_RE     = re.compile(r'"like_count"\s*:\s*(\d+)')
_POST_COMMENTS_RE  = re.compile(r'"comment_count"\s*:\s*(\d+)')
_POST_VIEWS_RE     = re.compile(r'"view_count"\s*:\s*(\d+)')
_POST_PLAYS_RE     = re.compile(r'"play_count"\s*:\s*(\d+)')
_POST_MEDIATYPE_RE = re.compile(r'"media_type"\s*:\s*(\d)')
_POST_PRODUCT_RE   = re.compile(r'"product_type"\s*:\s*"([^"]+)"')
_POST_CAROUSEL_RE  = re.compile(r'"carousel_media_count"\s*:\s*(\d+)')
_POST_CAPTION_RE   = re.compile(r'"caption"\s*:\s*\{[^}]*"text"\s*:\s*"((?:[^"\\]|\\.)*)\"')
_POST_WIDTH_RE     = re.compile(r'"original_width"\s*:\s*(\d+)')
_POST_HEIGHT_RE    = re.compile(r'"original_height"\s*:\s*(\d+)')
_POST_DURATION_RE  = re.compile(r'"video_duration"\s*:\s*([\d.]+)')
_POST_USERTAG_RE   = re.compile(r'"usertags"\s*:\s*\{[^}]*\}')
_POST_COAUTHOR_RE  = re.compile(r'"coauthor_producers"\s*:\s*\[([^\]]*)\]')
_POST_SPONSOR_RE   = re.compile(r'"product_tags"\s*:\s*\[([^\]]*)\]')
_POST_MUSIC_ARTIST = re.compile(r'"artist"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"')
_POST_MUSIC_TITLE  = re.compile(r'"title"\s*:\s*"([^"]{1,150})"')
_POST_DISPLAYURL_RE = re.compile(r'"display_url"\s*:\s*"(https://[^"]+)"')


def parse_post_html(html: str, shortcode: str) -> PostInfo:
    """
    Extract PostInfo from the raw HTML of https://www.instagram.com/p/{shortcode}/.

    Instagram embeds all post metadata in JSON blobs inside <script> tags.
    We parse with targeted regexes rather than a full JSON parse because:
    - The page is ~1 MB and contains many nested objects
    - Only a small subset of fields is needed
    - Regex is an order of magnitude faster than json.loads on 1 MB

    All fields are best-effort — missing ones stay at their zero/empty defaults.
    """
    info = PostInfo(
        shortcode=shortcode,
        post_url=f"https://www.instagram.com/p/{shortcode}/",
    )

    # Extract the script tag containing post metadata — search only ~10KB instead of 1MB
    import re as _re_local
    _script_block = _re_local.search(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        html,
        _re_local.DOTALL,
    )
    if not _script_block:
        # Fallback: find any script block containing 'taken_at'
        _script_block = _re_local.search(
            r'<script[^>]*>((?:[^<]|<(?!/script))*taken_at(?:[^<]|<(?!/script))*)</script>',
            html,
            _re_local.DOTALL,
        )
    _search_html = _script_block.group(1) if _script_block else html

    # ── Location ──────────────────────────────────────────────────────────────
    m = _POST_LOCATION_RE.search(_search_html)
    if m:
        try:
            import json as _json
            loc_obj = _json.loads(m.group(1))
            lat = float(loc_obj.get("lat") or 0)
            lng = float(loc_obj.get("lng") or 0)
            name = str(loc_obj.get("name") or "")
            pk = str(loc_obj.get("pk") or "")
            maps_url = (
                f"https://www.google.com/maps?q={lat},{lng}"
                if lat and lng else ""
            )
            info.location = PostLocation(name=name, lat=lat, lng=lng, pk=pk, maps_url=maps_url)
        except (ValueError, KeyError):
            pass

    # ── Timestamp ─────────────────────────────────────────────────────────────
    m = _POST_TAKEN_AT_RE.search(_search_html)
    if m:
        ts = int(m.group(1))
        if _IG_TS_MIN <= ts <= _IG_TS_MAX:
            info.taken_at = ts
            info.taken_at_str = (
                _dt.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
            )

    # ── Author ────────────────────────────────────────────────────────────────
    m = _POST_USERNAME_RE.search(_search_html)
    if m:
        info.username = m.group(1)

    m = _POST_FULLNAME_RE.search(_search_html)
    if m:
        info.full_name = m.group(1)

    m = _POST_USER_ID_RE.search(_search_html)
    if m:
        info.user_id = m.group(1)

    m = _POST_VERIFIED_RE.search(_search_html)
    if m:
        info.is_verified = m.group(1) == "true"

    # ── Engagement ────────────────────────────────────────────────────────────
    m = _POST_LIKES_RE.search(_search_html)
    if m:
        info.likes = int(m.group(1))

    m = _POST_COMMENTS_RE.search(_search_html)
    if m:
        info.comments = int(m.group(1))

    m = _POST_VIEWS_RE.search(_search_html)
    if m:
        info.view_count = int(m.group(1))

    m = _POST_PLAYS_RE.search(_search_html)
    if m:
        info.play_count = int(m.group(1))

    # ── Media type ────────────────────────────────────────────────────────────
    m = _POST_MEDIATYPE_RE.search(_search_html)
    if m:
        info.media_type = int(m.group(1))

    m = _POST_PRODUCT_RE.search(_search_html)
    if m:
        info.product_type = m.group(1)

    m = _POST_CAROUSEL_RE.search(_search_html)
    if m:
        info.carousel_count = int(m.group(1))

    # Resolve human-readable type
    if info.product_type == "clips":
        info.post_type = "reels"
    else:
        info.post_type = _MEDIA_TYPE_LABEL.get(info.media_type, "unknown")

    # ── Caption ───────────────────────────────────────────────────────────────
    m = _POST_CAPTION_RE.search(_search_html)
    if m:
        raw_cap = m.group(1)
        # Unescape JSON string escapes: \n \t \" \\ \uXXXX
        try:
            import json as _json
            info.caption = _json.loads(f'"{raw_cap}"')
        except (ValueError, UnicodeDecodeError):
            info.caption = raw_cap.replace("\\n", "\n").replace("\\t", "\t")
        info.hashtags = _HASHTAG_RE.findall(info.caption)
        info.mentions = _MENTION_RE.findall(info.caption)

    # ── Dimensions / duration ─────────────────────────────────────────────────
    m = _POST_WIDTH_RE.search(_search_html)
    if m:
        info.width = int(m.group(1))

    m = _POST_HEIGHT_RE.search(_search_html)
    if m:
        info.height = int(m.group(1))

    m = _POST_DURATION_RE.search(_search_html)
    if m:
        info.duration_secs = float(m.group(1))

    # ── Display URL (first image) ─────────────────────────────────────────────
    m = _POST_DISPLAYURL_RE.search(_search_html)
    if m:
        info.display_url = m.group(1)

    # ── Coauthors ────────────────────────────────────────────────────────────
    m = _POST_COAUTHOR_RE.search(_search_html)
    if m:
        info.coauthors = _POST_USERNAME_RE.findall(m.group(1))

    # ── Music (reels) ────────────────────────────────────────────────────────
    m = _POST_MUSIC_ARTIST.search(_search_html)
    if m:
        info.music_artist = m.group(1)

    m = _POST_MUSIC_TITLE.search(_search_html)
    if m:
        info.music_title = m.group(1)

    return info
