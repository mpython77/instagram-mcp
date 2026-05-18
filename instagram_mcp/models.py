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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
            from datetime import timezone as _tz
            return int(datetime.strptime(s, fmt).replace(tzinfo=_tz.utc).timestamp())
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
class StoryItem:
    pk: str
    shortcode: str
    taken_at: int
    taken_at_str: str
    expiring_at: int
    media_type: int           # 1=image, 2=video
    duration_secs: float      # 0.0 if image
    width: int
    height: int
    thumbnail_url: str        # best quality image URL
    caption: str
    accessibility_caption: str
    is_paid_partnership: bool
    can_reshare: bool
    can_reply: bool
    has_audio: bool
    mentions: List[str]       # usernames from mention stickers
    hashtags: List[str]       # from hashtag stickers
    linked_post_code: str     # from story_feed_media (post sticker)
    music_title: str
    music_artist: str


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
            "  'status'         — show cache hit rate, proxy health, rate limiter state\n"
            "  'clear_cache'    — flush ALL cached profiles and feeds (full reset)\n"
            "  'clear_user'     — flush cache for ONE user only (provide username=)\n"
            "  'reload_cookies' — reload cookies.txt/cookies.json from disk without restarting"
        ),
    )
    username: str = Field(
        default="",
        description=(
            "Instagram username for action='clear_user'. "
            "Leave empty for 'status' or 'clear_cache'."
        ),
    )


class HashtagInput(BaseModel):
    """Input for instagram_hashtag tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    tag: str = Field(
        description=(
            "Hashtag to search (without #). "
            "Example: 'football', 'photography', 'travel'."
        ),
    )
    max_posts: int = Field(
        default=30,
        ge=1,
        le=300,
        description=(
            "Maximum posts to return. "
            "🔐 Auth mode: up to 300 (30/page, paginated). "
            "🌐 Anon mode: always 12 regardless of this value."
        ),
    )

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, v: str) -> str:
        v = v.lstrip("#").strip().lower()
        if not v:
            raise ValueError("tag must not be empty")
        return v


class SearchInput(BaseModel):
    """Input for instagram_search tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    query: str = Field(
        description=(
            "Search keyword. Can be a username, full name, or hashtag topic. "
            "Example: 'cristiano', 'nike', 'football'."
        ),
    )
    context: str = Field(
        default="blended",
        description=(
            "What to search for. "
            "'blended' = users + hashtags (default). "
            "'user' = accounts only. "
            "'hashtag' = hashtags only."
        ),
    )

    @field_validator("query")
    @classmethod
    def clean_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v

    @field_validator("context")
    @classmethod
    def clean_context(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("blended", "user", "hashtag"):
            raise ValueError("context must be 'blended', 'user', or 'hashtag'")
        return v



class FollowersInput(BaseModel):
    """Input for instagram_followers_list tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        description="Instagram username (without @). Example: 'nike', 'cristiano'."
    )
    max_users: int = Field(
        default=50,
        ge=1,
        le=1000,
        description=(
            "Maximum followers to return. "
            "YOUR OWN account: full pagination (50/page), up to 1000. "
            "OTHER accounts: Instagram limits to ~50 regardless."
        ),
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.lstrip("@").strip().lower()
        if not v:
            raise ValueError("username must not be empty")
        return v


class FollowingInput(BaseModel):
    """Input for instagram_following_list tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        description="Instagram username (without @). Example: 'nike', 'adidas'."
    )
    max_users: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="Maximum number of following accounts to return (50 per page). Default 200.",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.lstrip("@").strip().lower()
        if not v:
            raise ValueError("username must not be empty")
        return v


class StoriesInput(BaseModel):
    username: str = Field(..., description="Instagram username without @.", min_length=1, max_length=30)

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not v:
            raise ValueError("username cannot be empty")
        return v


class HighlightsInput(BaseModel):
    username: str = Field(..., description="Instagram username without @.", min_length=1, max_length=30)
    max_highlights: int = Field(default=50, ge=1, le=200, description="Max highlights to return from tray (1-200).")
    include_media: bool = Field(default=False, description="Fetch media items inside each highlight. Requires extra API calls.")
    max_media_highlights: int = Field(default=3, ge=1, le=10, description="If include_media=True, fetch media for top N highlights (1-10).")

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not v:
            raise ValueError("username cannot be empty")
        return v


