# instagram-mcp

**Version 1.0.0**

A production-grade MCP (Model Context Protocol) server for fetching Instagram data. No login required for 10 tools; all 13 tools available with an authenticated session cookie.

Works natively with Claude Desktop, Claude Code, and any MCP-compatible AI assistant.

---

## Table of Contents

1. [What Is This?](#what-is-this)
2. [Features at a Glance](#features-at-a-glance)
3. [Tool Overview](#tool-overview)
4. [Tool Reference — Anonymous (🌐)](#tool-reference--anonymous-)
   - [instagram_profile](#instagram_profile)
   - [instagram_feed_deep](#instagram_feed_deep)
   - [instagram_analyze_engagement](#instagram_analyze_engagement)
   - [instagram_find_collab_network](#instagram_find_collab_network)
   - [instagram_compare_profiles](#instagram_compare_profiles)
   - [instagram_bulk_check](#instagram_bulk_check)
   - [instagram_batch_scrape](#instagram_batch_scrape)
   - [instagram_server](#instagram_server)
   - [instagram_post](#instagram_post)
   - [instagram_post_comments](#instagram_post_comments)
5. [Tool Reference — Authenticated (🔐)](#tool-reference--authenticated-)
   - [instagram_tagged_by](#instagram_tagged_by)
   - [instagram_reposts](#instagram_reposts)
   - [instagram_reels](#instagram_reels)
6. [MCP Resources](#mcp-resources)
7. [MCP Prompts](#mcp-prompts)
8. [Programmatic Agents](#programmatic-agents)
9. [Account Scoring Formula](#account-scoring-formula)
10. [Data Models](#data-models)
11. [Error Types](#error-types)
12. [Tool Decision Guide](#tool-decision-guide)
13. [Installation](#installation)
14. [Configuration](#configuration)
15. [Authentication](#authentication)
16. [Proxy Setup](#proxy-setup)
17. [Connecting to Claude Desktop](#connecting-to-claude-desktop)
18. [Environment Variables](#environment-variables)
19. [Architecture](#architecture)
20. [Limitations](#limitations)
21. [FAQ](#faq)

---

## What Is This?

`instagram-mcp` is a server that speaks the [Model Context Protocol](https://modelcontextprotocol.io/). It gives AI assistants — Claude, GPT, or any MCP client — structured access to public Instagram data through a set of clearly defined tools.

**It is not a scraper framework.** It is a tool suite designed for AI-driven workflows: vetting influencers, auditing account health, mapping collaboration networks, analyzing content strategies, and bulk-processing prospect lists.

All returned data is formatted as LLM-readable Markdown with tables and summaries. No raw JSON is ever returned to the AI — the server parses, structures, and formats everything internally.

---

## Features at a Glance

| Feature | Detail |
|---------|--------|
| Tools | 13 MCP tools across two auth tiers |
| Anonymous tools | 10 — no login, no cookies, no credentials |
| Authenticated tools | 3 — require exported browser cookies |
| Max posts (paginated) | 200 via GraphQL cursor pagination |
| Batch capacity | Up to 500 profiles in one `batch_scrape` call |
| Cache | Per-type TTL cache — repeated lookups are instant |
| Rate limiter | Adaptive token-bucket + circuit breaker + jitter |
| Proxy support | Round-robin rotation, health checks, auto-fallback |
| Bot detection bypass | `curl_cffi` Chrome 142 impersonation |
| Transport | STDIO (Claude Desktop/Code) and HTTP |
| MCP Resources | 3 live resources (profile, feed, server status) |
| MCP Prompts | 6 ready-to-use workflow templates |
| Programmatic agents | 5 Python agents for use outside MCP |

---

## Tool Overview

### Anonymous Tools (🌐 — no login required)

| # | Tool | Purpose |
|---|------|---------|
| 1 | `instagram_profile` | Profile metadata + recent post tags + activity status |
| 2 | `instagram_feed_deep` | Deep paginated feed — up to 200 posts |
| 3 | `instagram_analyze_engagement` | ER%, content mix, best days, top posts, hashtags |
| 4 | `instagram_find_collab_network` | Usertags, mentions, co-authors, paid sponsors |
| 5 | `instagram_compare_profiles` | Side-by-side table for 2–5 accounts |
| 6 | `instagram_bulk_check` | Status + follower check for up to 20 accounts |
| 7 | `instagram_batch_scrape` | Up to 500 profiles, parallel, date-filtered |
| 8 | `instagram_server` | Cache stats + proxy health + rate limiter state |
| 9 | `instagram_post` | Full single-post details: location, caption, music |
| 10 | `instagram_post_comments` | Comments with per-comment likes, replies, GIF detection |

### Authenticated Tools (🔐 — requires cookies.txt)

| # | Tool | Purpose |
|---|------|---------|
| 11 | `instagram_tagged_by` | Posts by OTHERS that tag this account (Tagged Tab) |
| 12 | `instagram_reposts` | Content this account reposted from others (Reposts Tab) |
| 13 | `instagram_reels` | Own reels with **play counts** — the only endpoint that exposes them |

> **`play_count` note:** The standard feed API returns `view_count=null` for all reels. Only the Reels Tab endpoint (`PolarisProfileReelsTabContentQuery_connection`) exposes true play counts. `instagram_reels` is the only tool in this server — or anywhere in the public tooling ecosystem — that surfaces this metric.

---

## Tool Reference — Anonymous (🌐)

### `instagram_profile`

Fetches a public account's profile data. One API call covers profile, feed tags, @mentions, and activity status.

**Modes (controlled by `include_feed` + `check_alive`):**

| Mode | include_feed | check_alive | Use case |
|------|-------------|-------------|----------|
| Full (default) | `true` | `true` | Profile + recent tags + activity |
| Status check | `false` | `true` | Fastest alive/dead check |
| Profile only | `false` | `false` | Bio + followers, no post data |
| Tags only | `true` | `false` | Tags without dead-account logic |

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username (without @) |
| `include_feed` | bool | `true` | Fetch recent post tags |
| `max_feed_posts` | int 1–12 | `12` | Number of posts to extract tags from |
| `check_alive` | bool | `true` | Classify account as active/dead |
| `dead_threshold_days` | int 30–3650 | `365` | Days without a post = "dead" |
| `max_age_days` | int 1–365 | `4` | Only include posts newer than N days |
| `since_timestamp` | int | `0` | Unix timestamp lower bound |
| `until_timestamp` | int | `0` | Unix timestamp upper bound |

**Returns:**

- `followers`, `following`, `posts_count`, `bio`, `website`, `category`
- `is_verified`, `is_business`, `is_professional`, `is_private`
- `highlight_count`, `has_reels`, `city`, `external_url`
- `hashtags[]` and `@mentions[]` extracted from the most recent posts
- `tag_shortcodes{}` — maps each tag to the post shortcode it appeared in
- `last_post_days` — days since the most recent post
- `is_dead` — `true` if `last_post_days > dead_threshold_days`

**Private accounts:** Always returns profile metadata; feed tags are skipped.

**Not found:** Raises a `ToolError` (except in status-check mode, which returns content).

---

### `instagram_feed_deep`

Paginated feed analysis. Starts with the first 12 posts from the profile endpoint, then continues via GraphQL cursor until `max_posts` is reached or there are no more posts.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username |
| `max_posts` | int 1–200 | `50` | Maximum posts to fetch |
| `max_age_days` | int 1–365 | `30` | Discard posts older than N days |
| `since_date` | str | `""` | Start date filter (DD.MM.YYYY) |
| `until_date` | str | `""` | End date filter (DD.MM.YYYY) |

**Returns per post:**

- `shortcode`, `post_url` — direct link to the post
- `media_type` — `image` / `video` / `carousel` / `reel`
- `likes`, `comments`
- `caption` — full caption text
- `hashtags[]`, `mentions[]`, `usertags[]`, `coauthors[]`, `sponsor_tags[]`
- `taken_at` — Unix timestamp, `taken_at_str` — human-readable UTC
- `display_url` — thumbnail image URL

**Also returns:** `pages_fetched` (number of GraphQL pages fetched), `has_more` (whether more posts exist beyond the requested limit)

---

### `instagram_analyze_engagement`

Computes engagement metrics over a post sample. Uses the same pagination engine as `instagram_feed_deep`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username |
| `max_posts` | int 1–200 | `50` | Posts to analyze |
| `max_age_days` | int 1–365 | `90` | Age cutoff for posts |

**Returns:**

- **Engagement rate %** — `(avg_likes + avg_comments) / followers × 100`
  - Benchmarks: Excellent ≥ 6% · Good 3–6% · Average 1–3% · Low < 1%
- **Content mix** — image / video / carousel / reel as percentages
- **Best posting days** — weekday breakdown by average engagement
- **Top 5 posts** — ranked by `likes + comments`
- **Top 10 hashtags** — ranked by frequency across analyzed posts
- **Averages** — avg likes, avg comments, median engagement rate

---

### `instagram_find_collab_network`

Maps every person or brand this account has publicly associated with across recent posts. Useful for finding hidden brand deals, recurring collaborators, and paid partner patterns.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username |
| `max_posts` | int 1–200 | `50` | Posts to scan |
| `max_age_days` | int 1–365 | `90` | Age cutoff for posts |
| `min_frequency` | int ≥ 1 | `1` | Minimum appearances to be included |

**Returns four categories:**

| Category | What it means |
|----------|--------------|
| `usertags` | Other accounts **tagged in photos/videos** by this account |
| `@mentions` | Other accounts **mentioned in captions** by this account |
| `coauthors` | Accounts listed as **co-creators** on a post |
| `paid_sponsors` | Accounts disclosed as **paid partnerships** |

Each entry includes the frequency (how many posts they appeared in) and the shortcodes of those posts.

---

### `instagram_compare_profiles`

Fetches 2–5 accounts in parallel and renders a side-by-side comparison table.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `usernames` | list[str] | required | 2–5 Instagram usernames |

**Returns a table with per-account columns:**

- Followers, following, posts count
- Account type (personal / business / creator)
- Verified, private flags
- Category, city
- Last post days, is_dead
- Bio excerpt

---

### `instagram_bulk_check`

Checks up to 20 accounts in parallel. Each check is a lightweight profile fetch — much faster than running `instagram_profile` 20 times sequentially.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `usernames` | list[str] | required | Up to 20 usernames |
| `check_alive` | bool | `true` | Include dead/active classification |
| `dead_threshold_days` | int | `365` | Days without post = dead |

**Returns per account:**

- `status` — `active` / `dead` / `private` / `not_found`
- `followers`, `following`, `posts_count`
- `last_post_days`
- `is_verified`, `is_private`

---

### `instagram_batch_scrape`

Large-scale parallel scraping. Designed for processing prospect lists, CRM enrichment, and bulk data collection jobs.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `targets` | list[str] | required | Up to 500 usernames |
| `since_date` | str | `""` | Include posts after this date (DD.MM.YYYY) |
| `until_date` | str | `""` | Include posts before this date (DD.MM.YYYY) |
| `max_workers` | int 1–20 | `10` | Parallel worker threads |
| `use_cookies` | bool | `false` | Use authenticated session if available |
| `output_file` | str | `""` | Save path for JSON output (empty = temp file) |

**Returns:**

- Path to the output JSON file
- Summary: total processed, successful, failed, skipped (private/not_found)
- Elapsed time

**Output JSON structure per account:**
```json
{
  "username": "example",
  "found": true,
  "is_private": false,
  "followers": 125000,
  "following": 890,
  "posts_count": 347,
  "is_verified": false,
  "last_post_days": 3,
  "is_dead": false,
  "category": "Creator",
  "bio": "...",
  "website": "...",
  "hashtags": ["travel", "photography"],
  "mentions": ["brand1", "brand2"],
  "posts": [...]
}
```

---

### `instagram_server`

Server diagnostics and cache management.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | str | `"status"` | `status` / `clear_cache` / `clear_user` |
| `username` | str | `""` | For `clear_user`: the username to evict |

**`status` returns:**
- Cache: hit rate %, total entries, estimated memory size
- Per-proxy status: active / on cooldown / failed, request counts, last error
- Rate limiter: current RPS, burst tokens remaining, circuit breaker state (closed/open)
- Server: version, uptime, transport type (stdio/http)

**`clear_cache`:** Flushes the entire in-memory cache.

**`clear_user`:** Evicts all cache keys for a single username (profile + feed + tagged + reposts + reels + comments).

---

### `instagram_post`

Fetches full details for a single post by shortcode or URL. Uses public HTML parsing — no cookies required.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Shortcode (`DXjuqH9nDVE`) or full URL |

**Accepted URL formats:**
- `https://www.instagram.com/p/DXjuqH9nDVE/`
- `https://www.instagram.com/reel/DXjuqH9nDVE/`

**Returns:**

| Field | Description |
|-------|-------------|
| `shortcode`, `post_url` | Post identifier and direct link |
| `media_type` | `image` / `video` / `carousel` / `reel` |
| `username`, `user_id`, `full_name`, `is_verified` | Author info |
| `likes`, `comments`, `view_count`, `play_count` | Engagement metrics |
| `carousel_count` | Number of slides (carousel only) |
| `caption` | Full caption text |
| `hashtags[]` | All hashtags extracted from caption |
| `mentions[]` | All @mentions extracted from caption |
| `usertags[]` | Accounts tagged in the photo/video |
| `coauthors[]` | Co-creator usernames |
| `sponsor_tags[]` | Paid partnership disclosures |
| `display_url` | Thumbnail/cover image URL |
| `width`, `height` | Media dimensions in pixels |
| `duration_secs` | Video/reel duration |
| `taken_at` | Unix timestamp (exact, from page HTML) |
| `taken_at_str` | UTC formatted: `"YYYY-MM-DD HH:MM UTC"` |
| `location.name` | Location tag name |
| `location.lat`, `location.lng` | GPS coordinates |
| `location.maps_url` | Pre-built Google Maps link |
| `music_artist`, `music_title` | Audio track info (reels only) |

---

### `instagram_post_comments`

Fetches comments on a public post with per-comment metadata. The shortcode is converted to a `media_id` internally without an extra API call.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post` | str | required | Shortcode or full URL (`/p/`, `/reel/`, `/tv/`) |
| `max_comments` | int 1–500 | `100` | Maximum comments to fetch |
| `sort_order` | str | `"popular"` | `popular` (most-liked first) or `recent` (chronological) |

**`sort_order` guidance:**
- `popular` — Best for finding the most-resonant audience reactions, top quotes, and influential comments
- `recent` — Best for monitoring live activity, finding the latest replies, tracking comment velocity

**Returns per comment:**

| Field | Description |
|-------|-------------|
| `text` | Comment text (empty string for GIF-only comments) |
| `comment_like_count` | Likes on this specific comment |
| `child_comment_count` | Number of threaded replies |
| `comment_index` | Sequential position in the full comment list |
| `username`, `user_id`, `full_name` | Commenter identity |
| `is_verified` | Whether the commenter is verified |
| `is_private` | Whether the commenter's account is private |
| `has_translation` | `true` if Instagram auto-detected non-English text |
| `has_gif` | `true` if this is a GIF-only comment |
| `gif_url` | URL of the GIF image (if `has_gif=true`) |
| `created_at` | Unix timestamp |
| `created_at_str` | UTC formatted |
| `is_caption` | `true` for the post's own caption (always included first) |

**Also returns:**
- Total comment count on the post
- Top 5 comments by `comment_like_count`
- Most frequent commenters (useful for identifying super-fans)
- Non-English percentage (`has_translation` rate — proxy for audience language diversity)

> **Note:** `instagram_post` returns the comment **count** only. Use `instagram_post_comments` to fetch the actual comment content.

---

## Tool Reference — Authenticated (🔐)

These three tools use Instagram's internal GraphQL API (`POST /graphql/query/`) with a user session. They require `cookies.txt` — see the [Authentication](#authentication) section.

---

### `instagram_tagged_by`

Fetches posts made by **other accounts** that tag this account. This is the "passive" view — content where the queried account was mentioned by someone else.

Uses `PolarisProfileTaggedTabContentQuery_connection` (`doc_id: 26707104818956021`).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username |
| `max_posts` | int 1–200 | `50` | Maximum tagged posts to fetch |

**Returns per tagged post:**

- `poster_username` — who made the post (not the queried account)
- `shortcode`, `post_url`
- `media_type` — image / video / carousel
- `likes`, `comments`
- `caption` — first 200 characters
- `taken_at_str` — estimated from media pk

**Pagination:** GraphQL `end_cursor` / `has_next_page` — same mechanism as the feed.

---

### `instagram_reposts`

Fetches content this account has **actively reposted** from others. This is the "active" view — content the queried account chose to amplify.

Uses `PolarisProfileRepostsTabContentRefetchQuery` (`doc_id: 35095888563388407`).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username |
| `max_reposts` | int 1–200 | `50` | Maximum reposts to fetch |

**Returns per repost:**

- `orig_username` — the original content creator
- `orig_user_id` — their user ID
- `shortcode`, `post_url` — link to the original post
- `media_type`, `post_type`, `product_type`
- `likes`, `comments`, `view_count`
- `caption` — first 200 characters of original caption
- `taken_at_str` — estimated from media pk

**Pagination:** `max_id` cursor (different from Tagged Tab's GraphQL cursor).

---

### `instagram_reels`

Fetches the account's own reels with **play counts**. This is the only endpoint in this server that exposes `play_count` — the primary reel performance metric.

Uses `PolarisProfileReelsTabContentQuery_connection` (`doc_id: 26292852833730510`).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | str | required | Instagram username |
| `max_reels` | int 1–200 | `50` | Maximum reels to fetch |

**Returns per reel:**

| Field | Description |
|-------|-------------|
| `shortcode`, `post_url` | Reel identifier and direct link |
| `pk` | Numeric media ID |
| `play_count` | **Primary metric** — total plays (NOT available via feed API) |
| `like_count` | Likes on this reel |
| `comment_count` | Comment count |
| `coauthor_ids[]` | Co-creator user IDs |
| `thumbnail_url` | Cover image URL |
| `width`, `height` | Video dimensions |
| `taken_at` | Unix timestamp (estimated from pk) |
| `taken_at_str` | UTC formatted |
| `is_pinned` | Whether the reel is pinned to the top |

**Also returns:**
- Total plays across all fetched reels
- Average plays per reel
- Average likes per reel, average comments per reel
- Top 5 reels by `play_count`

**Note:** `view_count` is always `null` in this API — only `play_count` is populated. This is correct behavior, not a bug.

---

## MCP Resources

MCP Resources are readable data endpoints — the AI client can read them directly without calling a tool. They are useful for quickly accessing cached data or embedding profile snapshots into context.

### `instagram://profile/{username}`

Returns cached profile data as JSON. If the profile is not in cache, it fetches from the API first.

```
URI:       instagram://profile/nike
MIME type: application/json
Returns:   { cached: bool, found: bool, username: str, profile: {...} }
```

### `instagram://feed/{username}`

Returns cached first-page feed tag data as JSON.

```
URI:       instagram://feed/nike
MIME type: application/json
Returns:   { found: bool, is_private: bool, tags: [...], posts: [...] }
```

### `instagram://server/status`

Returns live server diagnostics as JSON — cache stats, proxy health, rate limiter state.

```
URI:       instagram://server/status
MIME type: application/json
Returns:   { cache: {...}, proxies: [...], rate_limiter: {...} }
```

---

## MCP Prompts

MCP Prompts are reusable instruction templates. Select them from the prompt picker in Claude Desktop or invoke them by name. They orchestrate multiple tool calls into a complete analysis workflow.

### `analyze_influencer`

**Purpose:** Full influencer vetting for brand partnerships or sponsorships.

**Parameters:** `username`, `niche` (optional), `goal` (default: "brand partnership")

**Workflow:**
1. `instagram_profile` → profile snapshot + activity status
2. `instagram_analyze_engagement` → ER%, content mix, top posts
3. `instagram_find_collab_network` → existing brand relationships

**Output:** Profile overview · Engagement quality · Collaboration network · Audience signals · Verdict (Recommended / Conditional / Not Recommended)

---

### `find_brand_collaborations`

**Purpose:** Map every brand deal, paid sponsor, and recurring mention from an account's history.

**Parameters:** `username`, `max_posts` (default: 100)

**Workflow:**
1. `instagram_find_collab_network` → wide scan for all associations
2. `instagram_feed_deep` → full captions for keyword-based brand detection

**Output:** Paid partnerships list · Organic brand mentions · Partnership frequency timeline · Paid vs organic categorisation

---

### `competitive_analysis`

**Purpose:** Compare 2–5 Instagram accounts for competitive intelligence.

**Parameters:** `usernames` (comma-separated), `metric_focus` (default: "engagement")

**Workflow:**
1. `instagram_compare_profiles` → side-by-side table
2. `instagram_analyze_engagement` → ER% for top 3 accounts by followers
3. `instagram_find_collab_network` → brand strategy of the market leader

**Output:** Rankings table · Key differentiators · Focused metric breakdown · Leader's brand strategy · Top 3 strategic takeaways

---

### `account_audit`

**Purpose:** Full health audit of a single account — activity, growth signals, red flags.

**Parameters:** `username`, `dead_threshold_days` (default: 365)

**Workflow:**
1. `instagram_profile` (status mode) → activity status + last post age
2. `instagram_profile` (full mode) → bio, tags, pinned post detection
3. `instagram_analyze_engagement` → ER%, content consistency, posting frequency

**Output:** Account health status · Growth signals · Content consistency · Red flags checklist · Overall verdict (Healthy / Needs Attention / Problematic)

**Red flags automatically checked:**
- `following >> followers` (potential bot/spam behaviour)
- `ER < 1%` despite large following
- Posting gaps > 60 days
- Empty bio or no website
- Very new account with unusually high follower count

---

### `discover_creators`

**Purpose:** Find similar creators by traversing the tag network of a seed account.

**Parameters:** `seed_username`, `min_followers` (default: 1000), `min_frequency` (default: 2), `max_posts` (default: 50)

**Workflow:**
1. `instagram_find_collab_network` → extract everyone the seed account interacts with
2. `instagram_profile` × N → filter to public + active + followers ≥ threshold
3. `instagram_analyze_engagement` × top 5 → add ER% for accurate ranking

**Output:** Ranked creator table (frequency × followers × engagement) · How each creator was discovered (usertag / mention / co-author / sponsor) · Network insights

---

### `validate_prospect_list`

**Purpose:** Score and rank a list of accounts for outreach qualification.

**Parameters:** `usernames` (comma-separated), `min_followers` (default: 1000), `goal` (default: "influencer outreach")

**Workflow:**
1. `instagram_bulk_check` → quick parallel status filter
2. Filter out: not_found, private, dead, below min_followers
3. `instagram_analyze_engagement` × remaining → ER% for scoring
4. Score each account 0–100 using the formula below

**Output:** Qualified prospects ranked by score · Disqualified list with reasons · Top 3 recommendations with rationale

---

## Programmatic Agents

These agents are Python classes that can be used directly in scripts, cron jobs, or custom integrations — independent of the MCP transport layer. They orchestrate multiple API calls and return structured result objects.

```python
from instagram_mcp import create_mcp_server
from instagram_mcp.agents import InfluencerVettingAgent
from instagram_mcp.client import InstagramClient
from instagram_mcp.config import MCPConfig

config = MCPConfig.from_env()
client = InstagramClient(config=config, ...)

agent = InfluencerVettingAgent(client, config)
result = await agent.run("nike", goal="brand partnership", max_posts=50)
print(result.score, result.verdict)
```

**Progress callback (optional):**

```python
async def on_progress(current: int, total: int, message: str) -> None:
    print(f"[{current}/{total}] {message}")

result = await agent.run("nike", progress_cb=on_progress)
```

---

### `InfluencerVettingAgent`

Full vetting pipeline: profile → engagement → collab network → score → verdict.

```python
result = await agent.run(
    username: str,
    goal: str = "brand partnership",
    max_posts: int = 50,
    max_age_days: int = 90,
    progress_cb = None,
)
```

**Returns `VettingResult`:**
```
username, found, profile, is_dead, last_post_days
feed_tags, er_pct, avg_likes, avg_comments, posts_analysed
usertags, mentions, sponsors, coauthors
score (0-100), verdict, goal, errors[], elapsed_s
```

**Verdict values:** `recommended` · `conditional` · `not_recommended` · `private` · `dead` · `not_found` · `error`

---

### `AccountHealthAgent`

Activity + engagement → health score + red flags + verdict.

```python
result = await agent.run(
    username: str,
    max_posts: int = 30,
    max_age_days: int = 180,
    dead_threshold_days: int = 365,
    progress_cb = None,
)
```

**Returns `HealthReport`:**
```
username, found, profile, status, last_post_days
er_pct, avg_likes, posts_analysed
health_score (0-100), red_flags[], green_flags[]
verdict, errors[], elapsed_s
```

**Verdict values:** `healthy` · `needs_attention` · `problematic`

---

### `CreatorDiscoveryAgent`

Tag-network traversal to find creators similar to a seed account.

```python
results = await agent.run(
    seed_username: str,
    min_followers: int = 1000,
    min_frequency: int = 2,
    max_posts: int = 50,
    max_age_days: int = 90,
    top_n: int = 20,
    progress_cb = None,
)
```

**Returns `List[DiscoveredCreator]`:**
```
username, profile, discovered_via, frequency, score, last_post_days
```

`discovered_via` values: `usertag` · `mention` · `coauthor` · `sponsor`

---

### `BulkScoringAgent`

Score and rank up to 20 accounts in parallel. Uses the same 0–100 scoring formula as `validate_prospect_list`.

```python
results = await agent.run(
    usernames: List[str],
    max_posts: int = 30,
    max_age_days: int = 90,
    progress_cb = None,
)
```

**Returns `List[ScoredAccount]` sorted by score descending:**
```
username, found, profile, is_dead, last_post_days
er_pct, score (0-100)
```

---

### `ContentAuditAgent`

Deep feed audit: content mix, posting cadence, hashtag strategy, best/worst performing content.

```python
result = await agent.run(
    username: str,
    max_posts: int = 100,
    max_age_days: int = 180,
    progress_cb = None,
)
```

**Returns `ContentAuditResult`:**
```
username, profile, posts_analysed
content_mix (image/video/carousel/reel %)
avg_posts_per_week, posting_consistency
top_posts[], worst_posts[]
top_hashtags[], avg_hashtags_per_post
best_day, best_hour (UTC)
er_pct, avg_likes, avg_comments
```

---

## Account Scoring Formula

Used by `InfluencerVettingAgent`, `BulkScoringAgent`, and the `validate_prospect_list` prompt.

**Total score: 0–100**

| Component | Max points | Formula |
|-----------|-----------|---------|
| Engagement Rate | 40 | ≥6%→40 · ≥3%→30 · ≥1%→15 · <1%→0 (linear interpolation within bands) |
| Followers | 30 | `log10(followers) / log10(10,000,000) × 30` (log scale, capped at 10M) |
| Activity | 20 | ≤7d→20 · ≤30d→15 · ≤90d→8 · ≤365d→3 · >365d→0 |
| Profile quality | 10 | Verified→+5 · Business/creator→+2 · Has highlights→+2 · Has reels→+1 |

**Example scores:**

| Account type | Followers | ER% | Last post | Score |
|-------------|-----------|-----|-----------|-------|
| Nano active | 5,000 | 8% | 3 days | ~68 |
| Micro active | 50,000 | 4% | 7 days | ~73 |
| Macro low-ER | 1,000,000 | 0.8% | 14 days | ~49 |
| Mega verified | 10,000,000 | 1.5% | 5 days | ~78 |
| Dead account | 200,000 | — | 500 days | ~15 |

---

## Data Models

### `InstagramProfile`

Core profile data returned by most tools.

```
username, user_id, full_name
followers, following, posts_count
bio, website, category, city, email, phone
is_verified, is_business, is_professional, is_private
highlight_count, has_reels
last_post_days, is_dead
```

### `InstagramPost`

A single post from the feed.

```
shortcode, post_url, media_type, post_type
likes, comments
caption, hashtags[], mentions[], usertags[], coauthors[], sponsor_tags[]
display_url, taken_at, taken_at_str
```

### `CommentItem`

A single comment from `instagram_post_comments`.

```
pk, text, comment_index
comment_like_count, child_comment_count
username, user_id, full_name, is_verified, is_private
has_translation, has_gif, gif_url
created_at, created_at_str
is_caption
```

### `ReelItem`

A single reel from `instagram_reels`.

```
shortcode, post_url, pk
play_count, like_count, comment_count
coauthor_ids[], thumbnail_url, width, height
taken_at, taken_at_str, is_pinned
```

### `RepostItem`

A single repost from `instagram_reposts`.

```
shortcode, post_url, media_type, post_type, product_type
orig_username, orig_user_id
likes, comments, view_count, carousel_count
caption, display_url, width, height
taken_at, taken_at_str
```

### `TaggedPost`

A post where this account was tagged, from `instagram_tagged_by`.

```
shortcode, post_url, media_type
poster_username, poster_id
likes, comments
caption, display_url, width, height
taken_at, taken_at_str
```

---

## Error Types

All errors are returned as MCP `ToolError` with a structured message containing the error type and a suggested action for the LLM.

| Error type | When it occurs | Suggested action |
|------------|---------------|-----------------|
| `not_found` | Username doesn't exist, was deleted, or renamed | Verify the username |
| `private_account` | Account is private — feed data unavailable | Only basic profile info is returned |
| `rate_limited` | All retries exhausted, still getting 429 | Wait 1–2 minutes; add proxy URLs |
| `auth_required` | Authenticated tool called without cookies | Export cookies from browser |
| `fetch_error` | Network timeout, proxy failure, non-200 response | Check connectivity; verify proxies |
| `proxy_error` | All configured proxies are down | Check proxy URLs; direct fallback is used |
| `validation_error` | Invalid input parameter | Check parameter format |
| `config_error` | Invalid server configuration | Check environment variables |
| `account_suspended` | Instagram flagged the account as unavailable | No action; Instagram-side issue |
| `unexpected_error` | Unclassified exception | Check server logs |

---

## Tool Decision Guide

### "I want to know who collaborates with this account"

```
Who appears in THEIR OWN posts?   → instagram_find_collab_network 🌐
Who tagged THEM in THEIR posts?   → instagram_tagged_by 🔐
What did THEY repost from others? → instagram_reposts 🔐
```

### "I want engagement metrics"

```
Quick ER% + content mix           → instagram_analyze_engagement 🌐
Per-post likes/comments           → instagram_feed_deep 🌐
Reel play counts specifically     → instagram_reels 🔐
```

### "I want to check multiple accounts"

```
2-5 accounts, comparison table    → instagram_compare_profiles 🌐
Up to 20 accounts, status check   → instagram_bulk_check 🌐
Up to 500 accounts, full data     → instagram_batch_scrape 🌐
```

### "I want data from a single post"

```
Full post metadata + GPS          → instagram_post 🌐
Comments + likes per comment      → instagram_post_comments 🌐
```

### "I want account activity status"

```
Is the account active or dead?    → instagram_profile (check_alive=True, include_feed=False) 🌐
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
# With uv (recommended)
uv sync

# With pip
pip install -e .
```

**Core dependencies:**
- `mcp[cli] >= 1.0.0` — MCP server framework
- `curl-cffi >= 0.7.0` — Chrome impersonation HTTP client
- `pydantic >= 2.0.0` — Input validation

### Step 3 — Verify the installation

```bash
uv run python -c "from instagram_mcp import create_mcp_server; print('OK')"
```

---

## Configuration

All settings are controlled by environment variables. The defaults are production-ready — no configuration is required to start using anonymous tools.

### Quickstart (anonymous mode — zero config)

```bash
uv run python -m instagram_mcp
```

All 10 anonymous tools are immediately available.

### With authentication (full 13-tool access)

```bash
INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt \
uv run python -m instagram_mcp
```

### With proxies (for bulk operations)

```bash
INSTAGRAM_MCP_PROXIES="http://user:pass@proxy1:8080,http://user:pass@proxy2:8080" \
uv run python -m instagram_mcp
```

---

## Authentication

Authentication is **optional**. 10 tools work without any login. The 3 authenticated tools (`instagram_tagged_by`, `instagram_reposts`, `instagram_reels`) require an exported browser cookie file.

### How it works

The server reads a cookie file, extracts the Instagram session cookies, and fetches CSRF tokens (`fb_dtsg` + `lsd`) from the Instagram homepage. These tokens are injected into authenticated GraphQL POST requests. **Your login credentials are never required and never stored.**

### Exporting cookies

**Method 1 — Netscape format (`cookies.txt`)**

1. Install the [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) Chrome/Firefox extension
2. Log in to Instagram in your browser
3. Click the extension icon → **Export** → save as `cookies.txt`

**Method 2 — JSON format (`cookies.json`)**

1. Install the [EditThisCookie](https://www.editthiscookie.com/) extension
2. Log in to Instagram
3. Click the extension → **Export** → save the exported JSON as `cookies.json`

Both formats are auto-detected by the server.

### Where to place the cookie file

The server searches in this order (first match wins):

```
1. INSTAGRAM_MCP_COOKIES env var     explicit path, highest priority
2. ./cookies.json                    JSON format, working directory
3. ./cookies.txt                     Netscape format, working directory
4. ../cookies.json                   parent directory
5. ../cookies.txt                    parent directory
```

Recommended placement:

```
instagram_mcp/
├── cookies.txt     ← place it here
├── tools.py
└── ...
```

### Session expiry

Instagram sessions last approximately 90 days. When the session expires:

1. Log in to Instagram in your browser again
2. Re-export `cookies.txt`
3. Replace the old file — the server picks it up on the next authenticated request. **No restart required.**

---

## Proxy Setup

Without proxies, all requests originate from one IP address. Instagram's rate limiting (HTTP 429) will trigger under sustained load. Proxies distribute traffic across multiple IPs.

### proxies.txt

Create `proxies.txt` in the project root or `instagram_mcp/` directory:

```
# proxies.txt — one URL per line, # for comments
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
socks5://user:pass@proxy3.example.com:1080
```

### Via environment variable

```bash
INSTAGRAM_MCP_PROXIES="http://u:p@h1:8080,http://u:p@h2:8080"
```

### Rotation and health logic

| Event | Behaviour |
|-------|-----------|
| Normal request | Proxy selected by round-robin |
| 429 received | Current proxy's failure count incremented |
| 5 consecutive 429s on one proxy | Proxy enters 30-second cooldown |
| All proxies on cooldown | Falls back to direct connection (`proxy_auto_fallback=true`) |
| Health check (every 30s) | Recovered proxies are automatically restored |

### Recommended proxy count

| Workload | Recommended proxies |
|----------|-------------------|
| Occasional single lookups | 0 (direct) |
| Bulk check (20 accounts) | 2–3 |
| Batch scrape (100+ accounts) | 5–10 |
| Large batch (500 accounts) | 10–20 |

---

## Connecting to Claude Desktop

Add to `~/.config/claude/claude_desktop_config.json` (macOS/Linux) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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
        "INSTAGRAM_MCP_COOKIES": "/absolute/path/to/cookies.txt",
        "INSTAGRAM_MCP_PROXIES": "http://user:pass@proxy:8080"
      }
    }
  }
}
```

Remove the `"env"` block entirely for anonymous-only mode.

### Claude Code (CLI)

```bash
claude mcp add instagram -- uv --directory /path/to/instagram_mcp run python -m instagram_mcp
```

### HTTP transport

```bash
INSTAGRAM_MCP_TRANSPORT=http \
INSTAGRAM_MCP_HOST=0.0.0.0 \
INSTAGRAM_MCP_PORT=8000 \
uv run python -m instagram_mcp
```

The server exposes a standard MCP HTTP endpoint at `http://host:port/mcp`.

---

## Environment Variables

### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_COOKIES` | `""` | Path to cookies file (`.txt` Netscape or `.json` array) |
| `INSTAGRAM_MCP_TRANSPORT` | `stdio` | Transport mode: `stdio` or `http` |
| `INSTAGRAM_MCP_HOST` | `0.0.0.0` | Bind host for HTTP mode |
| `INSTAGRAM_MCP_PORT` | `8000` | Port for HTTP mode |

### Proxy settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_PROXIES` | `""` | Comma-separated proxy URLs (overrides `proxies.txt`) |
| `INSTAGRAM_MCP_PROXY_MAX_FAILS` | `5` | Consecutive failures before proxy cooldown |
| `INSTAGRAM_MCP_PROXY_COOLDOWN` | `30` | Proxy cooldown duration in seconds |
| `INSTAGRAM_MCP_PROXY_MAX_COOLDOWN` | `300.0` | Maximum proxy cooldown in seconds |

### Rate limiting settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_RATE_LIMIT_RPS` | `100.0` | Maximum requests per second |
| `INSTAGRAM_MCP_RATE_LIMIT_BURST` | `50` | Token bucket burst size |
| `INSTAGRAM_MCP_RATE_BACKOFF_FACTOR` | `0.7` | Rate multiplier on receiving a 429 |
| `INSTAGRAM_MCP_RATE_RECOVERY_FACTOR` | `1.15` | Rate multiplier on successful requests |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive 429s before circuit opens |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN` | `60.0` | Sleep time when circuit is open (seconds) |
| `INSTAGRAM_MCP_REQUEST_JITTER` | `0.1` | Max random jitter added to sleep (seconds) |

### Cache settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_CACHE_DISABLED` | `""` | Set to `1` or `true` to disable caching |
| `INSTAGRAM_MCP_CACHE_TTL` | `300` | Global cache TTL override in seconds |
| `INSTAGRAM_MCP_CACHE_MAX` | `500` | Maximum number of cache entries |

### Network settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_TIMEOUT` | `10` | Request timeout in seconds |
| `INSTAGRAM_MCP_MAX_RETRIES` | `3` | Retry attempts per request |
| `INSTAGRAM_MCP_MAX_WORKERS` | `12` | Default concurrency for batch operations |
| `INSTAGRAM_MCP_APP_ID` | `936619743392459` | Instagram `X-IG-App-ID` header value |
| `INSTAGRAM_MCP_IMPERSONATE` | `chrome142` | `curl_cffi` browser impersonation target |

### GraphQL settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTAGRAM_MCP_GRAPHQL_DOC_ID` | `26442143102071041` | Anonymous feed pagination `doc_id` |
| `INSTAGRAM_MCP_MAX_PAGINATION` | `200` | Hard ceiling on paginated post count |

---

## Architecture

### Component map

```
instagram_mcp/
├── __init__.py         Server factory · lifespan · resources · prompts
├── tools.py            13 MCP tool registrations
├── client.py           HTTP layer · all Instagram API calls · retry logic
├── parser.py           Raw API JSON → typed Python dataclasses
├── formatter.py        Dataclasses → LLM-readable Markdown tables
├── models.py           Pydantic input validation + internal dataclasses
├── config.py           All settings with environment variable overrides
├── cache.py            Async TTL cache with LRU eviction + background cleanup
├── rate_limiter.py     Adaptive token-bucket + circuit breaker
├── proxy_manager.py    Round-robin rotation · health checks · cooldown
├── cookie_manager.py   Netscape + JSON cookie loading · CSRF token fetching
├── exceptions.py       Typed exception hierarchy (10 types)
├── agents.py           5 high-level pipeline agents (Python-direct use)
└── batch_runner.py     Parallel batch scraping engine with worker pool
```

### Request lifecycle

```
1. MCP client sends ToolUse request
       │
2. Pydantic input validation (models.py)
       │
3. Rate limiter: acquire token from bucket (rate_limiter.py)
       │
4. Cache lookup (cache.py)
       ├── HIT  → parse cached JSON → format → return immediately
       └── MISS ↓
5. Select proxy (proxy_manager.py) — round-robin
       │
6. HTTP request (client.py) — curl_cffi Chrome impersonation
       │
7. Retry logic: on 429 or network error, try next proxy (up to max_retries)
       │
8. Successful response: store in cache with per-type TTL
       │
9. Parse raw JSON → dataclass (parser.py)
       │
10. Format dataclass → Markdown (formatter.py)
       │
11. Return MCP ToolResult
```

### API endpoints

| Endpoint | Auth | Used by |
|----------|------|---------|
| `GET i.instagram.com/api/v1/users/web_profile_info/?username={}` | None | All profile-based tools |
| `POST www.instagram.com/graphql/query/` | Session cookies + CSRF | `tagged_by`, `reposts`, `reels` |
| `GET www.instagram.com/api/v1/media/{id}/comments/` | None | `post_comments` |
| `GET www.instagram.com/p/{shortcode}/` | None | `post` |

### Cache TTL reference

| Data type | Default TTL | Rationale |
|-----------|------------|-----------|
| Comments | 60 seconds | Comments are added frequently |
| Feed tags | 120 seconds | New posts appear regularly |
| Paginated feed | 180 seconds | Post feed changes over time |
| Profile | 300 seconds | Follower count / bio changes slowly |
| Tagged / reposts / reels | 300 seconds | Tab content is relatively stable |
| Account status | 600 seconds | Active/dead status changes rarely |

### GraphQL doc_ids

| Tool | `fb_api_req_friendly_name` | `doc_id` |
|------|---------------------------|----------|
| Feed (anonymous) | `PolarisProfilePostsTabContentQuery_connection` | `26442143102071041` |
| Tagged Tab | `PolarisProfileTaggedTabContentQuery_connection` | `26707104818956021` |
| Reposts Tab | `PolarisProfileRepostsTabContentRefetchQuery` | `35095888563388407` |
| Reels Tab | `PolarisProfileReelsTabContentQuery_connection` | `26292852833730510` |

### Shortcode ↔ media_id conversion

Instagram shortcodes are base-64 encoded numeric media IDs using the alphabet `A–Z a–z 0–9 - _`. The conversion is pure arithmetic — no API call needed.

```python
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

def shortcode_to_media_id(shortcode: str) -> str:
    n = 0
    for c in shortcode:
        n = n * 64 + ALPHABET.index(c)
    return str(n)

# Example: 'DNnx22NOGnt' → '3704148491870169581'
```

---

## Limitations

### Public data only

This server only accesses publicly visible data. Private account feeds, follower lists, DMs, and story content are not accessible.

### play_count requires authentication

`play_count` on reels is only available via `instagram_reels` (🔐). The anonymous feed API always returns `view_count=null` for reels — this is an Instagram API limitation.

### Post HTML parser

`instagram_post` parses Instagram's embedded JSON from the post HTML page. If Instagram changes the page structure, some fields may stop being parsed. The server handles this gracefully — fields missing from the page default to zero/empty rather than raising an error.

### Rate limiting

Without proxies, sustained batch operations will trigger 429 responses. The adaptive rate limiter backs off automatically, but for large-scale work (100+ accounts), proxies are strongly recommended.

### Follower count in some contexts

The Explore Grid endpoint (used internally) does not include `follower_count` in the user object. This is an Instagram API limitation for that specific endpoint.

### Comment pagination direction

The comments API paginates in one direction from the starting cursor. `sort_order=popular` and `sort_order=recent` affect the ordering but not the direction of pagination.

### Session cookie expiry

Authenticated tool sessions expire after approximately 90 days. After expiry, re-export cookies from your browser.

---

## FAQ

**Do I need an Instagram account or password?**

No. The 10 anonymous tools require nothing. The 3 authenticated tools only need exported browser cookies — the server never receives your login credentials.

**Why is `play_count` zero or missing in `instagram_feed_deep`?**

Instagram's main feed API returns `view_count=null` for all reels. This is intentional on Instagram's side. Only the Reels Tab endpoint exposes real play counts. Use `instagram_reels` (🔐).

**I'm getting HTTP 429 errors. What should I do?**

1. Add proxies via `proxies.txt` or `INSTAGRAM_MCP_PROXIES`
2. Reduce `max_workers` for batch operations
3. Wait 1–2 minutes — the circuit breaker will recover automatically

**Where are batch scrape results saved?**

Provide a path in `output_file`. If left empty, results go to a temp file in `/tmp/` and the path is returned in the tool response.

**Does `instagram_post_comments` need authentication?**

No. It is fully anonymous and works on any public post.

**Can I run multiple instances of the server?**

Yes. Each instance maintains its own in-memory cache. There is no shared state between instances.

**Can I use HTTP transport instead of STDIO?**

Yes. Set `INSTAGRAM_MCP_TRANSPORT=http`. The server binds to `INSTAGRAM_MCP_HOST:INSTAGRAM_MCP_PORT` (default `0.0.0.0:8000`).

**How do I clear the cache for one user without restarting?**

Call `instagram_server` with `action="clear_user"` and `username="target_username"`. This evicts all cache entries (profile, feed, tagged, reposts, reels, comments) for that account.

**The server says "9/13 tools available" — why not 10/13?**

The log message counts authenticated tools (3) as unavailable in anonymous mode. It should say `10/13`. If it says `9/12`, you are running an older version — update to v1.0.0.

**What is `curl_cffi` and why is it used?**

`curl_cffi` is a Python binding for `libcurl` with TLS fingerprint impersonation. It sends requests with the exact TLS ClientHello and HTTP headers that Chrome 142 would send, bypassing Instagram's bot-detection heuristics that would block a standard `requests`/`httpx` client.

---

## License

MIT
