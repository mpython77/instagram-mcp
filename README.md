# instagram-mcp

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![MCP](https://img.shields.io/badge/MCP-compatible-green) ![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

A production-grade MCP (Model Context Protocol) server for Instagram intelligence. Exposes **26 tools** across two auth tiers — 15 tools run fully anonymously with no credentials; 11 tools require a `cookies.txt` session file. Built on `curl_cffi` with Chrome TLS impersonation, adaptive rate limiting, smart caching, and automatic JSON export.

Works natively with **Claude Desktop**, **Claude Code**, and any MCP-compatible AI client.

---

## Overview

### Auth Tiers

| Tier | Symbol | Requirement | Tool count |
|------|--------|-------------|-----------|
| Anonymous | 🌐 | None — no login, no cookies | 15 tools |
| Authenticated | 🔐 | `cookies.txt` with a valid Instagram session | 11 tools |

`instagram_hashtag` auto-upgrades from anon to auth mode when cookies are present.

### Key Features

- **Chrome TLS impersonation** via `curl_cffi` — bypasses fingerprint-based blocking
- **Adaptive rate limiter** — token-bucket with auto-backoff on 429, circuit breaker after 5 consecutive failures
- **Smart cache** — per-endpoint TTLs, LRU eviction, instant repeat calls
- **Proxy pool** — round-robin with per-proxy health tracking, auto-fallback to direct
- **Progress reporting** — MCP-native `report_progress` on every paginated tool
- **JSON auto-export** — every tool result saved to `exports/` with `_meta` + `_summary` + `data`
- **Selective toolsets** — enable only the groups you need via `INSTAGRAM_MCP_TOOLSETS`
- **Upload support** — publish single photos or carousels directly from Claude
- **Download support** — save images, videos, reels, and carousel slides to disk

---

## Installation

### Using pip

```bash
git clone <repo_url>
cd instagram_mcp
pip install -e .
instagram-mcp
```

### Using uv

```bash
uv sync
uv run instagram-mcp
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `mcp[cli]>=1.0.0` | MCP server protocol |
| `curl-cffi>=0.7.0` | Chrome TLS impersonation |
| `pydantic>=2.0.0` | Input validation |
| `aiofiles>=23.0` | Async file I/O for exports |
| `Pillow>=10.0.0` | PNG→JPEG conversion for uploads |
| `uvloop>=0.20` | Fast event loop (Linux/macOS) |

### cookies.txt Setup (for authenticated tools)

1. Log in to Instagram in your browser.
2. Install a cookie export extension:
   - Chrome: "Get cookies.txt LOCALLY"
   - Firefox: "cookies.txt" extension
3. Navigate to `instagram.com`, click the extension, export as `cookies.txt` (Netscape format).
4. Place the file in the project root or set `INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt`.

**Netscape format reference:**
```
# Netscape HTTP Cookie File
.instagram.com	TRUE	/	TRUE	1800000000	sessionid	YOUR_SESSION_ID
.instagram.com	TRUE	/	TRUE	1800000000	csrftoken	YOUR_CSRF_TOKEN
.instagram.com	TRUE	/	TRUE	1800000000	ds_user_id	YOUR_USER_ID
.instagram.com	TRUE	/	TRUE	1800000000	ig_did	YOUR_DEVICE_ID
```

**Session hygiene:**
- Use a dedicated account — not your personal account.
- Sessions expire; refresh `cookies.txt` if you get 401 errors.
- Do not share the same session between scraping and normal browsing.

### MCP Config for Claude Desktop

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

With `uv`:

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

### MCP Config for Claude Code

```bash
claude mcp add instagram instagram-mcp --env INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt
```

---

## All 26 Tools

### 🗂️ Profile & Feed

#### `instagram_profile` 🌐

Fetch a public account's profile data. Controls depth via flags.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `include_feed` | bool | `true` | Fetch recent posts and extract tags/mentions/hashtags |
| `max_feed_posts` | int | `12` | Posts to analyse when include_feed=True (1–12) |
| `max_age_days` | int | `30` | Ignore posts older than N days |
| `check_alive` | bool | `true` | Return last_post_days and active/dead status |
| `dead_threshold_days` | int | `365` | Days without posts to mark account dead |
| `since_date` / `until_date` | str | `""` | Filter posts by date: DD.MM.YYYY |

**Modes:**

| Mode | include_feed | check_alive | Use case |
|------|-------------|-------------|----------|
| Full (default) | true | true | Profile + feed tags + activity |
| Status check | false | true | Fastest: is the account active/dead/private? |
| Profile only | false | false | Bio + followers — single API call |
| Tags only | true | false | Tag extraction, no dead-check |

---

#### `instagram_feed_deep` 🌐

Paginated feed analysis. Fetches up to 200 posts across multiple API pages (~50 posts/page). Supports smart date-range pagination — paginates until the requested window is fully covered.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to fetch (1–200) |
| `max_age_days` | int | `30` | Stop when posts exceed this age |
| `include_posts_detail` | bool | `false` | Include full caption, hashtags, likes, location, music per post |
| `since_date` / `until_date` | str | `""` | Filter posts by date |

**Note:** Play counts on reels are not available via this tool. Use `instagram_reels` for play counts.

---

#### `instagram_bulk_check` 🌐

Fetch up to 20 profiles in parallel with activity status for each.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `usernames` | list[str] | required | Up to 20 usernames (without @) |
| `concurrency` | int | `5` | Parallel fetch count (1–20) |

Status values: `active`, `dead`, `private`, `not_found`. Non-found accounts appear in results rather than raising errors.

---

#### `instagram_compare_profiles` 🌐

Side-by-side comparison of 2–5 accounts fetched in parallel.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `usernames` | list[str] | required | 2–5 usernames (without @) |

Compared fields: followers, following, posts count, ER%, verification, account type, category, website, posting frequency, content mix.

---

### 📊 Analysis

#### `instagram_analyze_engagement` 🌐

Calculates ER% and detailed content analytics across up to 200 posts.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to analyse (1–200) |
| `max_age_days` | int | `90` | Skip posts older than N days |
| `since_date` / `until_date` | str | `""` | Date filters |

**Formula:** `ER% = (avg_likes + avg_comments) / followers × 100`

**Benchmarks:** Excellent ≥6% / Good 3–6% / Average 1–3% / Low <1%

**Output:** ER% with rating, content mix (image/video/reel/carousel), best posting days by avg engagement, top 5 posts by likes, top 15 hashtags by frequency, posting frequency.

---

#### `instagram_find_collab_network` 🌐

Maps all people who appear across recent posts across four relationship types: photo usertags, caption @mentions, official co-authors, and paid sponsors.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to scan (1–200) |
| `max_age_days` | int | `90` | Skip posts older than N days |
| `min_frequency` | int | `1` | Minimum appearances to include (1–50) |
| `since_date` / `until_date` | str | `""` | Date filters |

---

#### `instagram_account_report` 🌐

All-in-one account intelligence report — runs `instagram_profile` + `instagram_analyze_engagement` + `instagram_find_collab_network` in a single tool call. Replaces three separate calls.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Posts to analyse (1–200) |
| `include_collab` | bool | `true` | Include collaborator network section |

---

### 📸 Content

#### `instagram_post` 🌐

Full details for a single post by shortcode or URL. Parses public HTML — no auth required.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Shortcode or full URL (`/p/`, `/reel/`, `/tv/`) |

**Accepted formats:** `DXjuqH9nDVE` · `https://www.instagram.com/p/DXjuqH9nDVE/` · `instagram.com/reel/ABC123/`

**Returns:** post_type, author, likes, comments, view_count, play_count, caption, hashtags, mentions, usertags, coauthors, sponsor_tags, music_artist, music_title, location (name + GPS lat/lng + Maps URL), timestamp, carousel_count, duration_secs.

---

#### `instagram_post_bulk` 🌐

Fetch up to 50 posts in parallel by shortcode or URL list.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `shortcodes` | list[str] | required | 1–50 shortcodes or URLs |
| `max_concurrency` | int | `5` | Parallel fetch limit (1–20) |

Non-blocking — partial results returned if individual posts fail.

---

#### `instagram_post_comments` 🌐

Fetch comments with engagement metrics and thread structure.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Shortcode or URL |
| `max_comments` | int | `100` | Maximum comments (1–500) |
| `sort_order` | str | `"popular"` | `popular` (most-liked first) or `recent` (chronological) |

**Per comment:** username, text, comment_like_count, child_comment_count, created_at, is_verified, has_gif, is_caption.

---

#### `instagram_post_likers` 🔐

Fetch users who liked a post with full friendship status per liker.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Post shortcode or URL |

Returns up to ~98 likers. Per user: username, full_name, is_verified, is_private, you_follow_them, they_follow_you, plus total like count for the post.

---

### 🔍 Hashtag & Discovery

#### `instagram_hashtag` 🌐/🔐

Fetch top posts for a hashtag. Auto-upgrades to auth mode when cookies are present.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tag` | str | required | Hashtag without # (e.g., `football`) |
| `max_posts` | int | `30` | Max posts (anon: always 12; auth: up to 300) |

**Anon mode:** Parses public HTML → 12 posts + related_searches. Blocked for some hashtags (#swimwear, #fitness, etc.).

**Auth mode:** Uses paginated API → up to 300 posts with full like_count, play_count, comment_count.

---

#### `instagram_hashtag_deep` 🌐/🔐

Deep hashtag analytics: top accounts ranked by engagement, content type breakdown, best posting hour, aggregate stats across up to 500 posts.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tag` | str | required | Hashtag without # |
| `max_posts` | int | `90` | Posts to analyse (1–500) |
| `top_n` | int | `15` | Top accounts to include (1–50) |

**Output:** avg likes/comments/views, total reach, media type breakdown (%), best hour UTC, ranked top-N accounts table.

---

#### `instagram_niche_top` 🌐/🔐

Leaderboard of top accounts for a hashtag, ranked by engagement, post count, or total likes.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tag` | str | required | Hashtag without # |
| `max_posts` | int | `90` | Posts to scan (12–500) |
| `top_n` | int | `15` | Accounts in leaderboard (3–50) |
| `sort_by` | str | `"engagement"` | `engagement`, `post_count`, or `total_likes` |

---

#### `instagram_similar_accounts` 🔐

Discover accounts Instagram considers similar via the internal chaining API.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Seed account (without @) |
| `limit` | int | `20` | Max similar accounts (1–50) |

**Endpoint:** `www.instagram.com/api/v1/discover/chaining/?target_id={pk}`

---

#### `instagram_search` 🔐

Search Instagram for accounts and/or hashtags by keyword.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Search keyword |
| `context` | str | `"blended"` | `blended` (users + hashtags), `user`, or `hashtag` |

**Per user:** username, full_name, is_verified, is_private, follower_count.  
**Per hashtag:** name, total post count.

---

### 👥 Social Graph

#### `instagram_followers_list` 🔐

Fetch recent followers with mutual follow status.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_users` | int | `50` | Max followers to return |

**Limitation:** Instagram restricts follower access to ~50 users for accounts other than your own, regardless of max_users.

---

#### `instagram_following_list` 🔐

Fetch the full following list with favorite detection and pagination.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_users` | int | `200` | Max following accounts (1–1000, 50 per page) |

**Per user:** username, full_name, is_verified, is_private, is_favorite (Close Friend marker), you_follow_them, they_follow_you.

---

#### `instagram_tagged_by` 🔐

Fetch posts made by OTHER accounts that tag this account (the Tagged tab).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Account to check (without @) |
| `max_posts` | int | `50` | Max tagged posts (1–200) |
| `min_poster_followers` | int | `0` | Filter taggers with fewer followers |

**Distinction:** `instagram_find_collab_network` finds who appears in posts the account CREATED; `instagram_tagged_by` finds posts OTHERS created that tag this account.

---

#### `instagram_reposts` 🔐

Fetch content that an account actively reposted from other creators (Reposts Tab).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_posts` | int | `50` | Max repost items (1–200) |

**Signal:** A repost is an explicit endorsement — stronger relationship signal than a tag or mention.

---

### 🎬 Media

#### `instagram_reels` 🔐

Fetch an account's reels with play counts. The ONLY tool that exposes play_count — the standard feed API does not return it.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_reels` | int | `50` | Max reels (1–200, 12 per page) |

**Endpoint:** GraphQL `PolarisProfileReelsTabContentQuery_connection`

---

#### `instagram_stories` 🔐

Fetch an account's currently active Stories (expires after 24h, cached 2 min).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |

**Per story:** media_type, duration_secs, mentions, hashtags, linked_post_code, music_title, music_artist, link_stickers, polls, is_paid_partnership, capture_type (boomerang/selfie), camera_facing.

---

#### `instagram_highlights` 🔐

Fetch an account's Highlights tray and optionally the media inside each highlight.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `max_highlights` | int | `50` | Max highlights from tray (1–200) |
| `include_media` | bool | `false` | Fetch story items inside highlights |
| `max_media_highlights` | int | `3` | Fetch media for top N highlights (1–10) |

---

#### `instagram_audio_reels` 🔐

Fetch all reels using a specific audio track, identified by `audio_cluster_id`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `audio_cluster_id` | str | required | Numeric audio cluster ID |
| `max_reels` | int | `24` | Max reels to return (1–100) |

**How to find audio_cluster_id:** Extract from a reel's `clips_metadata.music_info.audio_cluster_id`, or from the audio page URL `instagram.com/reels/audio/{id}/`.

---

#### `instagram_location_posts` 🔐

Fetch top posts at a geographic location by Instagram location ID or name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `location_id` | str | `""` | Numeric Instagram location ID (preferred) |
| `location_name` | str | `""` | Location name to search if ID not known |
| `max_posts` | int | `33` | Max posts (1–100) |

**How to find location_id:** Extract from any post's location data via `instagram_post`, or visit `instagram.com/explore/locations/{id}/`.

---

### 📤 Upload & Download

#### `instagram_upload_photo` 🔐

Upload 1–10 images to Instagram as a post (single photo or carousel). Returns the post URL and shortcode immediately after publishing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `images` | list[str] | required | 1–10 absolute file paths (JPEG native; PNG requires Pillow) |
| `caption` | str | `""` | Post caption text |
| `disable_comments` | bool | `false` | Disable comments on the post |
| `hide_like_count` | bool | `false` | Hide like count from other viewers |
| `location_id` | str | `""` | Optional Instagram location ID |

**Note:** This is a write operation — it publishes a real post to Instagram.

---

#### `instagram_download` 🔐

Download all media files from an Instagram post to a local directory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Post shortcode or URL |
| `save_dir` | str | required | Existing local directory path |

**Supports:**
- Single image posts → saves 1 `.jpg`
- Video / Reel posts → saves 1 `.mp4`
- Carousel posts → saves N `.jpg`/`.mp4` files (one per slide)

**Endpoint:** `/api/v1/media/{id}/info/` — fetches full media metadata including CDN URLs before downloading.

**Example output:**
```
## Download complete — `DXjuqH9nDVE`
- Type: carousel
- Files: 4/4 saved in `/home/user/downloads`
- Time: 3.21s

### Saved files
- `/home/user/downloads/DXjuqH9nDVE_1.jpg` (482 KB, jpg)
- `/home/user/downloads/DXjuqH9nDVE_2.jpg` (519 KB, jpg)
- `/home/user/downloads/DXjuqH9nDVE_3.mp4` (8,204 KB, mp4)
- `/home/user/downloads/DXjuqH9nDVE_4.jpg` (477 KB, jpg)
```

---

### ⚡ Batch

#### `instagram_batch_scrape` 🌐

Large-scale scraping for up to 2000 profiles. Async, high-concurrency, with resume support, streaming JSONL output, and fail-fast protection.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `targets` | list[str] | required | Up to 2000 usernames (without @) |
| `profile_only` | bool | `false` | TURBO MODE: skip feed, only profile metadata |
| `max_workers` | int | `20` | Parallel workers (1–100) |
| `max_posts_per_profile` | int | `50` | Max posts per profile (ignored in TURBO) |
| `since_date` / `until_date` | str | `""` | Date filters (DD.MM.YYYY, full mode only) |
| `use_cookies` | bool | `false` | Use authenticated session |
| `output_file` | str | `""` | Output path for final JSON |
| `stream_jsonl` | bool | `true` | Append each completed profile to `output_file.jsonl` in real time |

See the dedicated **Batch Scraping** section below for full guidance.

---

### 🛠️ Server

#### `instagram_server` 🌐

Server diagnostics and cache management.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | str | `"status"` | `status`, `clear_cache`, `clear_user`, or `reload_cookies` |
| `username` | str | `""` | Target username for `clear_user` action |

**Actions:**

| Action | Effect |
|--------|--------|
| `status` | Cache hit rate, entry count, proxy health (per-proxy latency + success rate), rate limiter state |
| `clear_cache` | Flush all cached data |
| `clear_user` | Flush cache for one username only |
| `reload_cookies` | Reload `cookies.txt` without restarting the server |

---

## Batch Scraping

`instagram_batch_scrape` operates in two distinct modes. Choosing the right mode is critical for speed.

### TURBO MODE — `profile_only=True` (30–60x faster)

Profile metadata only: followers, bio, posts_count, verified, category, website. **No posts are fetched.** Completes 1000 profiles in 30–60 seconds with healthy proxies.

**Use when you need:**
- Bulk follower/bio collection
- Filtering verified or business profiles
- "Top 50 largest accounts from a list of 1000"
- Dead-check (falls back to posts_count == 0)

```
instagram_batch_scrape
  targets=[...1000 usernames...]
  profile_only=True
  max_workers=80
  output_file=/tmp/profiles.json
```

### FULL MODE — `profile_only=False` (default)

Profile + feed posts + tags + dead detection + engagement data. Slower: 500 profiles × 50 posts ≈ 5–10 minutes with healthy proxies.

**Use when you need:**
- Engagement analysis
- Tags/usertags per profile
- Real dead-account check (post date based)
- Date-range filtering

```
instagram_batch_scrape
  targets=[...500 usernames...]
  profile_only=False
  max_workers=30
  max_posts_per_profile=50
  since_date="01.01.2026"
  output_file=/tmp/full_profiles.json
```

### Worker Selection Guide

| Proxy setup | TURBO (profile_only) | FULL mode |
|-------------|---------------------:|----------:|
| 0–1 proxy | 10–20 | 5–15 |
| 5–10 proxies | 50–80 | 20–30 |
| 20+ proxies | 80–100 | 40–50 |

### Key Features

- **Resume** — re-run with the same `output_file` to skip already-completed profiles
- **Streaming JSONL** — `stream_jsonl=True` (default) appends each profile to `output_file.jsonl` in real time; `tail -f` friendly; memory-safe for huge batches
- **Fail-fast** — auto-aborts when error rate exceeds 60% after 50+ completions (IP-ban or dead cookies signal)
- **Aggregated JSON** — automatically disabled for batches >500 profiles (JSONL stream preferred); always written otherwise
- **Date filtering** — `since_date`/`until_date` in DD.MM.YYYY format (full mode only)

---

## Configuration

All settings are configurable via environment variables.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_COOKIES` | `""` | Path to `cookies.txt` for authenticated tools |
| `INSTAGRAM_MCP_APP_ID` | `936619743392459` | Instagram x-ig-app-id header value |
| `INSTAGRAM_MCP_IMPERSONATE` | `chrome142` | curl_cffi Chrome impersonation target |
| `INSTAGRAM_MCP_TIMEOUT` | `10` | Per-request timeout in seconds |
| `INSTAGRAM_MCP_MAX_RETRIES` | `3` | Max retries per request (each uses a different proxy) |
| `INSTAGRAM_MCP_MAX_WORKERS` | `12` | Default concurrency for batch operations |
| `INSTAGRAM_MCP_MAX_CLIENTS` | `50` | curl_cffi internal libcurl handle pool size |

### Cache Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_CACHE_DISABLED` | `""` | Set `1` or `true` to disable all caching |
| `INSTAGRAM_MCP_CACHE_TTL` | `300` | Global cache TTL override in seconds |
| `INSTAGRAM_MCP_CACHE_MAX` | `500` | Maximum cache entries before LRU eviction |

**Per-endpoint TTLs** (when cache is enabled):

| Endpoint type | TTL | Rationale |
|--------------|-----|-----------|
| Profile | 300s | Bio/follower counts change infrequently |
| Feed (per page) | 180s | New posts appear every few hours |
| Stories | 120s | Stories expire and update frequently |
| Highlights | 300s | Highlights change infrequently |
| Hashtag | 300s | Top posts rotate slowly |
| Comments | 60s | New comments arrive constantly |
| Following/Followers | 300s | Follow graph changes infrequently |
| Reels tab | 300s | New reels every few days |

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
| `INSTAGRAM_MCP_PROXY_CB_FAIL_THRESHOLD` | `3` | Per-proxy circuit breaker failure threshold |
| `INSTAGRAM_MCP_PROXY_MAX_CONCURRENT` | `30` | Per-proxy max concurrent requests (bulkhead) |
| `INSTAGRAM_MCP_PER_PROXY_RPS` | `1.0` | Per-proxy token-bucket rate (req/s) |

### Toolset Selection

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_TOOLSETS` | `all` | Comma-separated toolset names to enable |
| `INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES` | `""` | Set `1` to hide auth-only tools when no cookies loaded |

**Valid toolset names:** `profile`, `analysis`, `content`, `social_graph`, `batch`, `upload`, `download`, `server`, `all`

The `server` toolset (`instagram_server`) is always enabled regardless of selection.

### JSON Export

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_EXPORT_ENABLED` | `""` | Set `0` or `false` to disable auto-export |
| `INSTAGRAM_MCP_EXPORT_DIR` | `./exports` | Directory for saved JSON files |
| `INSTAGRAM_MCP_EXPORT_INDENT` | `2` | JSON indentation spaces (0 = compact) |

### Pagination

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_MAX_PAGINATION` | `200` | Hard ceiling for feed pagination posts |
| `INSTAGRAM_MCP_GRAPHQL_DOC_ID` | (internal) | GraphQL doc_id override for feed pagination |

---

## Proxy Setup

Proxies are optional but strongly recommended for bulk operations.

### Supported formats

```
http://user:pass@host:port
http://host:port
socks5://user:pass@host:port
socks5://host:port
```

### Configuration methods

**Method 1: `proxies.txt`** (place in project root, one proxy per line)
```
# comments supported
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
socks5://user:pass@proxy3.example.com:1080
```

**Method 2: Environment variable**
```bash
INSTAGRAM_MCP_PROXIES="http://user:pass@host1:8080,http://user:pass@host2:8080"
```

### Proxy behaviour

- Round-robin selection with per-proxy health tracking
- A proxy that fails `PROXY_MAX_FAILS` consecutive times enters cooldown
- Cooldown starts at 30s and increases exponentially up to `PROXY_MAX_COOLDOWN`
- If all proxies are in cooldown, auto-fallback to direct connection
- Each retry in `max_retries` automatically uses a different proxy
- Health stats visible via `instagram_server action=status`

---

## Exports

Every successful tool call saves a structured JSON file to `exports/`. Runs asynchronously — never blocks the tool response.

### Directory structure

```
exports/
  index.json                         ← append-only log of every save
  profile/
    nike_2026-05-16_10-23-45.json
  feed_deep/
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
  hashtag_deep/
  search/
  followers/
  following/
  post_likers/
  stories/
  highlights/
  location_posts/
  audio_reels/
  post_bulk/
  similar_accounts/
  niche_top/
  account_report/
  upload_photo/
```

### File format

Each JSON file has three top-level keys:

```json
{
  "_meta": {
    "tool": "profile",
    "subject": "nike",
    "saved_at": "2026-05-16T10:23:45.123456+00:00",
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
    "last_post_days": 2
  },
  "data": {
    "profile": { ... },
    "feed_tags": { ... },
    "is_dead": false,
    "last_post_days": 2
  }
}
```

**`_meta`:** Provenance — tool name, subject, timestamp, request duration.

**`_summary`:** AI-optimised overview tailored to each tool type. Covers 90% of use cases in a few lines.

**`data`:** Clean payload. Automatically strips: CDN URLs, redundant Unix timestamps (kept as `*_str`), empty strings/lists/dicts, technical size fields, always-false flags.

**`index.json`:** Append-only log with tool, subject, filename, timestamp, and duration for every save.

---

## Quick Examples

| Prompt | Correct tool |
|--------|-------------|
| "Scrape the nike profile" | `instagram_profile username=nike` |
| "Get 100 posts from cristiano" | `instagram_feed_deep username=cristiano max_posts=100` |
| "What's the engagement rate for @adidas" | `instagram_analyze_engagement username=adidas` |
| "Who does @leomessi collaborate with" | `instagram_find_collab_network username=leomessi` |
| "Compare nike, adidas, puma" | `instagram_compare_profiles usernames=[nike,adidas,puma]` |
| "Check if these 20 accounts are alive" | `instagram_bulk_check usernames=[...]` |
| "Follower counts only for 1000 profiles" | `instagram_batch_scrape profile_only=True max_workers=80` |
| "Scrape 500 profiles with their posts from March 2026" | `instagram_batch_scrape profile_only=False since_date="01.03.2026" until_date="31.03.2026"` |
| "Top posts for #football" | `instagram_hashtag tag=football` |
| "Who dominates the #fitness niche?" | `instagram_niche_top tag=fitness max_posts=200` |
| "Deep analysis of #travel hashtag" | `instagram_hashtag_deep tag=travel max_posts=300` |
| "Get details on these 5 posts at once" | `instagram_post_bulk shortcodes=[sc1,sc2,sc3,sc4,sc5]` |
| "Accounts similar to @gymshark" | `instagram_similar_accounts username=gymshark` |
| "Full report on @cristiano" | `instagram_account_report username=cristiano` |
| "What stories does @nike have right now" | `instagram_stories username=nike` |
| "Download all slides from this carousel" | `instagram_download post=DXjuqH9nDVE save_dir=/tmp/media` |
| "Post this photo to Instagram" | `instagram_upload_photo images=[/path/to/photo.jpg] caption="caption text"` |
| "Clear the cache for @nike" | `instagram_server action=clear_user username=nike` |
| "Check proxy and cache health" | `instagram_server action=status` |

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
│  tools.py ── 26 tool handlers (async, ctx: Context)             │
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
│  fetch_search()        fetch_post_likers()   fetch_post_bulk() │
│  fetch_similar_accounts() fetch_media_info() upload_photo()    │
│                                                                 │
│  _get_session()  ──── SmartCache ──── AdaptiveRateLimiter       │
│  _get_auth_session()── CookieManager ─ ProxyManager             │
└──────┬──────────────────────────────────────────────────────────┘
       │  curl_cffi AsyncSession (Chrome TLS impersonation)
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
4. **ProxyManager** selects the next healthy proxy (round-robin with per-proxy circuit breaker); falls back to direct if all proxies are in cooldown.
5. **curl_cffi AsyncSession** makes the request with Chrome TLS fingerprint. Two session types: `_get_session()` (anonymous, proxy-aware) and `_get_auth_session()` (single authenticated session with cookies).
6. On success: response cached, rate limiter recovery applied, result returned.
7. On 429: rate multiplied by 0.7, proxy rotated, retry (up to `max_retries`).
8. **Formatter** converts result to Markdown for the MCP response.
9. **JsonExporter** asynchronously saves the result to `exports/{tool}/{subject}_{timestamp}.json`.

### Key Design Decisions

- **`curl_cffi` with Chrome impersonation** bypasses TLS fingerprinting. Instagram blocks `requests` and `aiohttp` by JA3/JA4 hash.
- **Two session types** prevent session contamination: anonymous sessions use a proxy pool; authenticated sessions use a single stable session with cookies.
- **`i.instagram.com`** is used for hashtag sections, location sections, and audio reels — these endpoints require `ig_user_agent` + `x-ig-app-id` headers not exposed on `www.instagram.com`.
- **GraphQL** is used only for the reels tab (`PolarisProfileReelsTabContentQuery_connection`) because the REST API does not expose play counts.
- **All tool handlers are async** with `ctx: Context` for MCP-native progress reporting.
- **`ToolError` for all errors** — never raises Python exceptions; always returns `isError=true` in the MCP protocol response with an LLM-readable message and suggested action.

---

## Limitations

- **Private accounts:** Profile metadata is always accessible; feed, posts, stories, and highlights are not visible without following the account.
- **Follower lists:** Instagram restricts follower pagination to ~50 users for third-party access. Full pagination only works on the authenticated account's own followers list.
- **Play counts:** Only available via `instagram_reels`. The standard feed API does not return `play_count`.
- **Comments:** Very active posts may return fewer comments than requested — Instagram caps the returned count.
- **Rate limits:** Anonymous requests share a pool per IP. Heavy use without proxies will trigger 429 responses. The adaptive rate limiter handles backoff automatically.
- **Session expiry:** `cookies.txt` sessions expire. Long-running deployments need periodic cookie refresh. Use `instagram_server action=reload_cookies` to refresh without restarting.
- **No write operations except upload:** The server cannot like, comment, follow, or modify Instagram data other than publishing posts via `instagram_upload_photo`.
- **No DMs or notifications:** Private messaging, notification feeds, and activity feeds are not implemented.
- **Carousel media:** `instagram_download` fetches all slides; `instagram_feed_deep` returns only the slide count.
- **Historical data:** Instagram does not expose posts older than the account's paginated feed allows.

---

## FAQ

**Q: Do I need to log in to use this?**

No. 15 tools work completely anonymously with no account or cookies required. 11 tools require a `cookies.txt` file. `instagram_hashtag` automatically switches between anon and auth modes depending on whether cookies are present.

**Q: Why use `curl_cffi` instead of `requests` or `aiohttp`?**

Instagram blocks `requests` and `aiohttp` at the TLS handshake level by inspecting the TLS fingerprint (JA3/JA4 hash). `curl_cffi` impersonates a real Chrome browser's TLS stack, making the connection indistinguishable from a genuine browser request.

**Q: How do I get play counts for reels?**

Use `instagram_reels`. The standard feed API (`instagram_feed_deep`) does not include `play_count` in its response. The reels tab uses a separate GraphQL endpoint that exposes this field.

**Q: Why does `instagram_hashtag` return only 12 posts in anon mode?**

The anonymous mode parses the public HTML of `instagram.com/explore/tags/{tag}/`, which renders exactly 12 posts as a static page. The full paginated API requires authentication. With cookies, `instagram_hashtag` returns up to 300 posts.

**Q: What happens when Instagram rate-limits the server?**

The `AdaptiveRateLimiter` detects 429 responses and automatically multiplies the rate by 0.7, rotates to the next proxy, and retries. After 5 consecutive 429s, the circuit breaker opens and all requests pause for 60 seconds. The tool returns a `rate_limited` error with a suggested action to wait and retry.

**Q: Can I run multiple tool calls in parallel?**

Yes. The server handles concurrent requests. For bulk operations, use `instagram_bulk_check` (up to 20 profiles) or `instagram_batch_scrape` (up to 2000 profiles) — these manage concurrency internally. Avoid issuing many simultaneous manual calls without proxies, as this concentrates all requests on a single IP.

**Q: Are exported JSON files safe to commit to git?**

No. They may contain personally identifiable information. Add `exports/` to your `.gitignore`. The files are intended for local AI analysis workflows.

**Q: How do I refresh cookies without restarting the server?**

Use `instagram_server action=reload_cookies`. This reloads the `cookies.txt` file and resets the authenticated session so the next auth-tool call picks up the new cookies.