class LocationPostsInput(BaseModel):
    """Input for instagram_location_posts tool (AUTH REQUIRED)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    location_id: str = Field(
        default="",
        description=(
            "Instagram location ID (numeric). "
            "If empty, location_name is used for a search query. "
            "Example: '213385402' for New York."
        ),
    )
    location_name: str = Field(
        default="",
        description=(
            "Location name to search (if location_id is not provided). "
            "Example: 'Tashkent', 'Central Park New York'."
        ),
    )
    max_posts: int = Field(
        default=33,
        ge=1,
        le=100,
        description="Maximum posts to return (1-100). Default: 33.",
    )

    @field_validator("location_id", "location_name", mode="before")
    @classmethod
    def clean(cls, v: object) -> str:
        return (str(v) if v is not None else "").strip()


class AudioReelsInput(BaseModel):
    """Input for instagram_audio_reels tool (AUTH REQUIRED)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    audio_cluster_id: str = Field(
        description=(
            "Instagram audio cluster ID. "
            "Find it in a reel's clips_metadata.music_info or from the audio page URL. "
            "Example: '260841894490983'."
        ),
    )
    max_reels: int = Field(
        default=24,
        ge=1,
        le=100,
        description="Maximum reels to return (1-100). Default: 24.",
    )

    @field_validator("audio_cluster_id", mode="before")
    @classmethod
    def clean(cls, v: object) -> str:
        v_str = (str(v) if v is not None else "").strip()
        if not v_str:
            raise ValueError("audio_cluster_id must not be empty")
        return v_str


class PostLikersInput(BaseModel):
    """Input for instagram_post_likers tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    post: str = Field(
        description=(
            "Post shortcode or full URL. "
            "Example: 'DXUoQBqiCrY' or 'https://www.instagram.com/p/DXUoQBqiCrY/'."
        )
    )

    @field_validator("post")
    @classmethod
    def clean_post(cls, v: str) -> str:
        v = v.strip()
        if "/" in v:
            v = [p for p in v.rstrip("/").split("/") if p][-1]
        if not v:
            raise ValueError("post shortcode must not be empty")
        return v


# ═══════════════════════════════════════════════════════════════════════════════
# NEW TOOL INPUT MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class HashtagDeepInput(BaseModel):
    """Input for instagram_hashtag_deep tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    tag: str = Field(
        description=(
            "Hashtag to analyse (without #). "
            "Example: 'football', 'photography', 'travel'."
        ),
    )
    max_posts: int = Field(
        default=90,
        ge=1,
        le=500,
        description=(
            "Maximum posts to retrieve for analysis (up to 500). "
            "🔐 Auth mode: full pagination, 30 posts/page. "
            "🌐 Anon mode: capped at 12 regardless of this value."
        ),
    )
    top_n: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Number of top accounts to show in the ranking table.",
    )

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, v: str) -> str:
        v = v.lstrip("#").strip().lower()
        if not v:
            raise ValueError("tag must not be empty")
        return v


class PostBulkInput(BaseModel):
    """Input for instagram_post_bulk tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    shortcodes: List[str] = Field(
        ...,
        description=(
            "List of post shortcodes or full post URLs to fetch in parallel. "
            "Shortcode examples: 'DXjuqH9nDVE', 'C1abc123XYZ'. "
            "URL examples: 'https://www.instagram.com/p/DXjuqH9nDVE/'. "
            "Max 50 posts per call."
        ),
        min_length=1,
        max_length=50,
    )
    max_concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Parallel requests (1-20). Default 5 — safe for direct connections.",
    )

    @field_validator("shortcodes")
    @classmethod
    def clean_shortcodes(cls, v: List[str]) -> List[str]:
        cleaned = []
        for raw in v:
            sc = raw.strip()
            if "/" in sc:
                sc = [p for p in sc.rstrip("/").split("/") if p][-1]
            if sc:
                cleaned.append(sc)
        if not cleaned:
            raise ValueError("shortcodes list must contain at least one valid shortcode")
        return cleaned


class SimilarAccountsInput(BaseModel):
    """Input for instagram_similar_accounts tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        description="Instagram username to find similar accounts for (without @).",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Max number of similar accounts to return (1-50).",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.strip().lstrip("@").lower()
        if not v:
            raise ValueError("username must not be empty")
        return v


