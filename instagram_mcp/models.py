"""
Data models + Pydantic input validation models.

Data classes — internal structures:
  - InstagramProfile, InstagramPost, FeedTagResult, ProfileWithTags

Pydantic models — MCP tool inputs:
  - UsernameInput, ProfileWithTagsInput, FeedTagsInput, BulkProfilesInput, AccountStatusInput

Diagnostics:
  - CacheStats, ProxyStatus
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_user_date(s: str) -> Optional[int]:
    """
    Parse a user-supplied date string into a Unix timestamp.

    Accepts: 'DD.MM.YYYY', 'YYYY-MM-DD', 'DD/MM/YYYY'. Empty → None.
    Raises ValueError on a non-empty value that fails to parse.
    """
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace(",", ".")
    # Detect format by structure to minimize strptime attempts
    if len(s) == 10 and s[4] == '-':
        fmts = ["%Y-%m-%d"]
    elif len(s) == 10 and s[2] in ('.', '/', '-'):
        sep = s[2]
        if sep == '.':
            fmts = ["%d.%m.%Y"]
        elif sep == '/':
            fmts = ["%d/%m/%Y"]
        else:
            fmts = ["%d-%m-%Y"]
    else:
        fmts = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]
    for fmt in fmts:
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    raise ValueError(
        f"Invalid date {s!r} — use DD.MM.YYYY (e.g. 01.03.2026) or YYYY-MM-DD"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class AccountStatus(str, Enum):
    """Account status."""
    ACTIVE = "active"
    DEAD = "dead"
    PRIVATE = "private"
    NOT_FOUND = "not_found"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES (internal)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class InstagramProfile:
    """Instagram profile data."""
    user_id: str = ""
    username: str = ""
    full_name: str = ""
    biography: str = ""
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    category: str = ""
    website: str = ""
    external_url: str = ""
    is_private: bool = False
    is_verified: bool = False
    is_business: bool = False
    profile_pic_url: str = ""
    # Extended profile fields
    highlight_count: int = 0
    pronouns: List[str] = field(default_factory=list)
    is_professional: bool = False
    account_type: int = 0           # 1=personal, 2=creator, 3=business
    has_reels: bool = False
    has_guides: bool = False
    contact_phone: str = ""
    public_email: str = ""
    city: str = ""
    usertags_count: int = 0
    is_new_account: bool = False
    overall_category: str = ""

    def __post_init__(self) -> None:
        if self.followers < 0:
            self.followers = 0
        if self.following < 0:
            self.following = 0
        if self.posts_count < 0:
            self.posts_count = 0
        if self.highlight_count < 0:
            self.highlight_count = 0
        if self.usertags_count < 0:
            self.usertags_count = 0
        self.username = self.username.strip().lower()

    def __repr__(self) -> str:
        return f"Profile(username={self.username!r}, followers={self.followers})"


@dataclass
class InstagramPost:
    """Single Instagram post data."""
    shortcode: str = ""
    post_url: str = ""
    post_type: str = ""          # image | video | carousel | reel | igtv
    taken_at: int = 0
    taken_at_str: str = ""
    age_days: float = 0.0
    display_url: str = ""
    thumbnail_url: str = ""
    is_video: bool = False
    likes: int = 0
    comments: int = 0
    video_view_count: int = 0
    caption: str = ""
    accessibility_caption: str = ""
    product_type: str = ""          # "feed" | "reel" | "igtv" | "clips"
    usertags: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    coauthors: List[str] = field(default_factory=list)
    sponsor_tags: List[str] = field(default_factory=list)
    carousel_count: int = 0
    width: int = 0
    height: int = 0
    music_artist: str = ""
    music_title: str = ""
    location: Optional[Dict[str, Any]] = None
    hashtags: List[str] = field(default_factory=list)
    is_pinned: bool = False

    def __post_init__(self) -> None:
        if self.likes < 0:
            self.likes = 0
        if self.comments < 0:
            self.comments = 0
        if self.video_view_count < 0:
            self.video_view_count = 0
        if self.carousel_count < 0:
            self.carousel_count = 0
        if self.width < 0:
            self.width = 0
        if self.height < 0:
            self.height = 0

    def __repr__(self) -> str:
        return f"Post(shortcode={self.shortcode!r}, likes={self.likes}, taken_at_str={self.taken_at_str!r})"


@dataclass
class FeedTagResult:
    """Feed tag extraction result."""
    tags: List[str] = field(default_factory=list)
    tag_shortcodes: Dict[str, str] = field(default_factory=dict)
    tag_timestamps: Dict[str, str] = field(default_factory=dict)
    posts_checked: int = 0
    posts_with_tags: int = 0
    posts: List[InstagramPost] = field(default_factory=list)
    pages_fetched: int = 1            # How many API pages were fetched
    has_more_posts: bool = False      # More posts available beyond what was fetched

    def __post_init__(self) -> None:
        if self.posts_checked < 0:
            self.posts_checked = 0
        if self.posts_with_tags < 0:
            self.posts_with_tags = 0
        if self.pages_fetched < 1:
            self.pages_fetched = 1

    def __repr__(self) -> str:
        return f"FeedTagResult(tags={len(self.tags)}, posts_checked={self.posts_checked})"


@dataclass
class ProfileWithTags:
    """Profile + tags + status combined."""
    profile: Optional[InstagramProfile] = None
    feed_tags: FeedTagResult = field(default_factory=FeedTagResult)
    is_dead: bool = False
    last_post_days: int = 0
    found: bool = False


@dataclass
class TaggedPost:
    """Single post from the Tagged Tab — posted by SOMEONE ELSE who tagged this account."""
    shortcode: str = ""
    post_url: str = ""
    media_type: int = 0            # 1=image, 2=video, 8=carousel
    post_type: str = ""            # image | video | carousel
    poster_username: str = ""      # account that made this post
    poster_id: str = ""
    likes: int = 0
    comments: int = 0
    view_count: int = 0
    carousel_count: int = 0
    caption: str = ""
    display_url: str = ""
    width: int = 0
    height: int = 0
    taken_at: int = 0              # estimated from pk (may be 0 if unavailable)
    taken_at_str: str = ""

    def __post_init__(self) -> None:
        if self.likes < 0:
            self.likes = 0
        if self.comments < 0:
            self.comments = 0
        if self.view_count < 0:
            self.view_count = 0
        if self.carousel_count < 0:
            self.carousel_count = 0


@dataclass
class DateRange:
    """Unix timestamp range for filtering posts."""
    since: Optional[int] = None   # Unix timestamp
    until: Optional[int] = None   # Unix timestamp

    def contains(self, ts: int) -> bool:
        if self.since and ts < self.since:
            return False
        if self.until and ts > self.until:
            return False
        return True

    def is_before_range(self, ts: int) -> bool:
        """Returns True if ts is older than the since boundary."""
        return bool(self.since and ts < self.since)


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS DATA
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CacheStats:
    """Cache statistics."""
    enabled: bool = True
    total_entries: int = 0
    max_entries: int = 0
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    hit_rate: float = 0.0


@dataclass
class ProxyStatus:
    """Single proxy status."""
    url_masked: str = ""      # Hide password: http://***@host:port
    is_active: bool = True
    consecutive_fails: int = 0
    total_requests: int = 0
    total_success: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    cooldown_remaining_s: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC INPUT MODELS (MCP Tool Inputs)
# ═══════════════════════════════════════════════════════════════════════════════

def _clean_username(v: str) -> str:
    """Clean username — remove @ symbol and spaces."""
    return v.lstrip("@").strip().lower()


# ── Kept for backwards-compatibility with batch_runner internal usage ────────
class UsernameInput(BaseModel):
    """Input for a single username (legacy — use ProfileInput instead)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(..., description="Instagram username (without @).", min_length=1, max_length=30)

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return _clean_username(v)


