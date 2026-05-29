# instagram-mcp

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![MCP](https://img.shields.io/badge/MCP-compatible-green) ![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey) [![CI](https://github.com/mpython77/instagram-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/mpython77/instagram-mcp/actions/workflows/ci.yml) [![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)](https://github.com/mpython77/instagram-mcp/pkgs/container/instagram-mcp) [![PyPI](https://img.shields.io/badge/PyPI-instamcp-orange)](https://pypi.org/project/instamcp/) [![Smithery](https://img.shields.io/badge/Smithery-kelajak054%2Finstagram--mcp-purple)](https://smithery.ai/servers/kelajak054/instagram-mcp)

Production-grade MCP server for Instagram. **79 tools** — 22 anonymous, 56 authenticated, 1 auto-mode. Built on `curl_cffi` with Chrome TLS impersonation, adaptive rate limiting, smart caching, multi-account pool, and challenge/2FA resolver.

Works with **Claude Desktop**, **Claude Code**, and any MCP-compatible AI client.

---

## Auth Tiers

| Tier | Symbol | Requirement | Tools |
|------|--------|-------------|-------|
| Anonymous | 🌐 | None | 22 |
| Authenticated | 🔐 | `cookies.json` with valid Instagram session | 56 |
| Auto-mode | 🌐/🔐 | Anon by default, upgrades when cookies present | 1 |

---

## Installation

```bash
pip install instamcp
instagram-mcp
```

Or from source:

```bash
git clone https://github.com/mpython77/instagram-mcp.git
cd instagram-mcp
pip install -e .
instagram-mcp
```

With `uv`:

```bash
uv sync && uv run --quiet instagram-mcp
```

### Cookie Setup

1. Log in to Instagram in your browser.
2. Install [Cookie-Editor](https://cookie-editor.com/) and navigate to `instagram.com`.
3. Export cookies as **JSON**.
4. Save as `cookies.json` in the project root, or set `INSTAGRAM_MCP_COOKIES=/path/to/file`.

> Use a dedicated account — not your personal account. Sessions expire; refresh `cookies.json` if you get 401 errors.

---

## MCP Config

### Claude Desktop

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "instagram": {
      "command": "instagram-mcp",
      "env": {
        "INSTAGRAM_MCP_COOKIES": "/absolute/path/to/cookies.json"
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
      "args": ["run", "--project", "/path/to/instagram-mcp", "instagram-mcp"],
      "env": {
        "INSTAGRAM_MCP_COOKIES": "/absolute/path/to/cookies.json"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add instagram instagram-mcp --env INSTAGRAM_MCP_COOKIES=/path/to/cookies.json
```

---

## Docker

```bash
docker run -d \
  -e INSTAGRAM_MCP_COOKIES=/data/cookies.json \
  -v /path/to/cookies.json:/data/cookies.json:ro \
  -p 8000:8000 \
  ghcr.io/mpython77/instagram-mcp:latest
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_COOKIES` | `""` | Path to `cookies.json` or `cookies.txt` |
| `INSTAGRAM_MCP_PROXIES` | `""` | Comma-separated proxy URLs (or use `proxies.txt`, one per line) |
| `INSTAGRAM_MCP_IMPERSONATE` | `chrome142` | curl_cffi impersonation profile |
| `INSTAGRAM_MCP_TIMEOUT` | `10` | Per-request timeout in seconds |
| `INSTAGRAM_MCP_EXPORT_DIR` | `./exports` | Auto-export directory for JSON results |
| `INSTAGRAM_MCP_TOOLSETS` | `all` | Comma-separated toolsets to enable: `profile`, `analysis`, `content`, `social_graph`, `batch`, `server` |
| `INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES` | `""` | Set `1` to hide auth-only tools when no cookies are loaded |
| `INSTAGRAM_MCP_CACHE_DISABLED` | `""` | Set `1` to disable all caching |

---

## Tools

### 🌐 Profile & Feed

| Tool | Description |
|------|-------------|
| `instagram_profile` | Profile metadata, feed tags (up to 12 posts), activity status |
| `instagram_feed_deep` | Paginated feed — up to 200 posts with date filtering |
| `instagram_bulk_check` | Check up to 20 accounts in parallel with activity status |
| `instagram_compare_profiles` | Side-by-side comparison of 2–5 accounts |

### 🌐 Analysis

| Tool | Description |
|------|-------------|
| `instagram_analyze_engagement` | ER%, content mix, best posting days, top hashtags across up to 200 posts |
| `instagram_find_collab_network` | Map usertags, @mentions, co-authors, and paid sponsors across posts |
| `instagram_account_report` | Full profile + engagement + collab network in one call |
| `instagram_caption_analyze` | Caption patterns: avg length, hashtag density, emoji/CTA rates |
| `instagram_compare_followers` | Compare follower/following sets — find unfollowers or non-mutuals |
| `instagram_analyze_comments` | Sentiment analysis on comments — pos/neu/neg, emoji stats, keywords (EN/UZ/RU) |

### 🌐 Content

| Tool | Description |
|------|-------------|
| `instagram_post` | Full post details: location (GPS + Maps), music, usertags, coauthors |
| `instagram_post_bulk` | Fetch up to 50 posts in parallel by shortcode or URL |
| `instagram_post_comments` | Fetch comments with likes and thread structure (up to 500) |

### 🌐/🔐 Hashtag & Discovery

| Tool | Description |
|------|-------------|
| `instagram_hashtag` | Top posts for a hashtag — anon: 12 posts; auth: up to 300. Auto-upgrades. |
| `instagram_hashtag_deep` | Top accounts, content breakdown, best posting hour across up to 500 posts |
| `instagram_niche_top` | Account leaderboard for a hashtag ranked by engagement/post count/total likes |
| `instagram_hashtag_suggest` | Related hashtag suggestions by analyzing top posts under a seed tag |

### 🌐 Threads

| Tool | Description |
|------|-------------|
| `instagram_threads_profile` | Profile metadata from Threads (threads.net) |
| `instagram_threads_posts` | Recent posts for a Threads user |

### 🔐 Authenticated Feed & Activity

| Tool | Description |
|------|-------------|
| `instagram_home_feed` | Your authenticated home feed |
| `instagram_saved_posts` | Your bookmarked posts |
| `instagram_liked_posts` | Posts you have liked |
| `instagram_activity_feed` | Your recent activity notifications |
| `instagram_post_likers` | Users who liked a specific post (up to ~98) |

### 🔐 Discovery

| Tool | Description |
|------|-------------|
| `instagram_similar_accounts` | Accounts Instagram considers similar (internal chaining API) |
| `instagram_search` | Search users + hashtags by keyword |
| `instagram_user_search` | User search with higher-quality ranking (authenticated API) |
| `instagram_user_id_lookup` | Look up a user's numeric ID by username |

### 🔐 Social Graph

| Tool | Description |
|------|-------------|
| `instagram_followers_list` | Recent followers with mutual follow status |
| `instagram_following_list` | Full following list with close-friends detection |
| `instagram_user_followers` | Paginated followers for any user by numeric user_id |
| `instagram_user_following` | Paginated following for any user by numeric user_id |
| `instagram_tagged_by` | Posts by other accounts that tag this account |
| `instagram_reposts` | Content this account actively reposted |

### 🔐 Media

| Tool | Description |
|------|-------------|
| `instagram_reels` | Account's reels with play counts (only tool that exposes `play_count`) |
| `instagram_stories` | Currently active stories (cached 2 min) |
| `instagram_highlights` | Highlights tray + optional story items inside |
| `instagram_audio_reels` | Reels using a specific audio track by `audio_cluster_id` |
| `instagram_location_posts` | Top posts at a location by Instagram location ID or name |
| `instagram_media_insights` | Impressions, reach, saves for your own posts (Business/Creator only) |

### 🔐 Interactions

| Tool | Description |
|------|-------------|
| `instagram_post_like` | Like or unlike a post |
| `instagram_post_save` | Save or unsave (bookmark) a post |
| `instagram_post_comment` | Post a comment |
| `instagram_comment_reply` | Reply to a comment |
| `instagram_comment_like` | Like or unlike a comment |
| `instagram_comment_hide` | Hide a comment on your own post |
| `instagram_delete_comment` | Delete a comment |
| `instagram_toggle_comments` | Disable or enable comments on your post |
| `instagram_post_delete` | Delete one of your own posts |
| `instagram_follow_user` | Follow or unfollow a user |
| `instagram_block_user` | Block or unblock a user |
| `instagram_account_privacy` | Switch account between public and private |
| `instagram_edit_profile` | Edit bio, display name, website, email, or phone |
| `instagram_publish_story` | Publish a photo as a Story (24h) |
| `instagram_story_mark_seen` | Mark stories as viewed |
| `instagram_story_reply` | Reply to a story via DM |

### 🔐 DM

| Tool | Description |
|------|-------------|
| `instagram_dm_inbox` | Read DM inbox (threads list) |
| `instagram_dm_thread` | Fetch messages in a specific thread |
| `instagram_dm_send` | Send a text message |
| `instagram_dm_send_photo` | Send a photo |
| `instagram_dm_send_video` | Send a video |
| `instagram_dm_react` | Add or remove an emoji reaction on a message |
| `instagram_dm_unsend` | Delete a sent message |
| `instagram_dm_mark_seen` | Mark a thread as seen |

### 🔐 Upload & Download

| Tool | Description |
|------|-------------|
| `instagram_upload_photo` | Upload 1–10 images as a post or carousel |
| `instagram_upload_reel` | Upload an MP4 as a Reel |
| `instagram_upload_video` | Upload an MP4 as a regular video post |
| `instagram_download` | Download all media from a post (single/video/carousel) to local disk |

### 🔐 Broadcast & Automation

| Tool | Description |
|------|-------------|
| `instagram_broadcast_channel` | Read a creator's Broadcast Channel (info or messages) |
| `instagram_schedule` | Schedule posts for future publishing (`add`/`list`/`cancel`/`status`) |
| `instagram_monitor` | Poll accounts for new posts; fire webhooks on new post |
| `instagram_sessions` | Manage multiple accounts via `INSTAGRAM_MCP_COOKIES_<ALIAS>` env vars |
| `instagram_oauth` | Full Graph API OAuth 2.0 flow (`init_flow`/`exchange_code`/`refresh_token`/`status`) |

### 🌐 Audience Intelligence

| Tool | Description |
|------|-------------|
| `instagram_best_time_to_post` | Analyze post timestamps to find optimal posting times (UTC hours/days) |

### 🌐 Batch & Server

| Tool | Description |
|------|-------------|
| `instagram_batch_scrape` | Scrape up to 2000 profiles; `profile_only=True` gives 30–60× speedup |
| `instagram_server` | Diagnostics and cache management (`status`/`clear_cache`/`clear_user`/`reload_cookies`) |
| `instagram_metrics` | View or reset request metrics (counts, durations, error rates, cache stats) |
| `instagram_plugins` | List loaded third-party plugins |
| `instagram_submit_verification_code` | Submit SMS/Email/2FA code to resolve a pending checkpoint and restore the session |

---

## Tool Annotations

Each tool declares MCP-standard annotation hints so hosts can render them with the correct UX (write warnings on destructive ops, fast-path on read-only ops). Generated from the runtime tool inventory.

### profile

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_bulk_check` | ✓ | ✓ | ✗ | ✓ |
| `instagram_compare_profiles` | ✓ | ✓ | ✗ | ✓ |
| `instagram_feed_deep` | ✓ | ✓ | ✗ | ✓ |
| `instagram_profile` | ✓ | ✓ | ✗ | ✓ |
| `instagram_threads_posts` | ✓ | ✓ | ✗ | ✓ |
| `instagram_threads_profile` | ✓ | ✓ | ✗ | ✓ |

### analysis

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_account_report` | ✓ | ✓ | ✗ | ✓ |
| `instagram_analyze_comments` | ✓ | ✓ | ✗ | ✓ |
| `instagram_analyze_engagement` | ✓ | ✓ | ✗ | ✓ |
| `instagram_caption_analyze` | ✓ | ✓ | ✗ | ✓ |
| `instagram_find_collab_network` | ✓ | ✓ | ✗ | ✓ |
| `instagram_hashtag_suggest` | ✓ | ✓ | ✗ | ✓ |

### content

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_audio_reels` | ✓ | ✓ | ✗ | ✓ |
| `instagram_hashtag` | ✓ | ✓ | ✗ | ✓ |
| `instagram_hashtag_deep` | ✓ | ✓ | ✗ | ✓ |
| `instagram_highlights` | ✓ | ✓ | ✗ | ✓ |
| `instagram_location_posts` | ✓ | ✓ | ✗ | ✓ |
| `instagram_niche_top` | ✓ | ✓ | ✗ | ✓ |
| `instagram_post` | ✓ | ✓ | ✗ | ✓ |
| `instagram_post_bulk` | ✓ | ✓ | ✗ | ✓ |
| `instagram_post_comments` | ✓ | ✓ | ✗ | ✓ |
| `instagram_reels` | ✓ | ✓ | ✗ | ✓ |
| `instagram_reposts` | ✓ | ✓ | ✗ | ✓ |
| `instagram_stories` | ✓ | ✓ | ✗ | ✓ |
| `instagram_tagged_by` | ✓ | ✓ | ✗ | ✓ |

### social_graph

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_account_privacy` | ✗ | ✗ | ✓ | ✓ |
| `instagram_activity_feed` | ✓ | ✓ | ✗ | ✗ |
| `instagram_block_user` | ✗ | ✓ | ✓ | ✗ |
| `instagram_broadcast_channel` | ✗ | ✗ | ✓ | ✓ |
| `instagram_comment_hide` | ✗ | ✗ | ✓ | ✓ |
| `instagram_comment_like` | ✗ | ✗ | ✓ | ✓ |
| `instagram_comment_reply` | ✗ | ✗ | ✗ | ✗ |
| `instagram_compare_followers` | ✓ | ✓ | ✗ | ✗ |
| `instagram_delete_comment` | ✗ | ✗ | ✓ | ✗ |
| `instagram_edit_profile` | ✗ | ✗ | ✓ | ✓ |
| `instagram_follow_user` | ✗ | ✗ | ✓ | ✓ |
| `instagram_followers_list` | ✓ | ✓ | ✗ | ✓ |
| `instagram_following_list` | ✓ | ✓ | ✗ | ✓ |
| `instagram_home_feed` | ✓ | ✓ | ✗ | ✗ |
| `instagram_liked_posts` | ✓ | ✓ | ✗ | ✗ |
| `instagram_media_insights` | ✓ | ✓ | ✗ | ✗ |
| `instagram_post_comment` | ✗ | ✗ | ✗ | ✗ |
| `instagram_post_delete` | ✗ | ✗ | ✓ | ✗ |
| `instagram_post_like` | ✗ | ✗ | ✓ | ✓ |
| `instagram_post_likers` | ✓ | ✓ | ✗ | ✓ |
| `instagram_post_save` | ✗ | ✗ | ✓ | ✓ |
| `instagram_publish_story` | ✗ | ✗ | ✗ | ✗ |
| `instagram_saved_posts` | ✓ | ✓ | ✗ | ✗ |
| `instagram_search` | ✓ | ✓ | ✗ | ✓ |
| `instagram_similar_accounts` | ✓ | ✓ | ✗ | ✓ |
| `instagram_story_mark_seen` | ✗ | ✗ | ✓ | ✓ |
| `instagram_story_reply` | ✗ | ✗ | ✗ | ✗ |
| `instagram_submit_verification_code` | ✗ | ✗ | ✗ | ✗ |
| `instagram_toggle_comments` | ✗ | ✗ | ✓ | ✓ |
| `instagram_upload_video` | ✗ | ✗ | ✗ | ✗ |
| `instagram_user_followers` | ✓ | ✓ | ✗ | ✗ |
| `instagram_user_following` | ✓ | ✓ | ✗ | ✗ |
| `instagram_user_id_lookup` | ✓ | ✓ | ✗ | ✗ |
| `instagram_user_search` | ✓ | ✓ | ✗ | ✗ |

### dm

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_dm_inbox` | ✓ | ✓ | ✗ | ✗ |
| `instagram_dm_mark_seen` | ✗ | ✗ | ✓ | ✗ |
| `instagram_dm_react` | ✗ | ✗ | ✓ | ✗ |
| `instagram_dm_send` | ✗ | ✗ | ✓ | ✗ |
| `instagram_dm_send_photo` | ✗ | ✗ | ✓ | ✗ |
| `instagram_dm_send_video` | ✗ | ✗ | ✓ | ✗ |
| `instagram_dm_thread` | ✓ | ✓ | ✗ | ✗ |
| `instagram_dm_unsend` | ✗ | ✗ | ✓ | ✗ |

### upload

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_download` | ✓ | ✓ | ✗ | ✓ |
| `instagram_upload_photo` | ✗ | ✗ | ✓ | ✓ |
| `instagram_upload_reel` | ✗ | ✗ | ✓ | ✓ |

### automation

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_batch_scrape` | ✓ | ✗ | ✗ | ✓ |
| `instagram_monitor` | ✗ | ✗ | ✗ | ✓ |
| `instagram_oauth` | ✗ | ✗ | ✗ | ✓ |
| `instagram_schedule` | ✗ | ✗ | ✗ | ✗ |
| `instagram_sessions` | ✗ | ✗ | ✗ | ✗ |

### audience

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_best_time_to_post` | ✓ | ✓ | ✗ | ✓ |

### server

| Tool | readOnly | idempotent | destructive | openWorld |
|------|:---:|:---:|:---:|:---:|
| `instagram_server` | ✓ | ✓ | ✗ | ✗ |
| `instagram_metrics` | ✓ | ✓ | ✗ | ✗ |
| `instagram_plugins` | ✓ | ✓ | ✗ | ✗ |

---

## Resources

The server exposes three MCP Resources for direct AI consumption (no tool call required):

| URI Template | Name | Description | MIME Type |
|---|---|---|---|
| `instagram://profile/{username}` | Instagram Profile Cache | Cached profile data; refreshes via API on miss | application/json |
| `instagram://feed/{username}` | Instagram Feed Cache | Cached recent feed (tags, posts) | application/json |
| `instagram://server/status` | Instagram MCP Server Status | Live cache hit rate, proxy health, rate limiter stats | application/json |

---

## Prompts

The server exposes ready-made LLM workflow templates:

| Name | Parameters (with defaults) | Description |
|---|---|---|
| `analyze_influencer` | `username`, `niche=""`, `goal="brand partnership"` | Full influencer vetting pipeline: profile, engagement, collab network, scored verdict. |
| `find_brand_collaborations` | `username`, `max_posts=100` | Discover brand deals, paid sponsors, recurring brand mentions. |
| `competitive_analysis` | `usernames` (comma-separated), `metric_focus="engagement"` | Compare 2-5 accounts for competitive intelligence. |
| `account_audit` | `username`, `dead_threshold_days=365` | Health audit: activity status, growth signals, content consistency. |
| `discover_creators` | `seed_username`, `min_followers=1000`, `min_frequency=2`, `max_posts=50` | Find similar creators by traversing the seed account's tag network. |
| `validate_prospect_list` | `usernames` (comma-separated), `min_followers=1000`, `goal="influencer outreach"` | Score and rank a prospect list for outreach qualification. |

---

## Limitations

- **Private accounts:** Feed, posts, stories, and highlights are not accessible without following the account.
- **Follower pagination:** Restricted to ~50 for other accounts; unlimited only for your own account.
- **Play counts:** Only `instagram_reels` exposes `play_count` — the standard feed API omits it.
- **Session expiry:** Cookies expire. Use `instagram_server action=reload_cookies` to refresh without restarting.
- **Write operations:** Like, comment, follow, upload, DM actions — rate limits apply. Avoid rapid consecutive writes.
- **Anonymous hashtag blocks:** Some hashtags (#swimwear, #fitness, etc.) block anonymous HTML scraping.
- **Account restrictions:** Comment, reply, and follow may return "something went wrong" on new or restricted accounts — this is Instagram-side, not a bug.

---

## Error Taxonomy

Every `ToolError` raised by this server carries one of these `error_type` values:

| `error_type` | Description | Typical example |
|---|---|---|
| `validation_error` | Input parameter violated a constraint. | Empty username, unknown action verb. |
| `not_found` | Target resource does not exist on Instagram. | `@user` not found (404), post deleted. |
| `private_account` | Target is private; data not accessible. | Public profile metadata returned, but feed denied. |
| `auth_required` | Tool needs valid cookies; none loaded. | `instagram_dm_send` called anonymously. |
| `rate_limited` | All proxies exhausted by 429 responses. | Sustained 429s open the circuit breaker. |
| `network_error` | Proxy / TLS / DNS failure outside Instagram's control. | All configured proxies unhealthy. |
| `fetch_error` | HTTP request failed for non-rate, non-network reasons. | Unexpected 500, malformed JSON. |
| `unexpected_error` | Catch-all for unmapped Python exceptions. | Programmer error surfaced as a tool error. |

---

## Pre-commit Setup (recommended)

Install the pre-commit hook to block accidental commits of cookies, `*.env`, or `secrets.*` files:

```bash
pip install pre-commit
pre-commit install
```

The hooks run automatically on `git commit`. See `SECURITY.md` for the full secret policy and incident playbook.

---

## FAQ

**Do I need to log in?**  
No. 22 tools work anonymously with no credentials. 56 tools require `cookies.json`. `instagram_hashtag` auto-switches based on whether cookies are present.

**Why `curl_cffi` instead of `requests`?**  
Instagram blocks `requests` and `aiohttp` at the TLS handshake level by JA3/JA4 fingerprint. `curl_cffi` impersonates Chrome's TLS stack, bypassing this check.

**How do I get play counts for reels?**  
Use `instagram_reels`. The standard feed API (`instagram_feed_deep`) does not include `play_count`.

**What happens on rate limiting?**  
The adaptive rate limiter detects 429s, backs off (×0.7 RPS), rotates proxy, and retries. After 5 consecutive 429s the circuit breaker opens for 60s.

**How do I use multiple Instagram accounts?**  
Set `INSTAGRAM_MCP_COOKIES_<ALIAS>` env vars (e.g., `INSTAGRAM_MCP_COOKIES_BRAND=cookies_brand.json`). Use `instagram_sessions action=list` to see available sessions.

**Are `exports/` files safe to commit?**  
No — they may contain PII. Add `exports/` to `.gitignore`.

**How do I refresh cookies without restarting?**  
`instagram_server action=reload_cookies`
