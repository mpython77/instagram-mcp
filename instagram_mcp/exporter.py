"""
JsonExporter — automatic JSON persistence for all MCP tool results.

Directory layout:
    <export_dir>/
        index.json                          ← running log of every save
        profile/
            nike_2026-05-15_10-23-45.json
        feed_deep/
            cristiano_2026-05-15_10-24-00.json
        engagement/ | collab_network/ | compare/ | bulk_check/
        batch_scrape/ | tagged_by/ | reposts/ | post/ | reels/ | comments/

Each saved file (AI-optimised format):
    {
        "_meta":    { tool, subject, saved_at, saved_at_ts, duration_s, server_version },
        "_summary": { key metrics at a glance — AI reads this first },
        "data":     { clean, noise-free payload — empty fields stripped, CDN URLs removed }
    }

Configuration (env vars):
    INSTAGRAM_MCP_EXPORT_ENABLED  — '0' or 'false' disables all saving (default: enabled)
    INSTAGRAM_MCP_EXPORT_DIR      — output directory path (default: ./exports)
    INSTAGRAM_MCP_EXPORT_INDENT   — JSON indent spaces, 0 = compact (default: 2)
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
import json
import logging
import re
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._path_guard import ensure_path

__all__ = ["JsonExporter", "CsvExporter"]

logger = logging.getLogger("instagram_mcp.exporter")

_SERVER_VERSION = "1.0.0"

# CDN URL fields — hundreds of chars, expire quickly, useless for AI
_STRIP_URL_FIELDS = frozenset({
    "display_url", "thumbnail_url", "profile_pic_url",
    "video_url", "video_versions",
})

# Unix-timestamp fields made redundant by their *_str counterparts
_STRIP_TS_FIELDS = frozenset({"taken_at", "accessed_at"})

# Always strip — these fields have no AI value in any context
_ALWAYS_STRIP_FIELDS = frozenset({
    "width", "height",    # image pixel dimensions
})

# Strip when value == 0  (technical fields with no AI value when zero)
_STRIP_ZERO_FIELDS = frozenset({
    "carousel_count",     # posts without carousel slides
    "video_view_count",   # non-video posts
    "highlight_count",    # account highlight count
    "usertags_count",     # tagged-photo count on profile
    "account_type",       # internal code (0/1/2/3)
})

# Strip when value == 1  (fields only meaningful when > 1)
_STRIP_ONE_FIELDS = frozenset({"pages_fetched"})

# Strip when value == False  (almost-always-false flags)
_STRIP_FALSE_FIELDS = frozenset({
    "is_video",          # already encoded in post_type
    "is_pinned",         # default is not pinned; True is kept
    "is_new_account",    # almost never true
    "has_guides",        # almost never true
    "is_professional",   # redundant with is_business/is_verified
    "has_more_posts",    # pagination detail, not content
})

# Strip when value is None  (null placeholders with no information)
_STRIP_NONE_FIELDS = frozenset({"location"})


# ─────────────────────────────────────────────────────────────────────────────
# JSON encoder — handles all Instagram MCP types
# ─────────────────────────────────────────────────────────────────────────────

class _Encoder(json.JSONEncoder):
    """
    Extend JSONEncoder to handle types that appear in Instagram MCP data:
      - dataclasses (InstagramProfile, InstagramPost, FeedTagResult, …)
      - Pydantic BaseModel
      - datetime / date → ISO string
      - Enum → .value
      - set → sorted list
      - Path → str
      - bytes → UTF-8 str
    """

    def default(self, obj: Any) -> Any:  # type: ignore[override]
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, set):
            return sorted(str(x) for x in obj)
        if isinstance(obj, Path):
            return obj.as_posix()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


# ─────────────────────────────────────────────────────────────────────────────
# AI-friendly data cleaning
# ─────────────────────────────────────────────────────────────────────────────

def _strip_noise(obj: Any, _key: str = "") -> Any:
    """
    Recursively clean a fully-serialised dict/list for AI consumption.

    Rules applied per field:
      - Always strip: empty ""  []  {}
      - Always strip: CDN URL fields  (display_url, thumbnail_url, …)
      - Always strip: redundant unix TS  (taken_at — kept as taken_at_str)
      - Strip when 0: technical size/code fields  (width, height, …)
      - Strip when False: almost-always-false flags  (is_video, is_new_account, …)
      - Strip when None: null-placeholder fields  (location)
      - Keep always: 0 for counts (likes, followers), False for important flags
    """
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in _ALWAYS_STRIP_FIELDS:
                continue
            if k in _STRIP_URL_FIELDS:
                continue
            if k in _STRIP_TS_FIELDS:
                continue
            if k in _STRIP_ZERO_FIELDS and v == 0:
                continue
            if k in _STRIP_ONE_FIELDS and v == 1:
                continue
            if k in _STRIP_FALSE_FIELDS and v is False:
                continue
            if k in _STRIP_NONE_FIELDS and v is None:
                continue
            cleaned = _strip_noise(v, k)
            if cleaned == "" or cleaned == [] or cleaned == {}:
                continue
            out[k] = cleaned
        return out

    if isinstance(obj, list):
        cleaned_list = [_strip_noise(i) for i in obj]
        return [i for i in cleaned_list if i != "" and i != [] and i != {}]

    return obj


def _fmt_num(n: int) -> str:
    """Format large numbers: 664_400_000 → '664.4M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _profile_summary(profile: dict) -> dict:
    s: dict = {}
    u = profile.get("username", "")
    fn = profile.get("full_name", "")
    s["account"] = f"@{u}" + (f" ({fn})" if fn else "")
    followers = profile.get("followers", 0)
    s["followers"] = _fmt_num(followers)
    if profile.get("is_verified"):
        s["verified"] = True
    if profile.get("is_private"):
        s["private"] = True
    if profile.get("website"):
        s["website"] = profile["website"]
    if profile.get("biography"):
        bio = profile["biography"]
        s["bio"] = bio[:120] + ("…" if len(bio) > 120 else "")
    return s