# ── PRIMARY unified profile input — replaces 4 old models ───────────────────
class ProfileInput(BaseModel):
    """
    Input for instagram_profile tool.
    Controls depth: profile only, or profile + feed + activity check.
    """
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username without @. Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    include_feed: bool = Field(
        default=True,
        description=(
            "Fetch recent posts and extract tags, mentions, hashtags. "
            "Set False for fastest profile-only lookup (no post data)."
        ),
    )
    max_feed_posts: int = Field(
        default=12,
        description="Number of recent posts to analyse when include_feed=True. Range: 1-12.",
        ge=1,
        le=12,
    )
    max_age_days: int = Field(
        default=30,
        description="Ignore posts older than this many days when include_feed=True. Range: 1-365.",
        ge=1,
        le=365,
    )
    check_alive: bool = Field(
        default=True,
        description=(
            "Check if the account is active or dead. "
            "Returns last_post_days and status (active/dead). "
            "Ignored for private accounts."
        ),
    )
    dead_threshold_days: int = Field(
        default=365,
        description="Days without posts before account is marked dead. Range: 30-3650.",
        ge=30,
        le=3650,
    )
    since_timestamp: Optional[int] = Field(
        default=None,
        description="Filter feed posts after this Unix timestamp (optional).",
    )
    until_timestamp: Optional[int] = Field(
        default=None,
        description="Filter feed posts before this Unix timestamp (optional).",
    )
    since_date: str = Field(
        default="",
        description=(
            "Convenience alternative to since_timestamp. Accepts DD.MM.YYYY, "
            "YYYY-MM-DD or DD/MM/YYYY. Example: '01.03.2026'."
        ),
    )
    until_date: str = Field(
        default="",
        description="Convenience alternative to until_timestamp. Same formats as since_date.",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return _clean_username(v)

    def resolved_since(self) -> Optional[int]:
        """Return the effective `since` Unix timestamp (date string takes precedence)."""
        return _parse_user_date(self.since_date) if self.since_date else self.since_timestamp

    def resolved_until(self) -> Optional[int]:
        """Return the effective `until` Unix timestamp (date string takes precedence)."""
        return _parse_user_date(self.until_date) if self.until_date else self.until_timestamp


# Legacy aliases kept for internal compatibility
ProfileWithTagsInput = ProfileInput
FeedTagsInput = ProfileInput


class BulkProfilesInput(BaseModel):
    """Input for instagram_bulk_check tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    usernames: List[str] = Field(
        ...,
        description="Instagram usernames to check (without @). Up to 20.",
        min_length=1,
        max_length=20,
    )
    concurrency: int = Field(
        default=5,
        description="Parallel fetch count. Higher = faster but more likely to hit rate limits. Range: 1-20.",
        ge=1,
        le=20,
    )


# Legacy alias
AccountStatusInput = ProfileInput


class EngagementAnalysisInput(BaseModel):
    """Input for engagement rate analysis."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username (without @). Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    max_posts: int = Field(
        default=50,
        description="Number of posts to analyze (1-200). Default: 50",
        ge=1,
        le=200,
    )
    max_age_days: int = Field(
        default=90,
        description="Skip posts older than this many days (1-365). Default: 90",
        ge=1,
        le=365,
    )
    since_timestamp: Optional[int] = Field(
        default=None,
        description="Only include posts after this Unix timestamp (optional).",
    )
    until_timestamp: Optional[int] = Field(
        default=None,
        description="Only include posts before this Unix timestamp (optional).",
    )
    since_date: str = Field(
        default="",
        description=(
            "Filter posts after this date. Accepts DD.MM.YYYY, YYYY-MM-DD or DD/MM/YYYY. "
            "Example: '01.01.2026' to analyse only 2026 posts."
        ),
    )
    until_date: str = Field(
        default="",
        description="Filter posts before this date. Same formats as since_date.",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return _clean_username(v)

    def resolved_since(self) -> Optional[int]:
        return _parse_user_date(self.since_date) if self.since_date else self.since_timestamp

    def resolved_until(self) -> Optional[int]:
        return _parse_user_date(self.until_date) if self.until_date else self.until_timestamp


class CollabNetworkInput(BaseModel):
    """Input for collaboration network analysis."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username (without @). Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    max_posts: int = Field(
        default=50,
        description="Number of posts to analyze (1-200). Default: 50",
        ge=1,
        le=200,
    )
    max_age_days: int = Field(
        default=90,
        description="Skip posts older than this many days (1-365). Default: 90",
        ge=1,
        le=365,
    )
    min_frequency: int = Field(
        default=1,
        description="Minimum times a person must appear to be included. Default: 1",
        ge=1,
        le=50,
    )
    since_timestamp: Optional[int] = Field(
        default=None,
        description="Only include posts after this Unix timestamp (optional).",
    )
    until_timestamp: Optional[int] = Field(
        default=None,
        description="Only include posts before this Unix timestamp (optional).",
    )
    since_date: str = Field(
        default="",
        description=(
            "Filter posts after this date. Accepts DD.MM.YYYY, YYYY-MM-DD or DD/MM/YYYY. "
            "Example: '01.01.2026' to map collabs from 2026 only."
        ),
    )
    until_date: str = Field(
        default="",
        description="Filter posts before this date. Same formats as since_date.",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return _clean_username(v)

    def resolved_since(self) -> Optional[int]:
        return _parse_user_date(self.since_date) if self.since_date else self.since_timestamp

    def resolved_until(self) -> Optional[int]:
        return _parse_user_date(self.until_date) if self.until_date else self.until_timestamp


class CompareProfilesInput(BaseModel):
    """Input for comparing multiple profiles."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    usernames: List[str] = Field(
        ...,
        description="List of Instagram usernames to compare (2-5, without @).",
        min_length=2,
        max_length=5,
    )