class NicheTopInput(BaseModel):
    """Input for instagram_niche_top tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    tag: str = Field(
        description=(
            "Hashtag that defines the niche (without #). "
            "Example: 'fitness', 'streetwear', 'foodphotography'."
        ),
    )
    max_posts: int = Field(
        default=90,
        ge=12,
        le=500,
        description=(
            "Number of hashtag posts to analyse (min 12, max 500). "
            "More posts → better account ranking accuracy. "
            "🔐 Auth: paginated. 🌐 Anon: capped at 12."
        ),
    )
    top_n: int = Field(
        default=15,
        ge=3,
        le=50,
        description="Number of top accounts to return.",
    )
    sort_by: str = Field(
        default="engagement",
        description=(
            "How to rank accounts. "
            "'engagement' = avg (likes+comments) per post (default). "
            "'post_count' = most posts in the hashtag. "
            "'total_likes' = highest total likes."
        ),
    )

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, v: str) -> str:
        v = v.lstrip("#").strip().lower()
        if not v:
            raise ValueError("tag must not be empty")
        return v

    @field_validator("sort_by")
    @classmethod
    def clean_sort(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("engagement", "post_count", "total_likes"):
            raise ValueError("sort_by must be 'engagement', 'post_count', or 'total_likes'")
        return v


class AccountReportInput(BaseModel):
    """Input for instagram_account_report tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(
        description="Instagram username (without @). Example: 'nike', 'cristiano'.",
    )
    max_posts: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Posts to fetch for engagement analysis (1-200). Default 50.",
    )
    include_collab: bool = Field(
        default=True,
        description="Include collaboration network (tags, mentions, sponsors). Adds one API call.",
    )

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.strip().lstrip("@").lower()
        if not v:
            raise ValueError("username must not be empty")
        return v


class DownloadInput(BaseModel):
    """Input for instagram_download tool (🔐 auth required)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    post: str = Field(
        ...,
        description=(
            "Instagram post shortcode or full URL. Supports all post types "
            "(image, video/reel, carousel). Examples:\n"
            "  'DXjuqH9nDVE'\n"
            "  'https://www.instagram.com/p/DXjuqH9nDVE/'\n"
            "  'https://www.instagram.com/reel/DXjuqH9nDVE/'"
        ),
        min_length=5,
    )
    save_dir: str = Field(
        default="/tmp",
        description=(
            "Absolute directory path where files will be saved. "
            "Default is /tmp. Directory must already exist."
        ),
    )

    @field_validator("post")
    @classmethod
    def extract_shortcode(cls, v: str) -> str:
        v = v.strip()
        m = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_\-]+)', v)
        if m:
            return m.group(1)
        if re.match(r'^[A-Za-z0-9_\-]{5,15}$', v):
            return v
        raise ValueError(f"Cannot extract a valid shortcode from: {v!r}")


class UploadPhotoInput(BaseModel):
    """Input for instagram_upload_photo tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    images: List[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description=(
            "List of absolute local file paths to upload (1–10 images). "
            "Supported formats: JPEG (.jpg/.jpeg) natively; PNG requires Pillow installed. "
            "For carousel posts provide 2–10 paths. "
            "Example: ['/tmp/photo.jpg'] or ['/tmp/img1.jpg', '/tmp/img2.jpg']"
        ),
    )
    caption: str = Field(
        default="",
        max_length=2200,
        description="Post caption (max 2200 characters). Supports @mentions and #hashtags.",
    )
    disable_comments: bool = Field(
        default=False,
        description="Disable comments on the post.",
    )
    hide_like_count: bool = Field(
        default=False,
        description="Hide the like count from viewers (owner can still see it).",
    )
    location_id: str = Field(
        default="",
        description=(
            "Optional Instagram location ID to tag the post. "
            "Get the ID from instagram_location_posts or from instagram_post details."
        ),
    )


# ── DM Tools ─────────────────────────────────────────────────────────────────

class DMInboxInput(BaseModel):
    """Input for instagram_dm_inbox tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Number of threads to return (1-50). Default 20.",
    )
    cursor: str = Field(
        default="",
        description="Pagination cursor from a previous call's oldest_cursor field.",
    )


class DMThreadInput(BaseModel):
    """Input for instagram_dm_thread tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    thread_id: str = Field(
        ...,
        description="Thread ID from instagram_dm_inbox results.",
        min_length=1,
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of messages to return (1-100). Default 20.",
    )
    cursor: str = Field(
        default="",
        description="Pagination cursor (prev_cursor from previous result) to load older messages.",
    )


