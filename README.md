# instagram-mcp

A production-grade MCP server for fetching Instagram data — no login required for 10 tools, full 13-tool access with authenticated session cookies.

Works natively with Claude Desktop, Claude Code, and any MCP-compatible AI assistant.

---

## Table of Contents

- [Features](#features)
- [Tools](#tools)
- [Installation](#installation)
- [Configuration](#configuration)
- [Authentication (Optional)](#authentication-optional)
- [Proxy Setup](#proxy-setup)
- [Connecting to Claude Desktop](#connecting-to-claude-desktop)
- [Environment Variables](#environment-variables)
- [Architecture](#architecture)
- [File Structure](#file-structure)
- [FAQ](#faq)

---

## Features

- **13 MCP tools** — from single profile lookups to 500-account batch scraping
- **10 anonymous tools** — no login, no cookies, no credentials required
- **3 authenticated tools** — deeper data access via `cookies.txt`
- **Deep pagination** — GraphQL cursor-based fetching up to 200 posts
- **Batch scraping** — parallel workers, configurable concurrency, date filtering
- **TTL cache** — repeated lookups are instant; per-data-type TTLs
- **Adaptive rate limiter** — token-bucket algorithm + circuit breaker + jitter
- **Proxy rotation** — automatic health checks, cooldown, fallback to direct connection
- **curl_cffi impersonation** — requests are sent as Chrome 142 (bot-detection bypass)
- **Dual transport** — STDIO (Claude Desktop/Code) and HTTP (custom integrations)

---

## Tools

### Anonymous Tools (🌐 — no login required)

| # | Tool | Description |
|---|------|-------------|
| 1 | `instagram_profile` | Profile metadata + up to 12 recent post tags + account activity status |
| 2 | `instagram_feed_deep` | Paginated feed analysis — up to 200 posts |
| 3 | `instagram_analyze_engagement` | Engagement rate %, content mix, best posting days, top posts, top hashtags |
| 4 | `instagram_find_collab_network` | Maps usertags, @mentions, co-authors, and paid sponsors across recent posts |
| 5 | `instagram_compare_profiles` | Side-by-side comparison table for 2–5 accounts, fetched in parallel |
| 6 | `instagram_bulk_check` | Check up to 20 accounts in parallel — status, followers, last post |
| 7 | `instagram_batch_scrape` | Large-scale scraping: up to 500 profiles, parallel workers, date range filter |
| 8 | `instagram_server` | Server diagnostics + cache management |
| 9 | `instagram_post` | Full details for a single post: GPS location, caption, hashtags, usertags, music |
| 10 | `instagram_post_comments` | Post comments with per-comment likes, reply count, GIF detection, language flags |

### Authenticated Tools (🔐 — requires cookies.txt)

| # | Tool | Description |
|---|------|-------------|
| 11 | `instagram_tagged_by` | Posts by OTHER accounts that tag this account (Tagged Tab endpoint) |
| 12 | `instagram_reposts` | Content this account chose to repost from others (Reposts Tab endpoint) |
| 13 | `instagram_reels` | Account's own reels with **play counts** — the only endpoint that exposes them |

> **Note:** `play_count` is **not available** via `instagram_feed_deep` or `instagram_analyze_engagement`. The standard feed API returns `view_count=null` for all reels. Only the Reels Tab endpoint exposes true play counts — this is an Instagram API limitation, not a bug.

---

## Tool Reference

### `instagram_profile`

```
Parameters:
  username            Instagram username (without @)
  include_feed        Fetch recent post tags (default: true)
  max_feed_posts      How many posts to include (1–12, default: 12)
  check_alive         Check whether the account is active (default: true)
  dead_threshold_days Days of inactivity before "dead" classification (default: 365)
  max_age_days        Only include posts newer than N days (default: 4)

Returns:
  followers, following, posts count, bio, website, category
  is_verified, is_business, is_private
  Hashtags and @mentions extracted from recent posts
  last_post_days, is_dead flag
```

### `instagram_feed_deep`

```
Parameters:
  username            Instagram username
  max_posts           Number of posts to fetch (1–200, default: 50)
  max_age_days        Only include posts from the last N days (1–365)
  since / until       Date range filter (DD.MM.YYYY)

Returns:
  Per post: shortcode, URL, likes, comments, caption, hashtags, usertags
  Chronological order, newest first
  pages_fetched and has_more indicators
```

### `instagram_analyze_engagement`

```
Parameters:
  username            Instagram username
  max_posts           Posts to analyze (1–200, default: 50)

Returns:
  Engagement rate % (likes + comments / followers × 100)
  Content type breakdown: image / video / carousel / reel percentages
  Best posting days by weekday
  Top 5 posts by engagement
  Top 10 hashtags
  Average likes, average comments, median engagement
```

### `instagram_find_collab_network`

```
Parameters:
  username            Instagram username
  max_posts           Posts to scan (1–200, default: 50)
  min_frequency       Minimum appearances to be included (default: 1)

Returns:
  Usertags: accounts tagged in photos/videos
  @mentions: accounts mentioned in captions
  Co-authors: accounts listed as post co-creators
  Paid partnerships: sponsored content disclosures
  Frequency count per account
```

### `instagram_compare_profiles`

```
Parameters:
  usernames           List of 2–5 usernames

Returns:
  Side-by-side table: followers, posts, ER%, last post date
  All accounts fetched in parallel
```

### `instagram_bulk_check`

```
Parameters:
  usernames           Up to 20 usernames
  check_alive         Include activity check (default: true)

Returns:
  Per account: status (active / dead / private / not_found), followers, last post date
```

### `instagram_batch_scrape`

```
Parameters:
  targets             Up to 500 usernames
  since_date          Start date filter (DD.MM.YYYY)
  until_date          End date filter (DD.MM.YYYY)
  max_workers         Parallel workers (1–20, default: 10)
  use_cookies         Use authenticated session (default: false)
  output_file         Path to save JSON results (empty = temp file)

Returns:
  Path to the output JSON file
  Success / failed / skipped counts
```

### `instagram_post`

```
Parameters:
  post                Shortcode or full URL
                      ('DXjuqH9nDVE' or 'https://instagram.com/p/DXjuqH9nDVE/')

Returns:
  Likes, comments, views / plays
  Caption, hashtags, @mentions, usertags
  Location: name + GPS coordinates + Google Maps link
  Co-authors, sponsor tags
  For reels: music artist and title
  Exact timestamp (taken_at)
```

### `instagram_post_comments`

```
Parameters:
  post                Shortcode or full URL (/p/, /reel/, /tv/ all accepted)
  max_comments        Number of comments to fetch (1–500, default: 100)
  sort_order          'popular' (most-liked first) or 'recent' (chronological)

Returns:
  Per comment: text, like_count, reply_count, author username, verified flag,
               posting time, GIF indicator, translation flag
  Caption included (is_caption=true)
  Top 5 comments by likes
  Most frequent commenters
  Audience language breakdown (has_translation %)
  
Note: instagram_post returns comment COUNT only.
      Use this tool to fetch actual comment content.
```

### `instagram_tagged_by` 🔐

```
Parameters:
  username            Instagram username
  max_posts           Number of posts (1–200, default: 50)

Returns:
  Posts made by OTHER accounts that tag this profile
  Per post: poster username, shortcode, likes, caption excerpt, timestamp
  This is "passive" — others mentioned us in their posts
```

### `instagram_reposts` 🔐

```
Parameters:
  username            Instagram username
  max_reposts         Number of reposts (1–200, default: 50)

Returns:
  Content this account actively chose to repost
  Per repost: original author, original post URL, likes, caption, timestamp
  This is "active" — the account chose to amplify this content
```

### `instagram_reels` 🔐

```
Parameters:
  username            Instagram username
  max_reels           Number of reels (1–200, default: 50)

Returns:
  play_count (PRIMARY metric — not available anywhere else)
  likes, comments, thumbnail dimensions, timestamp, is_pinned
  Top 5 reels by plays
  Summary: total plays, average plays / likes / comments
```

### `instagram_server`

```
Parameters:
  action              'status' | 'clear_cache' | 'clear_user'
  username            For 'clear_user': the username to evict from cache

Returns (status):
  Cache: hit rate, entry count, size
  Proxies: active, on cooldown, failed
  Rate limiter: current RPS, circuit breaker state
  Server version and transport type
```

---

## Installation

### Requirements

- Python 3.10 or higher
- `uv` package manager (recommended) or `pip`

### Step 1 — Clone the repository

```bash
git clone https://github.com/yourusername/instagram-mcp.git
cd instagram-mcp
```

### Step 2 — Install dependencies

```bash
# With uv (recommended — fast)
uv sync

# With pip
pip install -e .
```

### Step 3 — Verify the installation

```bash
uv run python -c "from instagram_mcp import create_mcp_server; print('OK')"
```

---

## Configuration

All settings are controlled by environment variables. No config files are required — the defaults are production-ready out of the box.

### Minimal setup (anonymous mode)

No configuration needed. Run the server and all 10 anonymous tools are available immediately.

### With cookies (full 13-tool access)

```bash
INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt
```

### With proxies (recommended for bulk operations)

```bash
INSTAGRAM_MCP_PROXIES=http://user:pass@host1:8080,http://user:pass@host2:8080
```

---

## Authentication (Optional)

10 tools work without any login. Authentication is only required for `instagram_tagged_by`, `instagram_reposts`, and `instagram_reels`.

### Exporting cookies

**Method 1: "Get cookies.txt LOCALLY" (Chrome / Firefox)**

1. Install the [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension
2. Log in to Instagram in your browser
3. Click the extension icon → **Export** → save as `cookies.txt`

**Method 2: "EditThisCookie" (JSON format)**

1. Install the EditThisCookie extension
2. Log in to Instagram
3. Click the extension → **Export** → save the JSON as `cookies.json`

Both `.txt` (Netscape format) and `.json` (array format) are supported and auto-detected.

### Placing the cookie file

The server searches for cookie files in this order:

```
1. INSTAGRAM_MCP_COOKIES env var  → explicit path (highest priority)
2. ./cookies.json                 → JSON format, current directory
3. ./cookies.txt                  → Netscape format, current directory
4. ../cookies.json                → parent directory
5. ../cookies.txt                 → parent directory
```

Recommended placement:

```
instagram_mcp/
├── cookies.txt     ← place it here
└── ...
```

### Cookie expiry

Instagram sessions last approximately 90 days. When the session expires:

1. Log in to Instagram in your browser again
2. Re-export `cookies.txt`
3. Replace the old file — the server will pick it up automatically on the next authenticated request (no restart required)

---

## Proxy Setup

Making too many requests from a single IP triggers Instagram's rate limiting (HTTP 429). Proxies distribute requests across multiple IPs.

### proxies.txt file

Create `proxies.txt` in the project root or the `instagram_mcp/` directory:

```
# proxies.txt
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
socks5://user:pass@proxy3.example.com:1080
```

Lines starting with `#` are ignored.

### Via environment variable

```bash
INSTAGRAM_MCP_PROXIES="http://u:p@h1:8080,http://u:p@h2:8080"
```

### Rotation logic

- Requests are distributed across proxies in round-robin order
- A proxy that returns 5 consecutive 429s enters a 30-second cooldown
- If all proxies are in cooldown, the server falls back to a direct connection
- Health checks run every 30 seconds to detect recovered proxies automatically

---

## Connecting to Claude Desktop

Add the following to `~/.config/claude/claude_desktop_config.json` (macOS/Linux) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "instagram": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/instagram_mcp",
        "run",
        "python",
        "-m",
        "instagram_mcp"
      ],
      "env": {
        "INSTAGRAM_MCP_COOKIES": "/absolute/path/to/cookies.txt"
      }
    }
  }
}
```

Remove the `"env"` block if you are running in anonymous mode.

### Claude Code (CLI)

```bash
claude mcp add instagram -- uv --directory /path/to/instagram_mcp run python -m instagram_mcp
```

### HTTP transport (custom integrations)

```bash
INSTAGRAM_MCP_TRANSPORT=http \
INSTAGRAM_MCP_HOST=0.0.0.0 \
INSTAGRAM_MCP_PORT=8000 \
uv run python -m instagram_mcp
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_COOKIES` | `""` | Path to cookies file (`.txt` or `.json`) |
| `INSTAGRAM_MCP_PROXIES` | `""` | Comma-separated proxy URLs, or use `proxies.txt` |
| `INSTAGRAM_MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `INSTAGRAM_MCP_HOST` | `0.0.0.0` | HTTP server bind host |
| `INSTAGRAM_MCP_PORT` | `8000` | HTTP server port |
| `INSTAGRAM_MCP_APP_ID` | `936619743392459` | Instagram app ID header |
| `INSTAGRAM_MCP_IMPERSONATE` | `chrome142` | curl_cffi browser impersonation target |
| `INSTAGRAM_MCP_TIMEOUT` | `10` | Request timeout in seconds |
| `INSTAGRAM_MCP_MAX_RETRIES` | `3` | Maximum retry attempts per request |
| `INSTAGRAM_MCP_MAX_WORKERS` | `12` | Default concurrency for batch operations |
| `INSTAGRAM_MCP_CACHE_DISABLED` | `""` | Set to `1` or `true` to disable caching |
| `INSTAGRAM_MCP_CACHE_TTL` | `300` | Global cache TTL in seconds |
| `INSTAGRAM_MCP_CACHE_MAX` | `500` | Maximum number of cache entries |
| `INSTAGRAM_MCP_RATE_LIMIT_RPS` | `100.0` | Maximum requests per second |
| `INSTAGRAM_MCP_RATE_LIMIT_BURST` | `50` | Token bucket burst size |
| `INSTAGRAM_MCP_RATE_BACKOFF_FACTOR` | `0.7` | Rate multiplier applied on receiving a 429 |
| `INSTAGRAM_MCP_RATE_RECOVERY_FACTOR` | `1.15` | Rate multiplier applied on successful requests |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive 429s before the circuit opens |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN` | `60.0` | Sleep duration when circuit is open (seconds) |
| `INSTAGRAM_MCP_PROXY_MAX_FAILS` | `5` | Consecutive failures before proxy enters cooldown |
| `INSTAGRAM_MCP_PROXY_COOLDOWN` | `30` | Proxy cooldown duration in seconds |
| `INSTAGRAM_MCP_PROXY_MAX_COOLDOWN` | `300.0` | Maximum proxy cooldown in seconds |
| `INSTAGRAM_MCP_REQUEST_JITTER` | `0.1` | Max random jitter added to rate-limiter sleep (seconds) |
| `INSTAGRAM_MCP_GRAPHQL_DOC_ID` | `26442143102071041` | Feed pagination GraphQL doc_id |
| `INSTAGRAM_MCP_MAX_PAGINATION` | `200` | Hard ceiling on paginated post count |

---

## Architecture

### Component overview

```
instagram_mcp/
├── __init__.py         MCP server factory — lifespan, resources, prompts
├── tools.py            13 tool registrations with full docstrings
├── client.py           All Instagram API requests + retry logic
├── parser.py           Raw JSON → typed dataclasses
├── formatter.py        Dataclasses → LLM-readable Markdown
├── models.py           Pydantic input models + internal dataclasses
├── config.py           All settings with env-var overrides
├── cache.py            Async TTL cache with LRU eviction
├── rate_limiter.py     Adaptive token-bucket + circuit breaker
├── proxy_manager.py    Round-robin rotation + health checks
├── cookie_manager.py   Cookie loading (Netscape + JSON) + CSRF tokens
├── exceptions.py       Typed exception hierarchy
├── agents.py           High-level pipeline agents (vetting, audit, discovery)
└── batch_runner.py     Parallel batch scraping engine
```

### Request flow

```
MCP Tool call (tools.py)
    │
    ├── Input validation — Pydantic model
    ├── Rate limiter — token bucket acquire
    │
    ├── Cache lookup (cache.py)
    │   ├── HIT  → return immediately
    │   └── MISS → proceed to API
    │
    ├── Client (client.py)
    │   ├── Select proxy — round-robin (proxy_manager.py)
    │   ├── HTTP request — curl_cffi Chrome impersonation
    │   └── Retry up to 3×, each attempt on a different proxy
    │
    ├── Parser (parser.py) — raw JSON → dataclass
    ├── Formatter (formatter.py) — dataclass → Markdown
    └── MCP ToolResult
```

### API endpoints used

| Endpoint | Auth | Used by |
|----------|------|---------|
| `GET /api/v1/users/web_profile_info/?username={}` | None | Most anonymous tools |
| `POST https://www.instagram.com/graphql/query/` | cookies + CSRF | `instagram_tagged_by`, `instagram_reposts`, `instagram_reels` |
| `GET /api/v1/media/{id}/comments/` | None | `instagram_post_comments` |
| `GET https://www.instagram.com/p/{shortcode}/` | None | `instagram_post` |

### Cache TTL by data type

| Data | TTL |
|------|-----|
| Comments | 1 minute |
| Feed tags | 2 minutes |
| Paginated feed | 3 minutes |
| Profile | 5 minutes |
| Tagged / reposts / reels | 5 minutes |
| Account status | 10 minutes |

### GraphQL endpoints (authenticated tools)

| Tool | `fb_api_req_friendly_name` | `doc_id` |
|------|---------------------------|----------|
| `instagram_feed_deep` (anon) | `PolarisProfilePostsTabContentQuery_connection` | `26442143102071041` |
| `instagram_tagged_by` | `PolarisProfileTaggedTabContentQuery_connection` | `26707104818956021` |
| `instagram_reposts` | `PolarisProfileRepostsTabContentRefetchQuery` | `35095888563388407` |
| `instagram_reels` | `PolarisProfileReelsTabContentQuery_connection` | `26292852833730510` |

### Shortcode to media_id conversion

`instagram_post_comments` converts a shortcode to a numeric `media_id` without making an extra API call. Instagram shortcodes are base-64-encoded media IDs using the alphabet `A-Z a-z 0-9 - _`.

```python
# Example: 'DNnx22NOGnt' → '3704148491870169581'
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
n = 0
for c in shortcode:
    n = n * 64 + ALPHABET.index(c)
media_id = str(n)
```

---

## File Structure

```
MCP/
├── instagram_mcp/          Main package
│   ├── README.md
│   ├── .gitignore
│   ├── __init__.py
│   ├── tools.py
│   ├── client.py
│   ├── parser.py
│   ├── formatter.py
│   ├── models.py
│   ├── config.py
│   ├── cache.py
│   ├── rate_limiter.py
│   ├── proxy_manager.py
│   ├── cookie_manager.py
│   ├── exceptions.py
│   ├── agents.py
│   └── batch_runner.py
├── proxies.txt             Proxy URLs — one per line (optional)
├── cookies.txt             Instagram session cookies (optional)
└── pyproject.toml          Package definition and dependencies
```

---

## FAQ

**Do I need an Instagram account or password?**

No. The 10 anonymous tools require nothing at all. The 3 authenticated tools only need exported browser cookies — the server never sees your login credentials.

**Why is `play_count` missing from `instagram_feed_deep` and `instagram_analyze_engagement`?**

Instagram's main feed API returns `view_count=null` for all reels. Only the dedicated Reels Tab endpoint (`/clips/user/connection/`) exposes real play counts. This is an Instagram API limitation. Use `instagram_reels` (🔐) to get play counts.

**I'm getting HTTP 429 (rate limited). What should I do?**

Add proxies via `proxies.txt` or `INSTAGRAM_MCP_PROXIES`. Each proxy has its own rate limit, so distributing requests across multiple proxies prevents 429s. The adaptive rate limiter will also back off automatically and recover on its own.

**Where does `instagram_batch_scrape` save results?**

If you provide an `output_file` path, results are saved there as JSON. If left empty, the server writes to a temporary file in `/tmp/` and returns the path.

**Do comments require authentication?**

No. `instagram_post_comments` is fully anonymous and works on any public post.

**Can I use HTTP transport instead of STDIO?**

Yes. Set `INSTAGRAM_MCP_TRANSPORT=http`. The server will bind to `INSTAGRAM_MCP_HOST:INSTAGRAM_MCP_PORT` (default `0.0.0.0:8000`).

**How do I clear the cache for a single user?**

Call `instagram_server` with `action="clear_user"` and `username="target_username"`.

---

## License

MIT
