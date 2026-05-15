# instagram-mcp

**Version 1.0.0** | Python 3.10+ | MIT License

A production-grade MCP (Model Context Protocol) server for Instagram intelligence. Provides 22 tools across two auth tiers: 11 tools run fully anonymously with no credentials; 11 tools require a `cookies.txt` session file. Built on `curl_cffi` with Chrome TLS impersonation, adaptive rate limiting, smart caching, and automatic JSON export.

Works natively with Claude Desktop, Claude Code, and any MCP-compatible AI client.

---

## Table of Contents

1. [Tool Overview](#tool-overview)
2. [Quick Start](#quick-start)
3. [Tool Reference — Anonymous](#tool-reference--anonymous-)
4. [Tool Reference — Authenticated](#tool-reference--authenticated-)
5. [Architecture](#architecture)
6. [Data Models](#data-models)
7. [Authentication](#authentication)
8. [Configuration](#configuration)
9. [JSON Auto-Export](#json-auto-export)
10. [Tool Decision Guide](#tool-decision-guide)
11. [Proxy Setup](#proxy-setup)
12. [Connecting to Claude Desktop](#connecting-to-claude-desktop)
13. [Limitations](#limitations)
14. [FAQ](#faq)

---

## Tool Overview

| # | Tool | Auth | Description |
|---|------|------|-------------|
| 1 | `instagram_profile` | 🌐 | Profile metadata + optional feed tags (up to 12 posts) + activity status |
| 2 | `instagram_feed_deep` | 🌐 | Paginated feed analysis up to 200 posts |
| 3 | `instagram_analyze_engagement` | 🌐 | ER%, content mix, best posting days, top posts |
| 4 | `instagram_find_collab_network` | 🌐 | Maps usertags, @mentions, co-authors, paid sponsors |
| 5 | `instagram_compare_profiles` | 🌐 | Side-by-side comparison of 2–5 accounts, parallel fetch |
| 6 | `instagram_bulk_check` | 🌐 | Up to 20 profiles in parallel with status |
| 7 | `instagram_batch_scrape` | 🌐 | Large-scale scraping up to 500 profiles with date filtering |
| 8 | `instagram_server` | 🌐 | Diagnostics + cache management (status/clear_cache/clear_user) |
| 9 | `instagram_post` | 🌐 | Full details for one post (shortcode or URL): GPS, timestamp, caption, hashtags, music |
| 10 | `instagram_post_comments` | 🌐 | Comments with per-comment likes, threading, GIF detection |
| 11 | `instagram_hashtag` | 🌐/🔐 | AUTO-MODE: anon=12 posts, auth=up to 300 posts with likes/plays |
| 12 | `instagram_search` | 🔐 | Search users and hashtags by keyword (blended/user/hashtag context) |
| 13 | `instagram_followers_list` | 🔐 | Recent followers (~50), mutual follow status |
| 14 | `instagram_following_list` | 🔐 | Full following list with pagination up to 1000, is_favorite detection |
| 15 | `instagram_post_likers` | 🔐 | Users who liked a post (~98), full friendship_status per liker |
| 16 | `instagram_tagged_by` | 🔐 | Posts BY OTHERS that tag this account (Tagged Tab) |
| 17 | `instagram_reposts` | 🔐 | Content this account actively reposted from others |
| 18 | `instagram_reels` | 🔐 | Account's own reels with PLAY COUNTS (unavailable in feed_deep) |
| 19 | `instagram_stories` | 🔐 | Active Stories: music, mentions, hashtags, linked posts, polls, link stickers |
| 20 | `instagram_highlights` | 🔐 | Highlights tray + optional full media fetch; boomerang/selfie detection |
| 21 | `instagram_location_posts` | 🔐 | Top posts at a location by name or ID |
| 22 | `instagram_audio_reels` | 🔐 | Reels using a specific audio track by audio_cluster_id |

**🌐 = anonymous** (no cookies needed) | **🔐 = authenticated** (requires cookies.txt)

---

## Quick Start

### Anonymous mode (no login required)

```bash
# 1. Clone and install
git clone <repo_url>
cd instagram_mcp
pip install -e .

# 2. Run the MCP server
instagram-mcp

# 3. Call any anonymous tool
# Example: profile + recent posts
instagram_profile username=nike
```

### Authenticated mode

```bash
# Export cookies.txt from your browser (see Authentication section)
# Place it next to the server or set the path via env var

INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt instagram-mcp

# Now all 22 tools are available
instagram_hashtag tag=football max_posts=300
instagram_reels username=nike max_reels=100
instagram_stories username=nike
```

### Using with `uv`

```bash
uv sync
uv run instagram-mcp
```

---

## Tool Reference — Anonymous 🌐

### `instagram_profile`

Fetch a public account's profile data. Controls depth via flags.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `include_feed` | bool | `true` | Fetch recent posts and extract tags/mentions/hashtags |
| `max_feed_posts` | int | `12` | Posts to analyse when include_feed=True (1–12) |
| `max_age_days` | int | `30` | Ignore posts older than N days (1–365) |
| `check_alive` | bool | `true` | Return last_post_days and active/dead status |
| `dead_threshold_days` | int | `365` | Days without posts to mark account dead (30–3650) |
| `since_date` | str | `""` | Filter posts after date: DD.MM.YYYY / YYYY-MM-DD / DD/MM/YYYY |
| `until_date` | str | `""` | Filter posts before date: same formats |

**Modes (via flag combinations):**

| Mode | include_feed | check_alive | Use case |
|------|-------------|-------------|----------|
| Full (default) | true | true | Profile + feed tags + activity status |
| Status check | false | true | Fastest: is account active/dead/private? |
| Profile only | false | false | Bio, followers, category — single API call |
| Tags only | true | false | Post tags without dead-account check |

**Example output (profile + feed):**
```
@nike (Nike) · Verified · Business
Followers: 306.4M | Following: 117 | Posts: 1,472
Category: Sportswear Store | Website: nike.com
Bio: Just Do It.

Feed Analysis (12 posts, last 30 days)
Status: ACTIVE — last post 2 days ago
Tags found: @lebron, @serena, #justdoit, @nikefootball
Posts with tags: 8/12
```

**Use cases:** Brand research, influencer vetting, activity monitoring, collab discovery.

---

### `instagram_feed_deep`

Paginated feed analysis using the v1/feed/user API. Fetches up to 200 posts across multiple pages.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to fetch (1–200, ~50 per API page) |
| `max_age_days` | int | `30` | Stop fetching when posts exceed this age (1–365) |
| `include_posts_detail` | bool | `false` | Include full caption, hashtags, likes, location, music per post |
| `since_date` | str | `""` | Filter posts after date |
| `until_date` | str | `""` | Filter posts before date |

**Note:** For play counts on reels, use `instagram_reels` — the feed API does not expose `play_count`.

**Example:**
```
Feed: @cristiano (200 posts, pages: 4)
Status: ACTIVE — last post 1 day ago
Post types: 68 reels, 112 images, 20 carousels
Date range: 2024-01-15 → 2026-05-14
Tags: @georgina, @siu, #cr7, @binance (all appearances)
```

---

### `instagram_analyze_engagement`

Calculates ER% and detailed content analytics across up to 200 posts.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to analyse (1–200) |
| `max_age_days` | int | `90` | Skip posts older than N days (1–365) |
| `since_date` | str | `""` | Filter posts after date |
| `until_date` | str | `""` | Filter posts before date |

**Engagement Rate formula:** `ER% = (avg_likes + avg_comments) / followers × 100`

**Output includes:**
- ER% with rating (excellent ≥6%, good ≥3%, average ≥1%, low <1%)
- Content mix breakdown (image/video/reel/carousel percentages)
- Best posting days by average engagement
- Top 5 posts by likes, top 5 by comments
- Posting frequency (posts per week)

---

### `instagram_find_collab_network`

Maps every person who appears across recent posts — usertags (people in photos), @mentions (caption text), co-authors (joint posts), and paid sponsors (partnership tags).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to scan (1–200) |
| `max_age_days` | int | `90` | Skip posts older than N days |
| `min_frequency` | int | `1` | Minimum appearances to include (1–50) |
| `since_date` / `until_date` | str | `""` | Date filters |

**Output includes:**
- Ranked collaborator list with appearance counts per category (tag/mention/coauthor/sponsor)
- Post-level breakdown showing which collaborator appeared in which post
- Frequency heatmap across date range

---

### `instagram_compare_profiles`

Side-by-side comparison of 2–5 accounts fetched in parallel.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `usernames` | list[str] | required | 2–5 usernames to compare (without @) |

**Compared fields:** followers, following, posts count, ER%, verification status, account type, category, website, posting frequency, content mix.

---

### `instagram_bulk_check`

Check up to 20 profiles in parallel with status classification.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `usernames` | list[str] | required | Up to 20 usernames (without @) |
| `concurrency` | int | `5` | Parallel fetch count (1–20) |

**Status values:** `active`, `dead` (no posts in 365 days), `private`, `not_found`.

---

### `instagram_batch_scrape`

Large-scale scraping for up to 500 profiles. Writes results to a JSON file with progress tracking.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `targets` | list[str] | required | Up to 500 usernames (without @) |
| `since_date` | str | `""` | Include only posts after this date |
| `until_date` | str | `""` | Include only posts before this date |
| `max_workers` | int | `10` | Parallel workers (1–20) |
| `max_posts_per_profile` | int | `50` | Max posts per profile (1–500) |
| `use_cookies` | bool | `false` | Use authenticated session |
| `output_file` | str | `""` | Custom output path (auto-generated if empty) |

**Output:** Real-time progress via MCP progress events. Final JSON with stats block (total, completed, active, dead, private, not_found, rate/s) and per-profile data array.

---

### `instagram_server`

Server diagnostics and cache control.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | str | `"status"` | `status`, `clear_cache`, or `clear_user` |
| `username` | str | `""` | Target username for `clear_user` action |

**`status` returns:** Cache hit rate, entry count, proxy health (per-proxy: success rate, avg latency, cooldown remaining), rate limiter state (current RPS, circuit breaker status).

---

### `instagram_post`

Full details for a single post by shortcode or URL. Parses public HTML — no auth required.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Shortcode (`DXjuqH9nDVE`) or full URL (`/p/`, `/reel/`, `/tv/`) |

**Returns:** post_type, author, likes, comments, view_count, play_count, caption, hashtags, mentions, usertags, coauthors, sponsor_tags, music_artist, music_title, location (name + GPS lat/lng + Google Maps URL), timestamp, carousel_count, duration_secs.

**Example input formats:**
```
DXjuqH9nDVE
https://www.instagram.com/p/DXjuqH9nDVE/
https://www.instagram.com/reel/DXjuqH9nDVE/
https://www.instagram.com/tv/DXjuqH9nDVE/
```

---

### `instagram_post_comments`

Fetch comments with engagement metrics and thread structure.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Shortcode or URL |
| `max_comments` | int | `100` | Maximum comments (1–500) |
| `sort_order` | str | `"popular"` | `popular` (most-liked first) or `recent` (chronological) |

**Per comment:** username, text, comment_like_count, child_comment_count (threaded replies), created_at, is_verified, has_gif (GIF-only comments), is_caption (post's own caption is returned as first item).

---

### `instagram_hashtag`

Fetch top posts for a hashtag. Automatically uses auth mode when cookies are present.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tag` | str | required | Hashtag without # (e.g., `football`, `travel`) |
| `max_posts` | int | `30` | Max posts: anon always returns 12; auth returns up to 300 |

**Anonymous mode:** Parses public HTML → 12 posts, includes related_searches (Instagram's suggested hashtags).

**Auth mode:** Uses `i.instagram.com/api/v1/tags/{tag}/sections/` with pagination → up to 300 posts with play_count and like_count per post.

---

## Tool Reference — Authenticated 🔐

All tools in this section require a valid `cookies.txt` file. Set the path via `INSTAGRAM_MCP_COOKIES` env var or place `cookies.txt` in the project root.

---

### `instagram_search`

Search Instagram by keyword, returning users and/or hashtags.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Search keyword (username, full name, or topic) |
| `context` | str | `"blended"` | `blended` (users + hashtags), `user` (accounts only), `hashtag` (hashtags only) |

**Returns:** User list (username, full_name, follower_count_text, is_verified, is_private, category) and hashtag list (name, media_count, subtitle), has_more flag.

---

### `instagram_followers_list`

Fetch recent followers with mutual follow status.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_users` | int | `50` | Max followers to return (1–1000) |

**Note:** Instagram limits third-party follower access to ~50 users regardless of `max_users`. Full pagination (up to 1000) only works on your own authenticated account.

**Per user:** username, full_name, is_verified, is_private, follower_count, you_follow_them, they_follow_you.

---

### `instagram_following_list`

Fetch the full following list with favorite detection and pagination.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_users` | int | `200` | Max following accounts (1–1000, 50 per page) |

**Per user:** username, full_name, is_verified, is_private, is_favorite (marked as Close Friend by the auth account), latest_reel_media.

---

### `instagram_post_likers`

Fetch users who liked a post with full friendship status per liker.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Post shortcode or URL |

**Returns:** Up to ~98 likers. Per user: username, full_name, is_verified, is_private, you_follow_them, they_follow_you, user_count (total likes on post).

---

### `instagram_tagged_by`

Fetch posts made by OTHER accounts that tag this account (the Tagged tab).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Account to check (without @) |
| `max_posts` | int | `50` | Max tagged posts (1–200, 12 per page) |
| `min_poster_followers` | int | `0` | Filter out taggers with fewer followers |

**Per post:** poster_username, poster_id, shortcode, post_type, likes, comments, view_count, caption, taken_at.

---

### `instagram_reposts`

Fetch content that an account has actively reposted from other creators.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Max repost items (1–200, 12 per page) |

**Per item:** orig_username (original creator), shortcode, post_type, product_type (`clips` = reel), likes, comments, view_count, caption, taken_at.

---

### `instagram_reels`

Fetch an account's reels with play counts. Play counts are NOT available via `instagram_feed_deep` — this is the only tool that returns them.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_reels` | int | `50` | Max reels (1–200, 12 per page) |

**Per reel:** shortcode, play_count (primary metric), like_count, comment_count, taken_at, is_pinned, coauthor_ids. Uses GraphQL `PolarisProfileReelsTabContentQuery_connection`.

---

### `instagram_stories`

Fetch an account's active Stories (expires after 24h).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |

**Per story item:** media_type (1=image/2=video), duration_secs, mentions (from mention stickers), hashtags (from hashtag stickers), linked_post_code (post sticker), music_title, music_artist, link_stickers, polls (question + vote tallies), is_paid_partnership, capture_type (boomerang/selfie), camera_facing (front/back), expiring_at.

---

### `instagram_highlights`

Fetch an account's Highlights tray and optionally the media inside each highlight.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_highlights` | int | `50` | Max highlights from tray (1–200) |
| `include_media` | bool | `false` | Fetch media items inside highlights (extra API calls) |
| `max_media_highlights` | int | `3` | If include_media=True, fetch media for top N highlights (1–10) |

**Per highlight:** id, title, media_count, created_at, latest_reel_media, highlight_reel_type, is_pinned, is_archived, cover_url. When include_media=True, each highlight also has items[] with boomerang/selfie detection.

---

### `instagram_location_posts`

Fetch top posts at a geographic location by Instagram location ID or name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `location_id` | str | `""` | Numeric Instagram location ID (preferred) |
| `location_name` | str | `""` | Location name to search if ID not provided |
| `max_posts` | int | `33` | Max posts to return (1–100) |

**Endpoint:** `i.instagram.com/api/v1/locations/{id}/sections/`

**Per post:** shortcode, username, media_type, like_count, play_count, taken_at_str, is_verified.

**How to find location_id:** Visit `instagram.com/explore/locations/{id}/` in a browser, or extract from any post's location data using `instagram_post`.

---

### `instagram_audio_reels`

Fetch all reels using a specific audio track, identified by `audio_cluster_id`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `audio_cluster_id` | str | required | Instagram audio cluster ID (numeric string) |
| `max_reels` | int | `24` | Max reels to return (1–100) |

**Endpoint:** `i.instagram.com/api/v1/clips/music/`

**Per reel:** shortcode, username, like_count, play_count, taken_at_str, is_verified.

**How to find audio_cluster_id:** Extract from a reel's `clips_metadata.music_info.audio_cluster_id` field, or from the audio page URL on Instagram.

---

## Architecture

### Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                     MCP CLIENT (Claude / AI)                    │
└─────────────────────┬───────────────────────────────────────────┘
                      │ MCP Protocol (stdio / HTTP-SSE)
┌─────────────────────▼───────────────────────────────────────────┐
│                  FastMCP Server  (mcp.server.fastmcp)           │
│                                                                 │
│  tools.py ── 22 tool handlers (async, ctx: Context)             │
│  models.py ── Pydantic input validation + dataclasses           │
│  formatter.py ── Markdown output generation                     │
│  exporter.py ── AI-optimised JSON auto-save                     │
└──────┬──────────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│                   InstagramClient (client.py)                   │
│                                                                 │
│  fetch_user()          fetch_feed_items()    fetch_stories()    │
│  fetch_comments()      fetch_reels()         fetch_highlights() │
│  fetch_hashtag()       fetch_followers()     fetch_following()  │
│  fetch_location_posts() fetch_audio_reels()  fetch_post()      │
│  fetch_search()        fetch_post_likers()   ...               │
│                                                                 │
│  _get_session()  ──── SmartCache ──── AdaptiveRateLimiter       │
│  _get_auth_session()── CookieManager ─ ProxyManager             │
└──────┬──────────────────────────────────────────────────────────┘
       │  curl_cffi AsyncSession (Chrome impersonation)
┌──────▼──────────────────────────────────────────────────────────┐
│                    Instagram APIs                               │
│                                                                 │
│  www.instagram.com/api/v1/   ── profile, feed, post, comments  │
│  i.instagram.com/api/v1/     ── hashtag, location, audio, auth │
│  www.instagram.com/graphql/  ── reels tab (GraphQL doc_id)     │
│  www.instagram.com/api/v1/   ── stories, highlights, search    │
└─────────────────────────────────────────────────────────────────┘
```

### Request Lifecycle

1. **Tool handler** validates input via Pydantic, calls `client.fetch_*()`.
2. **SmartCache** checks for a cached response (endpoint-specific TTL).
3. **AdaptiveRateLimiter** enforces token-bucket rate limiting with jitter; auto-backs off on 429, opens circuit breaker after 5 consecutive 429s.
4. **ProxyManager** selects the next healthy proxy (round-robin); falls back to direct if all proxies fail.
5. **curl_cffi AsyncSession** makes the request with Chrome TLS fingerprint. Two session types: `_get_session()` (anonymous pool, proxy-aware) and `_get_auth_session()` (single authenticated session with cookies).
6. On success: response cached, rate limiter recovery applied, result returned.
7. On 429: rate multiplied by 0.7, proxy rotated, retry (up to `max_retries`).
8. **Formatter** converts result to Markdown for MCP response.
9. **JsonExporter** asynchronously saves the result to `exports/{tool}/{subject}_{timestamp}.json`.

### Endpoint Table

| Tool | Endpoint | Method | Auth |
|------|----------|--------|------|
| `instagram_profile` | `www.instagram.com/api/v1/users/web_profile_info/` | GET | No |
| `instagram_feed_deep` | `www.instagram.com/api/v1/feed/user/{pk}/` | GET | No |
| `instagram_post` | `www.instagram.com/p/{shortcode}/` (HTML parse) | GET | No |
| `instagram_post_comments` | `www.instagram.com/api/v1/media/{media_id}/comments/` | GET | No* |
| `instagram_hashtag` (anon) | `www.instagram.com/explore/tags/{tag}/` (HTML) | GET | No |
| `instagram_hashtag` (auth) | `i.instagram.com/api/v1/tags/{tag}/sections/` | POST | Yes |
| `instagram_search` | `www.instagram.com/api/v1/fbsearch/web/top_serp/` | GET | Yes |
| `instagram_stories` | `www.instagram.com/api/v1/feed/user/{pk}/story/` | GET | Yes |
| `instagram_highlights` (tray) | `www.instagram.com/api/v1/highlights/{pk}/highlights_tray/` | GET | Yes |
| `instagram_highlights` (media) | `www.instagram.com/api/v1/feed/reels_media/?reel_ids=highlight:{id}` | GET | Yes |
| `instagram_followers_list` | `www.instagram.com/api/v1/friendships/{pk}/followers/` | GET | Yes |
| `instagram_following_list` | `www.instagram.com/api/v1/friendships/{pk}/following/` | GET | Yes |
| `instagram_post_likers` | `www.instagram.com/api/v1/media/{media_id}/likers/` | GET | Yes |
| `instagram_tagged_by` | `www.instagram.com/api/v1/usertags/{pk}/feed/` | GET | Yes |
| `instagram_reposts` | `www.instagram.com/api/v1/repost/user_repost_feed/` | GET | Yes |
| `instagram_reels` | `www.instagram.com/graphql/query` (PolarisProfileReelsTab) | POST | Yes |
| `instagram_location_posts` | `i.instagram.com/api/v1/locations/{id}/sections/` | POST | Yes |
| `instagram_audio_reels` | `i.instagram.com/api/v1/clips/music/` | POST | Yes |

\* Comments endpoint works anonymously for public posts but may require auth for some accounts.

### Cache TTLs

| Endpoint | TTL | Rationale |
|----------|-----|-----------|
| Profile | 300s | Bio/follower counts change infrequently |
| Feed (per page) | 180s | New posts appear every few hours |
| Stories | 120s | Stories expire and update frequently |
| Highlights | 300s | Highlights change infrequently |
| Hashtag | 300s | Top posts rotate slowly |
| Location posts | 300s | Top posts rotate slowly |
| Audio reels | 300s | Track usage changes slowly |
| Comments | 60s | New comments arrive constantly |
| Search | 60s | Results personalised and volatile |
| Following/Followers | 120s | Follow graph changes frequently |
| Tagged (usertags) | 300s | Tagged posts accumulate slowly |
| Reposts | 300s | Repost tab changes infrequently |
| Reels tab | 300s | New reels appear every few days |

### Key Design Decisions

- **`curl_cffi` with Chrome impersonation** bypasses TLS fingerprinting; Instagram blocks `requests` and `aiohttp` by fingerprint.
- **Two session types:** `_get_session()` for anonymous requests (proxy pool, no cookies) and `_get_auth_session()` for authenticated requests (single session with cookies, avoids session contamination).
- **`i.instagram.com`** is used for hashtag sections, location sections, and audio reels — these endpoints require `ig_user_agent` + `x-ig-app-id` headers and are not exposed on `www.instagram.com`.
- **GraphQL** is used only for the reels tab (`PolarisProfileReelsTabContentQuery_connection`) because the REST API does not expose play counts.
- **All tool handlers are async** with `ctx: Context` for MCP-native progress reporting and structured logging.
- **`ToolError` for all errors** — never raises Python exceptions; always returns `isError=true` in the MCP protocol response with an LLM-readable message and suggested action.

---

## Data Models

### `InstagramProfile`

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | str | Numeric Instagram user ID |
| `username` | str | Lowercase username |
| `full_name` | str | Display name |
| `biography` | str | Profile bio text |
| `followers` | int | Follower count |
| `following` | int | Following count |
| `posts_count` | int | Total posts |
| `category` | str | Business/creator category label |
| `website` | str | Profile link in bio |
| `is_private` | bool | Account visibility |
| `is_verified` | bool | Blue checkmark |
| `is_business` | bool | Business account type |
| `account_type` | int | 1=personal, 2=creator, 3=business |
| `highlight_count` | int | Number of Highlights |
| `pronouns` | list[str] | Profile pronouns |
| `contact_phone` | str | Business phone |
| `public_email` | str | Business email |
| `city` | str | Business location |
| `usertags_count` | int | Photos tagged-by count |
| `has_reels` | bool | Reels tab visible |

### `InstagramPost`

| Field | Type | Description |
|-------|------|-------------|
| `shortcode` | str | Post identifier (`/p/{shortcode}/`) |
| `post_type` | str | `image`, `video`, `reel`, `carousel`, `igtv` |
| `product_type` | str | `feed`, `reel`, `igtv`, `clips` |
| `taken_at` | int | Unix timestamp |
| `taken_at_str` | str | `YYYY-MM-DD HH:MM UTC` |
| `age_days` | float | Days since posted |
| `likes` | int | Like count |
| `comments` | int | Comment count |
| `video_view_count` | int | Views (video posts) |
| `caption` | str | Full post caption |
| `hashtags` | list[str] | Extracted from caption |
| `mentions` | list[str] | @mentioned usernames |
| `coauthors` | list[str] | Co-author usernames |
| `sponsor_tags` | list[str] | Paid partnership labels |
| `usertags` | list[str] | Users tagged in photo/video |
| `music_title` | str | Audio track name |
| `music_artist` | str | Audio artist name |
| `location` | dict | `{name, lat, lng, pk}` |
| `carousel_count` | int | Slides in carousel |
| `is_pinned` | bool | Pinned to profile |

### `StoryItem`

| Field | Type | Description |
|-------|------|-------------|
| `pk` | str | Story ID |
| `shortcode` | str | Story shortcode |
| `media_type` | int | 1=image, 2=video |
| `duration_secs` | float | Video duration (0.0 for images) |
| `mentions` | list[str] | Mentioned usernames (mention stickers) |
| `hashtags` | list[str] | Hashtag stickers |
| `linked_post_code` | str | Shortcode of linked post (post sticker) |
| `music_title` | str | Background music |
| `music_artist` | str | Music artist |
| `link_stickers` | list | External link sticker data |
| `polls` | list | Poll questions + vote tallies |
| `is_paid_partnership` | bool | Paid partnership disclosure |
| `capture_type` | str | `boomerang`, `selfie`, etc. |
| `camera_facing` | str | `front` or `back` |

### `CommentItem`

| Field | Type | Description |
|-------|------|-------------|
| `pk` | str | Comment ID |
| `text` | str | Comment text (empty for GIF-only) |
| `username` | str | Commenter username |
| `comment_like_count` | int | Likes on this comment |
| `child_comment_count` | int | Number of threaded replies |
| `created_at_str` | str | Formatted timestamp |
| `is_verified` | bool | Commenter verified status |
| `has_gif` | bool | GIF-only comment |
| `is_caption` | bool | True = post's own caption |

### `ReelItem`

| Field | Type | Description |
|-------|------|-------------|
| `shortcode` | str | Reel shortcode |
| `play_count` | int | Primary metric — only available via Reels tab |
| `like_count` | int | Like count |
| `comment_count` | int | Comment count |
| `taken_at_str` | str | Formatted timestamp |
| `is_pinned` | bool | Pinned reel |
| `coauthor_ids` | list[str] | Co-author user IDs |

### `HighlightTray`

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Highlight ID |
| `title` | str | Highlight title |
| `media_count` | int | Number of stories in highlight |
| `created_at` | int | Creation timestamp |
| `latest_reel_media` | int | Timestamp of most recent item |
| `highlight_reel_type` | str | Highlight type |
| `is_pinned` | bool | Pinned highlight |
| `is_archived` | bool | Archived highlight |
| `cover_url` | str | Cover image URL |
| `items` | list | Story items (when include_media=True) |

---

## Authentication

### How to Export `cookies.txt`

You need a valid Instagram session cookie from a browser. The recommended method:

**Method 1: Browser Extension (recommended)**

1. Install the "Get cookies.txt LOCALLY" extension for [Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) or [Firefox](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/).
2. Log into Instagram in your browser.
3. Navigate to `instagram.com`.
4. Click the extension icon → "Export" → save as `cookies.txt`.
5. Place the file in the project root or set `INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt`.

**Method 2: Browser Developer Tools**

1. Open DevTools (F12) on instagram.com.
2. Go to Application → Cookies → `https://www.instagram.com`.
3. Copy the `sessionid`, `csrftoken`, `ds_user_id`, and `ig_did` values.
4. Construct a Netscape-format cookies.txt file manually.

**cookies.txt format (Netscape):**
```
# Netscape HTTP Cookie File
.instagram.com	TRUE	/	TRUE	1800000000	sessionid	YOUR_SESSION_ID
.instagram.com	TRUE	/	TRUE	1800000000	csrftoken	YOUR_CSRF_TOKEN
.instagram.com	TRUE	/	TRUE	1800000000	ds_user_id	YOUR_USER_ID
.instagram.com	TRUE	/	TRUE	1800000000	ig_did	YOUR_DEVICE_ID
```

### Session Hygiene

- Use a **dedicated account** for scraping — not your personal account.
- Do not use the same session for both scraping and normal browsing.
- Sessions expire. Refresh `cookies.txt` if you get 401 errors.
- Instagram may challenge sessions after large volumes of requests; rotate accounts if needed.

---

## Configuration

All settings are configurable via environment variables. Defaults are tuned for balanced throughput with a proxy pool.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_COOKIES` | `""` | Path to `cookies.txt` for authenticated tools |
| `INSTAGRAM_MCP_APP_ID` | `936619743392459` | Instagram x-ig-app-id header value |
| `INSTAGRAM_MCP_IMPERSONATE` | `chrome142` | curl_cffi impersonation target |
| `INSTAGRAM_MCP_TIMEOUT` | `10` | Per-request timeout in seconds |
| `INSTAGRAM_MCP_MAX_RETRIES` | `3` | Max retries per request (each retry uses a different proxy) |
| `INSTAGRAM_MCP_MAX_WORKERS` | `12` | Default concurrency for batch operations |

### Cache Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_CACHE_DISABLED` | `""` | Set `1` or `true` to disable all caching |
| `INSTAGRAM_MCP_CACHE_TTL` | `300` | Global cache TTL override in seconds |
| `INSTAGRAM_MCP_CACHE_MAX` | `500` | Maximum cache entries before eviction |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_RATE_LIMIT_RPS` | `100.0` | Requests per second (token bucket fill rate) |
| `INSTAGRAM_MCP_RATE_LIMIT_BURST` | `50` | Burst token count |
| `INSTAGRAM_MCP_RATE_BACKOFF_FACTOR` | `0.7` | Multiply RPS by this on 429 response |
| `INSTAGRAM_MCP_RATE_RECOVERY_FACTOR` | `1.15` | Multiply RPS by this on success |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive 429s to open circuit breaker |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN` | `60.0` | Seconds to pause when circuit opens |
| `INSTAGRAM_MCP_REQUEST_JITTER` | `0.1` | Max random jitter added to token-bucket sleep |

### Proxy Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_PROXIES` | `""` | Comma-separated proxy URLs (or use `proxies.txt`) |
| `INSTAGRAM_MCP_PROXY_MAX_FAILS` | `5` | Consecutive failures before proxy enters cooldown |
| `INSTAGRAM_MCP_PROXY_COOLDOWN` | `30` | Proxy cooldown in seconds |
| `INSTAGRAM_MCP_PROXY_MAX_COOLDOWN` | `300.0` | Maximum proxy cooldown in seconds |

### Pagination

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_MAX_PAGINATION` | `200` | Hard ceiling for feed pagination posts |
| `INSTAGRAM_MCP_GRAPHQL_DOC_ID` | (internal) | GraphQL doc_id for feed pagination override |

### JSON Export

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_EXPORT_ENABLED` | `""` | Set `0` or `false` to disable auto-export |
| `INSTAGRAM_MCP_EXPORT_DIR` | `./exports` | Directory for saved JSON files |
| `INSTAGRAM_MCP_EXPORT_INDENT` | `2` | JSON indentation spaces (0 = compact) |

---

## JSON Auto-Export

Every successful tool call automatically saves a structured JSON file. This is enabled by default and runs asynchronously — it never blocks the tool response.

### Directory Structure

```
exports/
  index.json                              ← running log of every save
  profile/
    nike_2026-05-15_10-23-45.json
    cristiano_2026-05-15_10-24-00.json
  feed_deep/
    cristiano_2026-05-15_10-25-00.json
  engagement/
  collab_network/
  compare/
  bulk_check/
  batch_scrape/
  tagged_by/
  reposts/
  post/
  reels/
  comments/
  hashtag/
  search/
  followers/
  following/
  post_likers/
  stories/
  highlights/
  location_posts/
  audio_reels/
```

### File Format

Each JSON file has three top-level keys:

```json
{
  "_meta": {
    "tool": "profile",
    "subject": "nike",
    "saved_at": "2026-05-15T10:23:45.123456+00:00",
    "saved_at_ts": 1747298625,
    "duration_s": 1.247,
    "server_version": "1.0.0"
  },
  "_summary": {
    "tool": "profile",
    "account": "@nike (Nike)",
    "followers": "306.4M",
    "verified": true,
    "website": "nike.com",
    "bio": "Just Do It.",
    "status": "active",
    "last_post_days": 2,
    "collaborators": ["@lebron", "@serena", "@nikefootball"]
  },
  "data": {
    "profile": { ... },
    "feed_tags": { ... },
    "is_dead": false,
    "last_post_days": 2
  }
}
```

**`_meta`:** Provenance — tool name, subject, timestamp, duration.

**`_summary`:** AI-optimised overview tailored to each tool type. Designed to be read first — covers 90% of use cases in a few lines. Automatically computed from data.

**`data`:** Clean, noise-free payload. The following are automatically stripped:
- CDN URLs (`display_url`, `thumbnail_url`, `profile_pic_url`, `video_url`)
- Redundant Unix timestamps (`taken_at` — kept as `taken_at_str`)
- Empty strings, empty lists, empty dicts
- Technical size fields (`width`, `height`, `carousel_count` when 0)
- Almost-always-false flags (`is_video`, `is_new_account`, `has_guides`)

**`index.json`:** Append-only log of every save with tool, subject, filename, timestamp, and duration.

---

## Tool Decision Guide

| Goal | Tool |
|------|------|
| Get basic profile info (bio, followers, category) | `instagram_profile` with `include_feed=false, check_alive=false` |
| Check if an account is active or dead | `instagram_profile` with `include_feed=false, check_alive=true` |
| Get profile + recent collaborators | `instagram_profile` (default) |
| Analyse posting history across 30–200 posts | `instagram_feed_deep` |
| Calculate engagement rate | `instagram_analyze_engagement` |
| Map who someone works with | `instagram_find_collab_network` |
| Compare multiple brands/creators | `instagram_compare_profiles` |
| Check 5–20 accounts at once | `instagram_bulk_check` |
| Research 100–500 accounts | `instagram_batch_scrape` |
| Get full details of a specific post | `instagram_post` |
| Read comments on a post | `instagram_post_comments` |
| Explore a hashtag (fast, no login) | `instagram_hashtag` (anon) |
| Explore a hashtag with play counts and 300 posts | `instagram_hashtag` (with cookies) |
| Find accounts by name/keyword | `instagram_search` |
| See who follows an account | `instagram_followers_list` |
| See who an account follows | `instagram_following_list` |
| See who liked a post | `instagram_post_likers` |
| Find who tags this account in their posts | `instagram_tagged_by` |
| See what content an account reposts | `instagram_reposts` |
| Get reel play counts | `instagram_reels` |
| View current stories | `instagram_stories` |
| Browse saved highlights | `instagram_highlights` |
| Find posts at a place | `instagram_location_posts` |
| Find all reels using a specific song | `instagram_audio_reels` |
| Check cache and proxy health | `instagram_server` with `action=status` |
| Clear stale cache for one user | `instagram_server` with `action=clear_user` |

---

## Proxy Setup

Proxies are optional but strongly recommended for bulk operations. Without proxies, rate limits apply per IP.

### Supported proxy formats

```
http://user:pass@host:port
http://host:port
socks5://user:pass@host:port
socks5://host:port
```

### Configuration methods

**Method 1: `proxies.txt` file** (place in project root)
```
# One proxy per line, # for comments
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
socks5://user:pass@proxy3.example.com:1080
```

**Method 2: Environment variable**
```bash
INSTAGRAM_MCP_PROXIES="http://user:pass@host1:8080,http://user:pass@host2:8080"
```

### Proxy behaviour

- **Round-robin selection** with per-proxy health tracking.
- A proxy that fails `INSTAGRAM_MCP_PROXY_MAX_FAILS` (default: 5) consecutive times enters cooldown for `INSTAGRAM_MCP_PROXY_COOLDOWN` (default: 30s).
- If all proxies are in cooldown, **auto-fallback to direct connection** (`proxy_auto_fallback=true`).
- Each retry in `max_retries` uses a different proxy automatically.
- Health stats visible via `instagram_server action=status`.

---

## Connecting to Claude Desktop

Add to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "instagram": {
      "command": "instagram-mcp",
      "env": {
        "INSTAGRAM_MCP_COOKIES": "/absolute/path/to/cookies.txt",
        "INSTAGRAM_MCP_PROXIES": "http://user:pass@host:port",
        "INSTAGRAM_MCP_EXPORT_DIR": "/absolute/path/to/exports"
      }
    }
  }
}
```

If installed with `uv`:

```json
{
  "mcpServers": {
    "instagram": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/instagram_mcp", "instagram-mcp"],
      "env": {
        "INSTAGRAM_MCP_COOKIES": "/absolute/path/to/cookies.txt"
      }
    }
  }
}
```

### Connecting to Claude Code

```bash
claude mcp add instagram instagram-mcp --env INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt
```

---

## Limitations

- **Private accounts:** Profile metadata is always accessible; feed, posts, stories, and highlights are not visible without following the account.
- **Follower lists:** Instagram restricts follower pagination to ~50 users for third-party access. Full pagination (up to 1000) only works on the authenticated account's own followers list.
- **Play counts:** Only available via `instagram_reels` (GraphQL Reels tab endpoint). The standard feed API (`instagram_feed_deep`) does not return play_count.
- **Comments:** Per-comment likes and threading are available but Instagram caps the returned comment count. Very active posts may return fewer comments than requested.
- **Location IDs:** Must be numeric Instagram-internal IDs. Name-based lookup is a convenience wrapper but requires a separate search step and may be imprecise.
- **Audio cluster IDs:** Must be extracted manually from post metadata or audio page URLs — there is no search-by-audio-name function.
- **Rate limits:** Anonymous requests share a pool per IP. Heavy use without proxies will trigger 429 responses. The adaptive rate limiter handles backoff automatically but reduces throughput.
- **Session expiry:** `cookies.txt` sessions expire. Long-running deployments need periodic cookie refresh.
- **No write operations:** This server is read-only. It cannot post, like, comment, follow, or modify any Instagram data.
- **No DMs or notifications:** Private messaging, notification feeds, and activity feeds are not implemented.
- **Carousel media:** Individual carousel slide URLs are not extracted — only the count is returned.
- **Historical data:** Instagram does not expose posts older than the account's paginated feed allows. For very old posts, pagination may be cut off before reaching them.

---

## FAQ

**Q: Do I need to log in to use this?**

No. 11 tools work completely anonymously with no account or cookies required. The remaining 11 tools require a `cookies.txt` file with a valid Instagram session. `instagram_hashtag` automatically switches between anon and auth modes depending on whether cookies are present.

**Q: Why use `curl_cffi` instead of `requests` or `aiohttp`?**

Instagram blocks `requests` and `aiohttp` at the TLS handshake level by inspecting the TLS fingerprint (JA3/JA4 hash). `curl_cffi` impersonates a real Chrome browser's TLS stack, making the connection indistinguishable from a genuine browser request.

**Q: How do I get play counts for reels?**

Use `instagram_reels`. The standard feed API (`instagram_feed_deep`) does not include play_count in its response. The reels tab uses a separate GraphQL endpoint (`PolarisProfileReelsTabContentQuery_connection`) that exposes this field.

**Q: Why does `instagram_hashtag` return only 12 posts in anon mode?**

The anonymous mode parses the public HTML page of `instagram.com/explore/tags/{tag}/`, which renders exactly 12 posts as a static page. The full paginated API (`i.instagram.com/api/v1/tags/{tag}/sections/`) requires authentication. With cookies, `instagram_hashtag` uses the API and returns up to 300 posts.

**Q: What happens when Instagram rate-limits the server?**

The `AdaptiveRateLimiter` detects 429 responses and automatically multiplies the rate by 0.7 (slows down), rotates to the next proxy, and retries. After 5 consecutive 429s, the circuit breaker opens and the server pauses all requests for 60 seconds. During this pause, the tool returns a `rate_limited` error with a suggested action to wait and retry.

**Q: Are exported JSON files safe to commit to git?**

No. They may contain personally identifiable information (usernames, bios, follower counts) and should be treated as data files. Add `exports/` to your `.gitignore`. The files are intended for local AI analysis workflows, not version control.

**Q: Can I run multiple tool calls in parallel?**

Yes. The server handles concurrent requests. For bulk operations across many accounts, use `instagram_bulk_check` (up to 20 profiles) or `instagram_batch_scrape` (up to 500 profiles) — these manage concurrency internally with configurable worker counts. Avoid issuing many simultaneous calls manually without proxies, as this concentrates all requests on a single IP.