class DMSendInput(BaseModel):
    """Input for instagram_dm_send tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    thread_id: Optional[str] = Field(
        default=None,
        description="Thread igid from instagram_dm_inbox. Use to reply to an existing thread.",
        min_length=1,
    )
    username: Optional[str] = Field(
        default=None,
        description="Instagram username (e.g. 'cristiano'). Resolves thread automatically.",
        min_length=1,
    )
    text: str = Field(
        ...,
        description="Message text to send (max 1000 characters).",
        min_length=1,
        max_length=1000,
    )

    @model_validator(mode="after")
    def require_thread_or_username(self) -> "DMSendInput":
        if not self.thread_id and not self.username:
            raise ValueError("Provide either thread_id or username.")
        return self


# ── Schedule Tool ─────────────────────────────────────────────────────────────

class ScheduleInput(BaseModel):
    """Input for instagram_schedule tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    action: str = Field(
        ...,
        description=(
            "Action to perform:\n"
            "  'add'    — schedule a new post\n"
            "  'list'   — show pending scheduled posts\n"
            "  'cancel' — cancel a scheduled post by ID\n"
            "  'status' — show scheduler health"
        ),
    )
    images: List[str] = Field(
        default_factory=list,
        description="[add only] Absolute paths to images to post (1-10).",
    )
    caption: str = Field(
        default="",
        max_length=2200,
        description="[add only] Post caption (max 2200 chars).",
    )
    publish_at: str = Field(
        default="",
        description=(
            "[add only] When to publish. Accepts:\n"
            "  ISO format: '2026-05-20T15:00:00'\n"
            "  Date only: '2026-05-20' (publishes at midnight UTC)\n"
            "  Unix timestamp as string: '1716220800'"
        ),
    )
    post_id: str = Field(
        default="",
        description="[cancel only] The 8-char post ID returned by 'add' action.",
    )
    location: str = Field(
        default="",
        description="[add only] Optional location string to tag in the post.",
    )


# ── Monitor Tool ──────────────────────────────────────────────────────────────

class MonitorInput(BaseModel):
    """Input for instagram_monitor tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    action: str = Field(
        ...,
        description=(
            "Action to perform:\n"
            "  'add'    — start monitoring an account\n"
            "  'remove' — stop monitoring an account\n"
            "  'list'   — show all active monitors\n"
            "  'status' — show monitor service health\n"
            "  'test'   — send a test webhook"
        ),
    )
    username: str = Field(
        default="",
        description="[add/remove only] Instagram username to monitor (without @).",
    )
    webhook_url: str = Field(
        default="",
        description=(
            "[add/test only] HTTPS URL to POST new-post notifications to.\n"
            "Payload: {event, username, shortcode, post_url, caption, likes, timestamp, detected_at}"
        ),
    )
    interval: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="[add only] Polling interval in seconds (60-3600). Default 300 (5 min).",
    )


# ── OAuth Tool ────────────────────────────────────────────────────────────────

class OAuthInput(BaseModel):
    """Input for instagram_oauth tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    action: str = Field(
        ...,
        description=(
            "Action to perform:\n"
            "  'init_flow'    — generate the OAuth authorization URL to visit\n"
            "  'exchange_code'— exchange the callback code for a token\n"
            "  'refresh_token'— refresh the long-lived token before expiry\n"
            "  'status'       — show current token status"
        ),
    )
    code: str = Field(
        default="",
        description="[exchange_code only] The 'code' parameter from the OAuth callback URL.",
    )
    scopes: List[str] = Field(
        default_factory=list,
        description=(
            "[init_flow only] OAuth scopes to request. Defaults to "
            "['instagram_business_basic', 'instagram_business_manage_messages']."
        ),
    )


# ── DM Actions ────────────────────────────────────────────────────────────────

class DMReactInput(BaseModel):
    """Input for instagram_dm_react tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    thread_id: str = Field(..., description="Thread ID from instagram_dm_inbox.", min_length=1)
    item_id: str = Field(..., description="Message item_id to react to.", min_length=1)
    emoji: str = Field(default="❤", description="Emoji reaction (default: ❤). Empty string to remove.")
    action: str = Field(default="react", description="'react' to add reaction, 'unreact' to remove.")


class DMUnsendInput(BaseModel):
    """Input for instagram_dm_unsend tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    thread_id: str = Field(..., description="Thread ID from instagram_dm_inbox.", min_length=1)
    item_id: str = Field(..., description="Message item_id to delete/unsend.", min_length=1)