class DeepFeedInput(BaseModel):
    """Input for instagram_feed_deep tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username without @. Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    max_posts: int = Field(
        default=50,
        description=(
            "Maximum posts to fetch and analyse. Range: 1-200. "
            "Uses v1/feed/user pagination (50 posts per page): "
            "100 posts ≈ 2 requests, 200 posts ≈ 4 requests."
        ),
        ge=1,
        le=200,
    )
    max_age_days: int = Field(
        default=30,
        description="Stop fetching when posts get older than this. Range: 1-365.",
        ge=1,
        le=365,
    )
    include_posts_detail: bool = Field(
        default=False,
        description="Include full post data: caption, hashtags, likes, comments, location, music.",
    )
    since_timestamp: Optional[int] = Field(
        default=None,
        description="Only include posts after this Unix timestamp.",
    )
    until_timestamp: Optional[int] = Field(
        default=None,
        description="Only include posts before this Unix timestamp.",
    )
    since_date: str = Field(
        default="",
        description=(
            "Convenience alternative to since_timestamp. Accepts DD.MM.YYYY, "
            "YYYY-MM-DD or DD/MM/YYYY. Example: '01.03.2026' for posts since March 1st 2026."
        ),
    )
    until_date: str = Field(
        default="",
        description="Convenience alternative to until_timestamp. Same formats as since_date.",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return _clean_username(v)

    def resolved_since(self) -> Optional[int]:
        return _parse_user_date(self.since_date) if self.since_date else self.since_timestamp

    def resolved_until(self) -> Optional[int]:
        return _parse_user_date(self.until_date) if self.until_date else self.until_timestamp


# Legacy alias
PaginatedFeedInput = DeepFeedInput


class TaggedByInput(BaseModel):
    """Input for instagram_tagged_by tool (AUTH REQUIRED)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username without @. Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    max_posts: int = Field(
        default=50,
        description=(
            "Maximum tagged posts to fetch (1-200). "
            "Each page = 12 posts = 1 authenticated API request."
        ),
        ge=1,
        le=200,
    )
    min_poster_followers: int = Field(
        default=0,
        description="Only include posts by accounts with at least this many followers (0 = no filter).",
        ge=0,
    )

    @field_validator("username")
    @classmethod
    def clean_tagged_username(cls, v: str) -> str:
        return _clean_username(v)