def _make_summary(tool: str, data: dict) -> dict:
    """
    Build a concise `_summary` block tailored to each tool.
    AI should read this first — it covers 90% of use cases in a few lines.
    """
    s: dict = {"tool": tool}

    profile = data.get("profile") or {}
    if profile:
        s.update(_profile_summary(profile))

    if tool == "profile":
        s["status"] = "inactive" if data.get("is_dead") else "active"
        lpd = data.get("last_post_days", 0)
        if lpd:
            s["last_post_days"] = lpd
        ft = data.get("feed_tags") or {}
        tags = ft.get("tags") or []
        if tags:
            s["collaborators"] = tags
        posts_checked = ft.get("posts_checked", 0)
        if posts_checked:
            s["posts_checked"] = posts_checked

    elif tool == "feed_deep":
        s["status"] = "inactive" if data.get("is_dead") else "active"
        s["posts_fetched"] = data.get("posts_fetched", 0)
        ft = data.get("feed_tags") or {}
        tags = ft.get("tags") or []
        if tags:
            s["collaborators"] = tags[:20]

    elif tool == "engagement":
        posts: List[dict] = data.get("posts") or []
        s["posts_analyzed"] = len(posts)
        if posts:
            followers = profile.get("followers", 1) or 1
            likes_list = [p.get("likes", 0) for p in posts]
            comments_list = [p.get("comments", 0) for p in posts]
            avg_l = sum(likes_list) / len(likes_list)
            avg_c = sum(comments_list) / len(comments_list)
            er = (avg_l + avg_c) / followers * 100
            s["avg_likes"] = _fmt_num(int(avg_l))
            s["avg_comments"] = _fmt_num(int(avg_c))
            s["engagement_rate_pct"] = round(er, 3)
            rating = (
                "excellent" if er >= 6 else
                "good"      if er >= 3 else
                "average"   if er >= 1 else
                "low"
            )
            s["er_rating"] = rating
            top = sorted(posts, key=lambda p: p.get("likes", 0), reverse=True)[:3]
            s["top_posts"] = [
                {"url": p.get("post_url", ""), "likes": p.get("likes", 0)}
                for p in top
            ]

    elif tool == "collab_network":
        posts: List[dict] = data.get("posts") or []
        s["posts_analyzed"] = len(posts)
        from collections import Counter
        c: Counter = Counter()
        for p in posts:
            for u in (p.get("usertags") or []) + (p.get("mentions") or []):
                c[u] += 1
        s["top_collaborators"] = [
            {"username": u, "appearances": n}
            for u, n in c.most_common(10)
        ]

    elif tool == "compare":
        profiles_data: List[dict] = data.get("profiles") or []
        s["count"] = len(profiles_data)
        s["accounts"] = [
            {
                "username": (pd.get("profile") or {}).get("username", ""),
                "followers": _fmt_num((pd.get("profile") or {}).get("followers", 0)),
                "status": "inactive" if pd.get("is_dead") else "active",
            }
            for pd in profiles_data
        ]

    elif tool == "bulk_check":
        results: List[dict] = data.get("results") or []
        found = [r for r in results if r.get("found")]
        s["total"] = len(results)
        s["found"] = len(found)
        s["not_found"] = len(results) - len(found)
        active = sum(1 for r in found if not r.get("is_dead") and not r.get("is_private"))
        s["active"] = active

    elif tool == "batch_scrape":
        st = data.get("stats") or {}
        s["total"] = st.get("total", 0)
        s["completed"] = st.get("completed", 0)
        s["active"] = st.get("active", 0)
        s["dead"] = st.get("dead", 0)
        s["private"] = st.get("private", 0)
        s["not_found"] = st.get("not_found", 0)
        s["rate_per_sec"] = st.get("rate", 0)
        s["output_file"] = data.get("output_file", "")

    elif tool == "post":
        post = data.get("post") or {}
        s["shortcode"] = post.get("shortcode", "")
        s["post_url"] = post.get("post_url", "")
        s["author"] = post.get("username", "")
        s["type"] = post.get("post_type", "")
        s["date"] = post.get("taken_at_str", "")
        s["likes"] = post.get("like_count", 0)
        s["comments"] = post.get("comment_count", 0)
        loc = post.get("location") or {}
        if loc.get("name"):
            s["location"] = loc["name"]
            if loc.get("lat") and loc.get("lng"):
                s["gps"] = f"{loc['lat']},{loc['lng']}"

    elif tool == "tagged_by":
        tagged: List[dict] = data.get("tagged_posts") or []
        s["total_tagged_posts"] = len(tagged)
        if tagged:
            from collections import Counter
            taggers: Counter = Counter(p.get("poster_username", "") for p in tagged)
            s["top_taggers"] = [
                {"username": u, "count": n}
                for u, n in taggers.most_common(5)
            ]

    elif tool == "reposts":
        items: List[dict] = data.get("repost_items") or []
        s["total_reposts"] = len(items)
        if items:
            s["most_recent"] = items[0].get("taken_at_str", "")

    elif tool == "reels":
        reels: List[dict] = data.get("reels") or []
        s["total_reels"] = len(reels)
        if reels:
            top = sorted(reels, key=lambda r: r.get("play_count", 0), reverse=True)[:3]
            s["top_by_plays"] = [
                {"url": r.get("post_url", ""), "plays": _fmt_num(r.get("play_count", 0))}
                for r in top
            ]

    elif tool == "comments":
        s["shortcode"] = data.get("shortcode", "")
        s["total_comments"] = data.get("comment_count", 0)
        comments: List[dict] = data.get("comments") or []
        actual = [c for c in comments if not c.get("is_caption")]
        s["fetched"] = len(actual)
        if actual:
            top = sorted(actual, key=lambda c: c.get("like_count", 0), reverse=True)[:3]
            s["top_comments"] = [
                {
                    "author": c.get("username", ""),
                    "likes": c.get("like_count", 0),
                    "text": (c.get("text") or "")[:100],
                }
                for t in top
                if (c := t)
            ]

    elif tool == "hashtag":
        s["tag"] = f"#{data.get('tag', '')}"
        posts: List[dict] = data.get("posts") or []
        s["posts_returned"] = len(posts)
        s["has_more"] = data.get("has_more", False)
        related: List[str] = data.get("related_searches") or []
        if related:
            s["related_searches"] = related[:5]
        # Top posts by play_count (videos)
        top_posts = []
        for edge in posts[:12]:
            node = edge.get("node", {}) if isinstance(edge, dict) and "node" in edge else edge
            user = (node.get("user") or {})
            top_posts.append({
                "username": user.get("username", ""),
                "shortcode": node.get("code", ""),
                "play_count": node.get("play_count"),
            })
        s["top_posts"] = [p for p in top_posts if p["shortcode"]]

    elif tool == "search":
        s["query"]   = data.get("query", "")
        s["context"] = data.get("context", "blended")
        users: List[dict] = data.get("users") or []
        hashtags: List[dict] = data.get("hashtags") or []
        s["users_returned"]    = len(users)
        s["hashtags_returned"] = len(hashtags)
        s["has_more"] = data.get("has_more", False)
        s["top_users"] = [
            {"username": u.get("username"), "followers": u.get("follower_count_text"), "verified": u.get("is_verified")}
            for u in users[:5]
        ]
        if hashtags:
            s["top_hashtags"] = [
                {"name": ht.get("name"), "posts": ht.get("subtitle")}
                for ht in hashtags[:5]
            ]

    elif tool == "followers":
        s["username"]  = data.get("username", "")
        users: List[dict] = data.get("users") or []
        s["users_returned"] = len(users)
        s["has_more"]       = data.get("has_more", False)
        s["should_limit"]   = data.get("should_limit", False)
        s["verified_count"] = sum(1 for u in users if u.get("is_verified"))
        s["sample"] = [{"username": u.get("username"), "verified": u.get("is_verified")} for u in users[:5]]

    elif tool == "following":
        s["username"]      = data.get("username", "")
        users: List[dict]  = data.get("users") or []
        s["users_returned"] = len(users)
        s["pages_fetched"]  = data.get("pages_fetched", 1)
        s["has_more"]       = data.get("has_more", False)
        s["verified_count"] = sum(1 for u in users if u.get("is_verified"))
        s["favorite_count"] = sum(1 for u in users if u.get("is_favorite"))
        s["sample"] = [{"username": u.get("username"), "verified": u.get("is_verified"), "favorite": u.get("is_favorite")} for u in users[:5]]

    elif tool == "post_likers":
        s["shortcode"]      = data.get("shortcode", "")
        s["total_likes"]    = data.get("user_count", 0)
        users: List[dict]   = data.get("users") or []
        s["users_returned"] = len(users)
        s["verified_count"] = sum(1 for u in users if u.get("is_verified"))
        s["you_follow"]     = sum(1 for u in users if u.get("you_follow_them"))
        s["sample"] = [{"username": u.get("username"), "verified": u.get("is_verified")} for u in users[:5]]

    elif tool == "highlights":
        highlights = data.get("highlights", [])
        all_media = [item for h in highlights for item in (h.get("items") or [])]
        return {
            "username": data.get("username"),
            "highlight_count": data.get("highlight_count", 0),
            "total_stories": sum(h.get("media_count", 0) for h in highlights),
            "pinned": sum(1 for h in highlights if h.get("is_pinned")),
            "archived": sum(1 for h in highlights if h.get("is_archived")),
            "with_media_fetched": sum(1 for h in highlights if h.get("items")),
            "boomerangs": sum(1 for i in all_media if i.get("capture_type") == "boomerang"),
            "selfies": sum(1 for i in all_media if i.get("camera_facing") == "front"),
            "sample": [
                {
                    "id": h.get("id"),
                    "title": h.get("title"),
                    "media_count": h.get("media_count"),
                    "created_at_str": h.get("created_at_str"),
                    "latest_reel_media": h.get("latest_reel_media"),
                    "highlight_reel_type": h.get("highlight_reel_type"),
                    "is_pinned": h.get("is_pinned"),
                    "is_archived": h.get("is_archived"),
                    "items_fetched": len(h.get("items", [])),
                }
                for h in highlights[:5]
            ],
        }

    elif tool == "stories":
        items = data.get("items", [])
        return {
            "username": data.get("username"),
            "story_count": data.get("story_count", 0),
            "expiring_at": data.get("expiring_at"),
            "images": sum(1 for i in items if i.get("media_type") == 1),
            "videos": sum(1 for i in items if i.get("media_type") == 2),
            "boomerangs": sum(1 for i in items if i.get("capture_type") == "boomerang"),
            "selfies": sum(1 for i in items if i.get("camera_facing") == "front"),
            "with_music": sum(1 for i in items if i.get("music_title")),
            "with_mentions": sum(1 for i in items if i.get("mentions")),
            "with_hashtags": sum(1 for i in items if i.get("hashtags")),
            "with_link_stickers": sum(1 for i in items if i.get("link_stickers")),
            "with_polls": sum(1 for i in items if i.get("polls")),
            "with_linked_post": sum(1 for i in items if i.get("linked_post_code")),
            "paid_partnerships": sum(1 for i in items if i.get("is_paid_partnership")),
            "sample": [
                {
                    "shortcode": i.get("shortcode"),
                    "media_type": i.get("media_type"),
                    "taken_at_str": i.get("taken_at_str"),
                    "capture_type": i.get("capture_type") or None,
                    "camera_facing": i.get("camera_facing") or None,
                    "music_title": i.get("music_title"),
                    "mentions": i.get("mentions", []),
                    "hashtags": i.get("hashtags", []),
                    "link_stickers": [ls.get("display_url") for ls in (i.get("link_stickers") or [])],
                    "polls": [{"q": p.get("question"), "votes": sum(t.get("count",0) for t in p.get("tallies",[]))} for p in (i.get("polls") or [])],
                }
                for i in items[:5]
            ],
        }

    elif tool == "location_posts":
        posts: List[dict] = data.get("posts", [])
        return {
            "location_id":    data.get("location_id", ""),
            "location_name":  data.get("location_name", ""),
            "post_count":     data.get("post_count", 0),
            "more_available": data.get("more_available", False),
            "videos":         sum(1 for p in posts if p.get("media_type") == 2),
            "carousels":      sum(1 for p in posts if p.get("media_type") == 8),
            "images":         sum(1 for p in posts if p.get("media_type") == 1),
            "verified_count": sum(1 for p in posts if p.get("is_verified")),
            "total_likes":    sum(p.get("like_count") or 0 for p in posts),
            "sample": [
                {
                    "shortcode":   p.get("shortcode"),
                    "username":    p.get("username"),
                    "media_type":  p.get("media_type"),
                    "like_count":  p.get("like_count"),
                    "play_count":  p.get("play_count"),
                    "taken_at_str": p.get("taken_at_str"),
                }
                for p in posts[:5]
            ],
        }

    elif tool == "audio_reels":
        posts_r: List[dict] = data.get("posts", [])
        return {
            "audio_cluster_id": data.get("audio_cluster_id", ""),
            "music_title":      data.get("music_title", ""),
            "music_artist":     data.get("music_artist", ""),
            "total_reels_str":  data.get("total_reels_str", ""),
            "reels_returned":   len(posts_r),
            "more_available":   data.get("more_available", False),
            "verified_count":   sum(1 for p in posts_r if p.get("is_verified")),
            "total_likes":      sum(p.get("like_count") or 0 for p in posts_r),
            "total_plays":      sum(p.get("play_count") or 0 for p in posts_r),
            "sample": [
                {
                    "shortcode":   p.get("shortcode"),
                    "username":    p.get("username"),
                    "like_count":  p.get("like_count"),
                    "play_count":  p.get("play_count"),
                    "taken_at_str": p.get("taken_at_str"),
                }
                for p in posts_r[:5]
            ],
        }

    # Remove empty values from summary itself
    return {k: v for k, v in s.items() if v not in ("", [], {}, None)}