class DMMarkSeenInput(BaseModel):
    """Input for instagram_dm_mark_seen tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    thread_id: str = Field(..., description="Thread ID to mark as seen.", min_length=1)
    item_id: str = Field(..., description="item_id of the last message to mark as seen.", min_length=1)


# ── Post Actions ───────────────────────────────────────────────────────────────

class PostCommentInput(BaseModel):
    """Input for instagram_post_comment tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    media_id: str = Field(..., description="Post media_id (numeric, e.g. '3612076889987614897').", min_length=1)
    text: str = Field(..., description="Comment text to post.", min_length=1, max_length=2200)


class PostSaveInput(BaseModel):
    """Input for instagram_post_save tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    media_id: str = Field(..., description="Post media_id (numeric) to save or unsave.", min_length=1)
    action: str = Field(default="save", description="'save' to bookmark, 'unsave' to remove bookmark.")


# ── User Actions ───────────────────────────────────────────────────────────────

class UserSearchInput(BaseModel):
    """Input for instagram_user_search tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    query: str = Field(..., description="Search query (username or name).", min_length=1)
    count: int = Field(default=10, ge=1, le=50, description="Number of results (1-50). Default 10.")


class UserFollowersInput(BaseModel):
    """Input for instagram_user_followers/following tools."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    user_id: str = Field(..., description="Numeric user ID.", min_length=1)
    count: int = Field(default=50, ge=1, le=200, description="Number of users per page (1-200). Default 50.")
    max_id: str = Field(default="", description="Pagination cursor (next_max_id from previous result).")


class BlockUserInput(BaseModel):
    """Input for instagram_block_user tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    user_id: str = Field(..., description="Numeric user ID to block or unblock.", min_length=1)
    action: str = Field(default="block", description="'block' or 'unblock'.")


class LikePostInput(BaseModel):
    """Input for instagram_post_like tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    media_id: str = Field(..., description="Numeric post media_id to like or unlike.", min_length=1)
    action: str = Field(default="like", description="'like' or 'unlike'.")


class FollowUserInput(BaseModel):
    """Input for instagram_follow_user tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    user_id: str = Field(..., description="Numeric user ID to follow or unfollow.", min_length=1)
    action: str = Field(default="follow", description="'follow' or 'unfollow'.")


# ── Story Actions ──────────────────────────────────────────────────────────────

class StoryMarkSeenInput(BaseModel):
    """Input for instagram_story_mark_seen tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    reel_ids: List[str] = Field(
        ...,
        description=(
            "List of story item IDs (media_id) to mark as seen. "
            "Get these from instagram_stories tool."
        ),
        min_length=1,
    )
    owner_ids: List[str] = Field(
        ...,
        description="List of owner user IDs corresponding to each reel_id.",
        min_length=1,
    )
    taken_ats: List[int] = Field(
        ...,
        description="List of taken_at timestamps (Unix seconds) for each story.",
        min_length=1,
    )


class StoryReplyInput(BaseModel):
    """Input for instagram_story_reply tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(..., description="Story owner's Instagram username.", min_length=1)
    text: str = Field(..., description="Reply message text.", min_length=1, max_length=1000)


# ── Profile Edit ───────────────────────────────────────────────────────────────

class EditProfileInput(BaseModel):
    """Input for instagram_edit_profile tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    biography: Optional[str] = Field(default=None, description="New bio text (max 150 chars).", max_length=150)
    full_name: Optional[str] = Field(default=None, description="New display name.", max_length=30)
    external_url: Optional[str] = Field(default=None, description="New website URL.")
    email: Optional[str] = Field(default=None, description="New email address.")
    phone_number: Optional[str] = Field(default=None, description="New phone number.")


# ── Session Tool ──────────────────────────────────────────────────────────────

class SessionInput(BaseModel):
    """Input for instagram_sessions tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    action: str = Field(
        default="list",
        description=(
            "Action to perform:\n"
            "  'list' — show all loaded sessions and their auth status\n"
            "  'status' — same as list but with more detail"
        ),
    )