class RepostsInput(BaseModel):
    """Input for instagram_reposts tool (AUTH REQUIRED)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username without @. Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    max_posts: int = Field(
        default=50,
        description=(
            "Maximum repost items to fetch (1-200). "
            "Each page = 12 items = 1 authenticated API request."
        ),
        ge=1,
        le=200,
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return _clean_username(v)


class ReelsInput(BaseModel):
    """Input for instagram_reels tool (AUTH REQUIRED)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        ...,
        description="Instagram username without @. Example: 'nike'",
        min_length=1,
        max_length=30,
    )
    max_reels: int = Field(
        default=50,
        description=(
            "Maximum reels to fetch (1-200). "
            "Each page = 12 reels = 1 authenticated API request."
        ),
        ge=1,
        le=200,
    )

    @field_validator("username")
    @classmethod
    def clean_reels_username(cls, v: str) -> str:
        return _clean_username(v)


@dataclass
class RepostItem:
    """
    Single item from the Reposts Tab.

    Represents a post made by ANOTHER ACCOUNT that THIS account chose to repost.
    orig_username / orig_user_id  → who made the original content
    shortcode / post_url          → the original post link
    likes / comments              → engagement on the ORIGINAL post
    """
    shortcode: str = ""
    post_url: str = ""
    media_type: int = 0             # 1=image, 2=video, 8=carousel
    post_type: str = ""             # image | video | carousel
    product_type: str = ""          # "clips" for reels, "" otherwise
    orig_username: str = ""         # who originally posted this content
    orig_user_id: str = ""
    likes: int = 0
    comments: int = 0
    view_count: int = 0
    carousel_count: int = 0
    caption: str = ""
    display_url: str = ""
    width: int = 0
    height: int = 0
    taken_at: int = 0               # estimated from pk
    taken_at_str: str = ""

    def __post_init__(self) -> None:
        if self.likes < 0:
            self.likes = 0
        if self.comments < 0:
            self.comments = 0
        if self.view_count < 0:
            self.view_count = 0
        if self.carousel_count < 0:
            self.carousel_count = 0