# ─────────────────────────────────────────────────────────────────────────────
# JsonExporter
# ─────────────────────────────────────────────────────────────────────────────

class JsonExporter:
    """
    Persists every successful MCP tool result as a pretty-printed, AI-friendly JSON file.

    Pipeline:
        raw data  →  _Encoder (dataclass/datetime/enum → plain Python)
                  →  _strip_noise (remove empty fields, CDN URLs, redundant TS)
                  →  _make_summary (concise _summary block for quick AI scan)
                  →  atomic file write + index update

    All file I/O runs in a thread pool — never blocks the event loop.
    Writes are atomic (.tmp → rename). asyncio.Lock protects index.json.
    All failures are logged — the tool response is never affected.

    Usage:
        exporter = JsonExporter.from_config(config)
        await exporter.save("profile", "nike", data_dict, duration_s=1.23)
    """

    def __init__(
        self,
        export_dir: str | Path = "exports",
        indent: int = 2,
        enabled: bool = True,
    ) -> None:
        export_dir = ensure_path(export_dir, name="export_dir")
        self._export_dir = Path(export_dir).expanduser().resolve()
        self.indent = max(0, int(indent))
        self.enabled = bool(enabled)
        self._lock = asyncio.Lock()

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: Any) -> "JsonExporter":
        return cls(
            export_dir=getattr(config, "export_dir", "exports"),
            indent=getattr(config, "export_indent", 2),
            enabled=getattr(config, "export_enabled", True),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    async def save(
        self,
        tool: str,
        subject: str,
        data: Any,
        duration_s: float = 0.0,
    ) -> Optional[Path]:
        """
        Save *data* to <export_dir>/<tool>/<subject>_<YYYY-MM-DD_HH-MM-SS>.json.

        The saved file has three top-level keys:
          _meta    — provenance (tool, subject, timestamp, duration)
          _summary — concise AI-readable overview generated from data
          data     — clean, noise-free payload (empty fields & CDN URLs removed)

        Never raises — failures are logged and None is returned.
        Returns the saved file Path on success.
        """
        if not self.enabled:
            return None

        now = datetime.now(timezone.utc)

        # 1. Serialise (dataclasses → dicts, datetimes → strings, …)
        try:
            serialised: Any = json.loads(
                json.dumps(data, cls=_Encoder, ensure_ascii=False)
            )
        except Exception as exc:
            logger.warning("JsonExporter: serialisation failed [%s/%s]: %s", tool, subject, exc)
            return None

        # 2. Strip noise (empty fields, CDN URLs, redundant unix TS)
        clean_data = _strip_noise(serialised)

        # 3. Build AI summary
        try:
            summary = _make_summary(tool, clean_data)
        except Exception as exc:
            logger.debug("JsonExporter: summary generation failed [%s]: %s", tool, exc)
            summary = {"tool": tool}

        payload: dict = {
            "_meta": {
                "tool": tool,
                "subject": subject,
                "saved_at": now.isoformat(),
                "saved_at_ts": int(now.timestamp()),
                "duration_s": round(duration_s, 3),
                "server_version": _SERVER_VERSION,
            },
            "_summary": summary,
            "data": clean_data,
        }

        path = self._make_path(tool, subject, now)

        # 4. Atomic file write
        try:
            await asyncio.to_thread(self._write_sync, path, payload)
        except Exception as exc:
            logger.warning("JsonExporter: write failed [%s/%s]: %s", tool, subject, exc)
            return None

        # 5. Update index (serialised via lock to prevent concurrent corruption)
        index_entry = {
            "tool": tool,
            "subject": subject,
            "file": path.relative_to(self._export_dir).as_posix(),
            "saved_at": now.isoformat(),
            "duration_s": round(duration_s, 3),
        }
        try:
            async with self._lock:
                await asyncio.to_thread(self._update_index_sync, index_entry)
        except Exception as exc:
            logger.warning("JsonExporter: index update failed: %s", exc)

        logger.debug("JsonExporter: saved %s/%s → %s", tool, subject, path.name)
        return path

    # ── Sync helpers (run inside asyncio.to_thread) ───────────────────────────

    def _make_path(self, tool: str, subject: str, now: datetime) -> Path:
        safe_tool = re.sub(r"[^a-z0-9_]", "_", tool.lower())
        safe_subject = re.sub(r"[^a-zA-Z0-9_\-\+\.]", "_", subject)[:60]
        ts = now.strftime("%Y-%m-%d_%H-%M-%S")
        return self._export_dir / safe_tool / f"{safe_subject}_{ts}.json"

    def _write_sync(self, path: Path, payload: dict) -> None:
        """Atomic write: serialise → .tmp → rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=self.indent or None),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _update_index_sync(self, entry: dict) -> None:
        """Append one entry to index.json (read → append → atomic rewrite)."""
        idx = self._export_dir / "index.json"
        self._export_dir.mkdir(parents=True, exist_ok=True)
        try:
            existing: list = (
                json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
            )
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
        existing.append(entry)
        tmp = idx.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(idx)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


# ─────────────────────────────────────────────────────────────────────────────
# CsvExporter
# ─────────────────────────────────────────────────────────────────────────────

class CsvExporter:
    """Export tool results as CSV files.

    Provides CSV and Markdown export alongside the existing JsonExporter.
    Handles nested dicts by flattening keys with dot notation, and handles
    lists by writing one row per item.
    """

    def __init__(self, export_dir: str | Path = "exports", enabled: bool = True) -> None:
        self._export_dir = Path(export_dir).expanduser().resolve()
        self.enabled = bool(enabled)

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    def _flatten_dict(self, d: dict, parent_key: str = "", sep: str = ".") -> dict:
        """Flatten a nested dict using dot notation for keys."""
        items: list = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep).items())
            elif isinstance(v, list):
                # Store lists as JSON strings in the cell
                items.append((new_key, json.dumps(v, ensure_ascii=False)))
            else:
                items.append((new_key, v))
        return dict(items)

    def _make_csv_path(self, tool_name: str, subject: str) -> Path:
        safe_tool = re.sub(r"[^a-z0-9_]", "_", tool_name.lower())
        safe_subject = re.sub(r"[^a-zA-Z0-9_\-\+\.]", "_", subject)[:60]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        return self._export_dir / safe_tool / f"{safe_subject}_{ts}.csv"

    def export_csv(self, tool_name: str, subject: str, data: dict) -> Optional[Path]:
        """Flatten data dict into CSV rows and write to file.

        If data contains a list at the top level (or a key whose value is a list
        of dicts), each list item becomes a row. Otherwise a single row is written
        with flattened keys as columns.

        Returns the file Path on success, None if disabled or on error.
        """
        if not self.enabled:
            return None

        try:
            path = self._make_csv_path(tool_name, subject)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Determine rows: if data has a list of dicts, use those as rows
            rows: list[dict] = []
            list_key = None
            for k, v in data.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    list_key = k
                    break

            if list_key:
                for item in data[list_key]:
                    flat = self._flatten_dict(item)
                    rows.append(flat)
            else:
                rows.append(self._flatten_dict(data))

            if not rows:
                return None

            # Collect all fieldnames across all rows
            fieldnames: list[str] = []
            seen: set = set()
            for row in rows:
                for k in row:
                    if k not in seen:
                        fieldnames.append(k)
                        seen.add(k)

            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)

            logger.debug("CsvExporter: saved %s/%s -> %s", tool_name, subject, path.name)
            return path

        except Exception as exc:
            logger.warning("CsvExporter: export_csv failed [%s/%s]: %s", tool_name, subject, exc)
            return None

    def export_markdown(self, tool_name: str, subject: str, content: str) -> Optional[Path]:
        """Save markdown content to file.

        Returns the file Path on success, None if disabled or on error.
        """
        if not self.enabled:
            return None

        try:
            safe_tool = re.sub(r"[^a-z0-9_]", "_", tool_name.lower())
            safe_subject = re.sub(r"[^a-zA-Z0-9_\-\+\.]", "_", subject)[:60]
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
            path = self._export_dir / safe_tool / f"{safe_subject}_{ts}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.debug("CsvExporter: saved markdown %s/%s -> %s", tool_name, subject, path.name)
            return path

        except Exception as exc:
            logger.warning("CsvExporter: export_markdown failed [%s/%s]: %s", tool_name, subject, exc)
            return None