@dataclass
class ReelItem:
    """
    Single reel from the account's Reels Tab.

    play_count is the primary metric — it is NOT available in instagram_feed_deep
    because the main feed API omits it. Only the Reels Tab endpoint returns it.
    view_count is always null in this API; play_count is the correct play metric.
    """
    shortcode: str = ""
    post_url: str = ""
    pk: str = ""
    play_count: int = 0       # PRIMARY metric — unique to Reels Tab endpoint
    like_count: int = 0
    comment_count: int = 0
    coauthor_ids: List[str] = field(default_factory=list)
    thumbnail_url: str = ""
    width: int = 0
    height: int = 0
    taken_at: int = 0
    taken_at_str: str = ""
    is_pinned: bool = False

    def __post_init__(self) -> None:
        if self.play_count < 0:
            self.play_count = 0
        if self.like_count < 0:
            self.like_count = 0
        if self.comment_count < 0:
            self.comment_count = 0


@dataclass
class CommentItem:
    """
    Single comment on an Instagram post.

    Fetched from /api/v1/media/{media_id}/comments/ — anonymous endpoint.
    is_caption=True marks the post's own caption (returned as a comment object
    by the API). GIF-only comments have text="" and has_gif=True.
    """
    pk: str = ""
    text: str = ""
    comment_index: int = 0          # sequential index in total comment list
    comment_like_count: int = 0
    child_comment_count: int = 0    # number of threaded replies
    created_at: int = 0
    created_at_str: str = ""
    username: str = ""
    user_id: str = ""
    full_name: str = ""
    is_verified: bool = False
    is_private: bool = False
    has_translation: bool = False   # Instagram detected non-English text
    has_gif: bool = False
    gif_url: str = ""
    is_caption: bool = False        # True = this is the post's own caption

    def __post_init__(self) -> None:
        if self.comment_like_count < 0:
            self.comment_like_count = 0
        if self.child_comment_count < 0:
            self.child_comment_count = 0


@dataclass
class PostLocation:
    """Geographic location tag on an Instagram post."""
    name: str = ""
    lat: float = 0.0
    lng: float = 0.0
    pk: str = ""
    maps_url: str = ""   # pre-built Google Maps link

    @property
    def has_location(self) -> bool:
        return bool(self.name or (self.lat and self.lng))


@dataclass
class PostInfo:
    """
    Full details for a single Instagram post, fetched by shortcode or URL.

    Obtained by parsing the post's public HTML page — no auth required.
    Fields that Instagram did not include in the page are left at their
    zero/empty defaults (never None, safe to format directly).
    """
    shortcode: str = ""
    post_url: str = ""
    media_type: int = 0          # 1=image, 2=video, 8=carousel
    post_type: str = ""          # image | video | carousel | reels
    product_type: str = ""       # "clips" = reels

    # Author
    username: str = ""
    user_id: str = ""
    full_name: str = ""
    is_verified: bool = False

    # Engagement
    likes: int = 0
    comments: int = 0
    view_count: int = 0
    play_count: int = 0
    carousel_count: int = 0

    # Content
    caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    usertags: List[str] = field(default_factory=list)   # tagged in photo/video

    # Media
    display_url: str = ""
    width: int = 0
    height: int = 0
    duration_secs: float = 0.0   # video / reel duration

    # Time
    taken_at: int = 0            # Unix timestamp (exact, from HTML)
    taken_at_str: str = ""       # "YYYY-MM-DD HH:MM UTC"

    # Location
    location: PostLocation = field(default_factory=PostLocation)

    # Collab
    coauthors: List[str] = field(default_factory=list)
    sponsor_tags: List[str] = field(default_factory=list)

    # Music (reels)
    music_artist: str = ""
    music_title: str = ""

    def __post_init__(self) -> None:
        if self.likes < 0:
            self.likes = 0
        if self.comments < 0:
            self.comments = 0


class PostInput(BaseModel):
    """Input for instagram_post tool (🌐 anonymous)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    post: str = Field(
        ...,
        description=(
            "Instagram post shortcode or full URL. All URL types supported:\n"
            "  'DXjuqH9nDVE'\n"
            "  'https://www.instagram.com/p/DXjuqH9nDVE/'\n"
            "  'https://www.instagram.com/reel/DXjuqH9nDVE/'\n"
            "  'https://www.instagram.com/tv/DXjuqH9nDVE/'"
        ),
        min_length=5,
    )

    @field_validator("post")
    @classmethod
    def extract_shortcode(cls, v: str) -> str:
        v = v.strip()
        # Full URL: extract shortcode from /p/, /reel/, or /tv/ paths
        m = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_\-]+)', v)
        if m:
            return m.group(1)
        # Bare shortcode: must be alphanumeric + - _
        if re.match(r'^[A-Za-z0-9_\-]{5,15}$', v):
            return v
        raise ValueError(f"Cannot extract a valid shortcode from: {v!r}")


class PostCommentsInput(BaseModel):
    """Input for instagram_post_comments tool (🌐 anonymous)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    post: str = Field(
        ...,
        description=(
            "Instagram post shortcode or full URL. Examples:\n"
            "  'DXjuqH9nDVE'\n"
            "  'https://www.instagram.com/p/DXjuqH9nDVE/'\n"
            "  'https://www.instagram.com/reel/DXjuqH9nDVE/'"
        ),
        min_length=5,
    )
    max_comments: int = Field(
        default=100,
        description="Maximum comments to fetch (1-500).",
        ge=1,
        le=500,
    )
    sort_order: str = Field(
        default="popular",
        description="'popular' (most-liked first) or 'recent' (chronological).",
    )

    @field_validator("post")
    @classmethod
    def extract_comments_shortcode(cls, v: str) -> str:
        v = v.strip()
        m = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_\-]+)', v)
        if m:
            return m.group(1)
        if re.match(r'^[A-Za-z0-9_\-]{5,15}$', v):
            return v
        raise ValueError(f"Cannot extract a valid shortcode from: {v!r}")

    @field_validator("sort_order")
    @classmethod
    def validate_comments_sort(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in ("popular", "recent") else "popular"


class ServerInput(BaseModel):
    """Input for instagram_server tool (diagnostics + cache)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    action: str = Field(
        default="status",
        description=(
            "Action to perform:\n"
            "  'status'      — show cache hit rate, proxy health, rate limiter state\n"
            "  'clear_cache' — flush ALL cached profiles and feeds (full reset)\n"
            "  'clear_user'  — flush cache for ONE user only (provide username=)"
        ),
    )
    username: str = Field(
        default="",
        description=(
            "Instagram username for action='clear_user'. "
            "Leave empty for 'status' or 'clear_cache'."
        ),
    )
