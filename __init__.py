"""
instagram_mcp — World-class Instagram data MCP server.

Architecture:
  - 12 MCP Tools: profile, tags, feed, bulk, engagement, collab, compare, batch
  - MCP Resources: live cache exposure for profile + feed data
  - MCP Prompts: ready-made LLM analysis templates
  - Smart proxy management (auto-rotation, health check, fallback)
  - TTL cache (LRU eviction) with background cleanup
  - Adaptive rate limiter (token-bucket + circuit breaker)
  - Session pooling (thread-safe, curl_cffi)
  - Full pagination (up to 200 posts via GraphQL cursor)
  - Context-aware tools: MCP-native progress reporting + logging

Transports supported:
  - STDIO (default, for Claude Desktop / Claude Code)
  - Streamable HTTP (set INSTAGRAM_MCP_TRANSPORT=http)

Usage:
    from instagram_mcp import create_mcp_server
    mcp = create_mcp_server()
    mcp.run()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict

__version__ = "1.0.0"

logger = logging.getLogger("instagram_mcp")


def create_mcp_server():
    """
    MCP server factory — instantiates all components, registers tools,
    resources, and prompts.

    Returns:
        FastMCP: Ready-to-run MCP server instance
    """
    from mcp.server.fastmcp import FastMCP

    from .cache import SmartCache
    from .client import InstagramClient
    from .config import MCPConfig
    from .cookie_manager import CookieManager
    from .proxy_manager import ProxyManager
    from .rate_limiter import AdaptiveRateLimiter
    from .tools import register_tools

    # ── 1. Configuration ──────────────────────────────────────────────────────
    config = MCPConfig.from_env()

    # ── 2. Components ─────────────────────────────────────────────────────────
    cookie_manager = CookieManager(cookies_path=config.cookies_path or None)
    cookie_manager.load()
    if cookie_manager.is_authenticated:
        logger.info("instagram_mcp: authenticated session loaded from cookies.txt")
    else:
        logger.info("instagram_mcp: no cookies.txt — running in anonymous mode (10/13 tools available)")

    cache = SmartCache(
        max_entries=config.cache_max_entries,
        enabled=config.cache_enabled,
    )
    rate_limiter = AdaptiveRateLimiter(
        rate=config.rate_limit_rps,
        burst=config.rate_limit_burst,
        min_rate=config.rate_limit_min_rps,
        backoff_factor=config.rate_backoff_factor,
        recovery_factor=config.rate_recovery_factor,
        circuit_breaker_threshold=config.circuit_breaker_threshold,
        circuit_breaker_cooldown=config.circuit_breaker_cooldown,
        request_jitter=config.request_jitter,
    )
    proxy_manager = ProxyManager(
        proxy_urls=config.proxy_urls,
        max_fails=config.proxy_max_fails,
        cooldown_seconds=config.proxy_cooldown,
        max_cooldown_seconds=config.proxy_max_cooldown,
        auto_fallback=config.proxy_auto_fallback,
        health_check_interval=config.proxy_health_interval,
    )

    # ── 3. Central client ─────────────────────────────────────────────────────
    client = InstagramClient(
        config=config,
        proxy_manager=proxy_manager,
        rate_limiter=rate_limiter,
        cache=cache,
        cookie_manager=cookie_manager,
    )

    # ── 4. Lifespan — all background tasks start inside the running event loop ─
    @contextlib.asynccontextmanager
    async def _lifespan(server):
        async def _cache_cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(60)
                    removed = await cache.cleanup_expired()
                    if removed:
                        logger.debug("Cache cleanup: %d expired entries removed", removed)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("Cache cleanup error: %s", exc)

        cleanup_task = asyncio.ensure_future(_cache_cleanup_loop())
        proxy_manager.start_health_checks()
        logger.info(
            "instagram_mcp v%s started | cache=%s | proxies=%d | transport=%s",
            __version__,
            "enabled" if config.cache_enabled else "disabled",
            len(config.proxy_urls),
            "http" if _is_http_transport() else "stdio",
        )
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=3.0)
            await proxy_manager.stop_health_checks()
            await client.close()
            logger.info("instagram_mcp v%s shutdown complete", __version__)

    # ── 5. MCP server ─────────────────────────────────────────────────────────
    import os as _os
    _http = _os.environ.get("INSTAGRAM_MCP_TRANSPORT", "").lower() == "http"
    _host = _os.environ.get("INSTAGRAM_MCP_HOST", "0.0.0.0")
    _port = int(_os.environ.get("INSTAGRAM_MCP_PORT", "8000"))

    _auth_status = "authenticated" if cookie_manager.is_authenticated else "anonymous (no cookies.txt)"
    mcp = FastMCP(
        "instagram_mcp",
        lifespan=_lifespan,
        host=_host if _http else "127.0.0.1",
        port=_port if _http else 8000,
        log_level="INFO",
        instructions=(
            f"Instagram data server — {_auth_status}.\n\n"
            "AUTH TIERS:\n"
            "• 🌐 NO LOGIN REQUIRED — 10 anonymous tools, no credentials needed.\n"
            "• 🔐 AUTH REQUIRED — 3 tools require cookies.json/cookies.txt with a valid "
            "Instagram session. Each tool's docstring starts with its tier marker.\n\n"
            "TOOLS (13 total):\n"
            "• 🌐 instagram_profile — profile metadata + optional feed tags (up to 12 posts) "
            "+ activity status. One API call covers profile, tags, mentions, dead-account check. "
            "Set include_feed=False for fastest profile-only lookup.\n"
            "• 🌐 instagram_feed_deep — paginated feed analysis up to 200 posts. "
            "Builds on instagram_profile but fetches many more posts for trend analysis.\n"
            "• 🌐 instagram_analyze_engagement — engagement rate %, content mix by type, "
            "best posting days, top posts, top hashtags. Uses pagination.\n"
            "• 🌐 instagram_find_collab_network — maps usertags, @mentions, co-authors, "
            "and paid sponsors across recent posts. Use min_frequency to filter regulars.\n"
            "• 🌐 instagram_compare_profiles — side-by-side table for 2-5 accounts in parallel.\n"
            "• 🌐 instagram_bulk_check — fetch up to 20 accounts in parallel with status for each.\n"
            "• 🌐 instagram_batch_scrape — large-scale scraping up to 500 profiles with date filtering.\n"
            "• 🌐 instagram_server — server diagnostics (action='status') or cache management "
            "(action='clear_cache' / action='clear_user' with username=).\n"
            "• 🌐 instagram_post — full details for ONE post by shortcode or URL: "
            "location (name + GPS + Google Maps link), exact timestamp, caption, hashtags, "
            "usertags, music. Input: shortcode like 'DXjuqH9nDVE' or full post URL.\n"
            "• 🔐 instagram_tagged_by — posts BY OTHERS that tag this account (passive — "
            "they mentioned us). Tagged Tab endpoint.\n"
            "• 🔐 instagram_reposts — content this account ACTIVELY REPOSTED from others "
            "(endorsements — we chose to amplify them). Reposts Tab endpoint.\n"
            "• 🔐 instagram_reels — account's OWN reels with PLAY COUNTS. "
            "play_count is NOT available via instagram_feed_deep — only this tool exposes it. "
            "Use for reel performance analysis and virality ranking.\n"
            "• 🌐 instagram_post_comments — comments on a single post with per-comment like counts, "
            "author info, threading depth, GIF detection, and language flags. "
            "Input: shortcode or URL. sort_order='popular' for most-liked first, 'recent' for chronological. "
            "instagram_post returns comment COUNT only — use this tool for actual comment content.\n\n"
            "CONTENT-FROM-OTHERS DECISION GUIDE:\n"
            "  Who appears in account's OWN posts?    → instagram_find_collab_network 🌐\n"
            "  Who tagged the account in THEIR posts? → instagram_tagged_by 🔐\n"
            "  What did the account REPOST from others? → instagram_reposts 🔐\n\n"
            "Results are cached — repeated lookups are instant."
        ),
    )

    # ── 6. Tools ──────────────────────────────────────────────────────────────
    register_tools(mcp, client, config)

    # ── 7. Resources ──────────────────────────────────────────────────────────
    _register_resources(mcp, client, config)

    # ── 8. Prompts ────────────────────────────────────────────────────────────
    _register_prompts(mcp)

    return mcp


# ═════════════════════════════════════════════════════════════════════════════
# RESOURCES
# MCP Resources expose data that AI can READ directly without calling a tool.
# Perfect for cached profile data — no extra API call needed.
# ═════════════════════════════════════════════════════════════════════════════

def _register_resources(mcp, client, config) -> None:
    """Register MCP Resources."""

    from .parser import parse_profile
    from .formatter import format_profile_json, format_feed_tags_json, format_posts_json
    from .parser import parse_feed_tags

    @mcp.resource(
        "instagram://profile/{username}",
        name="Instagram Profile Cache",
        description="Cached Instagram profile data for a username. Returns JSON. Fast — no API call if cached.",
        mime_type="application/json",
    )
    async def profile_resource(username: str) -> str:
        """Read cached profile data. If not cached, fetches from API."""
        clean = username.strip().lstrip("@").lower()
        if not clean:
            return json.dumps({"error": "invalid username"})

        # Try cache first
        cached = await client.cache.get(f"user:{clean}")
        if cached is not None:
            try:
                profile = parse_profile(cached, clean, config)
                return json.dumps({
                    "cached": True,
                    "username": clean,
                    "profile": format_profile_json(profile),
                }, ensure_ascii=False, indent=2)
            except Exception:
                pass

        # Fetch from API
        try:
            user = await client.fetch_user(clean, config.cache_profile_ttl)
            if user is None:
                return json.dumps({"cached": False, "found": False, "username": clean})
            profile = parse_profile(user, clean, config)
            return json.dumps({
                "cached": False,
                "found": True,
                "username": clean,
                "profile": format_profile_json(profile),
            }, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc), "username": clean})

    @mcp.resource(
        "instagram://feed/{username}",
        name="Instagram Feed Cache",
        description="Cached recent feed data (tags, posts) for a username. Returns JSON.",
        mime_type="application/json",
    )
    async def feed_resource(username: str) -> str:
        """Read cached feed tag data. Fetches first-page feed if not cached."""
        clean = username.strip().lstrip("@").lower()
        if not clean:
            return json.dumps({"error": "invalid username"})

        try:
            user = await client.fetch_user(clean, config.cache_tags_ttl)
            if user is None:
                return json.dumps({"found": False, "username": clean})
            profile = parse_profile(user, clean, config)
            if profile.is_private:
                return json.dumps({"found": True, "username": clean, "is_private": True, "tags": []})
            ft = parse_feed_tags(user, 12, 30)
            return json.dumps({
                "found": True,
                "username": clean,
                "is_private": False,
                **format_feed_tags_json(ft),
                "posts": format_posts_json(ft.posts),
            }, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc), "username": clean})

    @mcp.resource(
        "instagram://server/status",
        name="Instagram MCP Server Status",
        description="Live server status: cache hit rate, proxy health, rate limiter stats.",
        mime_type="application/json",
    )
    async def server_status_resource() -> str:
        """Live server diagnostics as JSON."""
        try:
            from .formatter import format_diagnostics_json
            cache_stats = await client.cache.stats()
            proxy_statuses = await client.proxy_manager.get_all_status()
            proxy_summary = client.proxy_manager.stats
            rate_stats = client.rate_limiter.stats
            return format_diagnostics_json(cache_stats, proxy_statuses, proxy_summary, rate_stats)
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ═════════════════════════════════════════════════════════════════════════════
# PROMPTS
# MCP Prompts are reusable LLM instruction templates.
# Users select them from the client; variables are filled at call time.
# ═════════════════════════════════════════════════════════════════════════════

def _register_prompts(mcp) -> None:
    """Register MCP Prompts — 6 workflow agents."""

    # ── 1. analyze_influencer ─────────────────────────────────────────────────

    @mcp.prompt(
        name="analyze_influencer",
        description=(
            "Full influencer vetting pipeline: profile, engagement rate, collab network, "
            "scored verdict. Use for brand partnership or sponsorship evaluation."
        ),
    )
    def analyze_influencer(username: str, niche: str = "", goal: str = "brand partnership") -> list:
        niche_str = f" in the **{niche}** niche" if niche else ""
        text = (
            f"Vet Instagram account **@{username}**{niche_str} for: **{goal}**.\n\n"
            "## Execution plan\n\n"
            f"**Step 1 — Profile snapshot**\n"
            f"Call `instagram_profile` with username={username!r}, include_feed=True, "
            f"max_feed_posts=12, max_age_days=30, check_alive=True.\n"
            "→ If result shows private or not_found: report that and stop.\n"
            "→ If is_dead=True: note it and continue (score will reflect inactivity).\n\n"
            f"**Step 2 — Engagement analysis**\n"
            f"Call `instagram_analyze_engagement` with username={username!r}, "
            f"max_posts=50, max_age_days=90.\n"
            "→ Note the ER% and benchmark: Excellent ≥6%, Good 3-6%, Average 1-3%, Low <1%.\n\n"
            f"**Step 3 — Collaboration network**\n"
            f"Call `instagram_find_collab_network` with username={username!r}, "
            f"max_posts=50, max_age_days=90, min_frequency=2.\n"
            "→ Focus on: sponsor_tags (paid), recurring usertags (organic brands), "
            "co-authors (collab posts).\n\n"
            "## Report structure\n\n"
            "### Profile Overview\n"
            "Followers, following ratio, account type, verification, category, "
            "website, city, last post age.\n\n"
            "### Engagement Quality\n"
            "ER% with benchmark label. Avg likes/comments. Content mix "
            "(% reels / carousels / images). Best posting days.\n\n"
            "### Collaboration Network\n"
            "Top brands/people tagged (with frequency). Confirmed paid sponsors. "
            "Co-authored posts. @mention patterns.\n\n"
            "### Audience Signals\n"
            "Follower/following ratio assessment. Engagement authenticity "
            "(ER vs follower count). Activity consistency.\n\n"
            f"### Verdict for \"{goal}\"\n"
            "**Recommended / Conditional / Not Recommended.** "
            "Top 3 reasons. Suggested next action."
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]

    # ── 2. find_brand_collaborations ──────────────────────────────────────────

    @mcp.prompt(
        name="find_brand_collaborations",
        description=(
            "Discover all brand deals, paid sponsors, and recurring brand mentions "
            "from an account's recent posts. Categorises paid vs organic."
        ),
    )
    def find_brand_collaborations(username: str, max_posts: int = 100) -> list:
        text = (
            f"Map all brand relationships for **@{username}**.\n\n"
            "## Execution plan\n\n"
            f"**Step 1 — Collaboration network (wide scan)**\n"
            f"Call `instagram_find_collab_network` with username={username!r}, "
            f"max_posts={max_posts}, max_age_days=180, min_frequency=1.\n"
            "→ Captures usertags, mentions, coauthors, sponsor_tags across all posts.\n\n"
            f"**Step 2 — Deep feed with post details**\n"
            f"Call `instagram_feed_deep` with username={username!r}, "
            f"max_posts={max_posts}, max_age_days=180, include_posts_detail=True.\n"
            "→ Gives full captions for keyword-based brand detection.\n\n"
            "## Analysis\n\n"
            "From the combined results, extract and categorise:\n\n"
            "### 1. Paid Partnerships (confirmed)\n"
            "Accounts in sponsor_tags — these are official Instagram paid partnership "
            "disclosures. List with frequency and first appearance date.\n\n"
            "### 2. Recurring Brand Mentions (≥2 times)\n"
            "Brands @-mentioned in captions 2+ times. Note: organic vs likely paid "
            "(look for #ad, #sponsored, #gifted keywords in captions).\n\n"
            "### 3. Photo Usertags of Brands\n"
            "Brands tagged directly in post images/videos. Ranked by frequency.\n\n"
            "### 4. Co-authored Posts\n"
            "Official Instagram Collab posts (coauthors list). List each brand "
            "and number of collab posts.\n\n"
            "### Summary Table\n"
            "| Brand | Type | Frequency | First seen | Paid? |\n"
            "|-------|------|-----------|------------|-------|\n"
            "(fill from data above)\n\n"
            "Note any brands that appear across multiple categories "
            "(strong ongoing relationship)."
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]

    # ── 3. competitive_analysis ───────────────────────────────────────────────

    @mcp.prompt(
        name="competitive_analysis",
        description=(
            "Compare 2-5 Instagram accounts for competitive intelligence. "
            "Rankings, differentiators, engagement comparison, strategic takeaways."
        ),
    )
    def competitive_analysis(usernames: str, metric_focus: str = "engagement") -> list:
        names = [u.strip().lstrip("@") for u in usernames.split(",") if u.strip()]
        names_str = ", ".join(f"@{n}" for n in names)
        usernames_list = str(names)
        text = (
            f"Competitive intelligence for: **{names_str}**\n"
            f"Focus: **{metric_focus}**\n\n"
            "## Execution plan\n\n"
            f"**Step 1 — Side-by-side overview**\n"
            f"Call `instagram_compare_profiles` with usernames={usernames_list}.\n"
            "→ Gets followers, status, account type, category for all accounts in parallel.\n\n"
            "**Step 2 — Engagement deep-dive (top 3 by followers)**\n"
            "From Step 1 results, identify the top 3 accounts by follower count.\n"
            "For each: call `instagram_analyze_engagement` (max_posts=50, max_age_days=90).\n"
            "→ Gets ER%, content mix, best days, top posts.\n\n"
            "**Step 3 — Collab network for the leader**\n"
            "For the #1 account by followers:\n"
            "Call `instagram_find_collab_network` (max_posts=50, min_frequency=2).\n"
            "→ Reveals brand partnerships and collaboration strategy of the market leader.\n\n"
            "## Report\n\n"
            "### Rankings\n"
            "| Rank | Account | Followers | ER% | Status | Why ranked here |\n"
            "|------|---------|-----------|-----|--------|-----------------|\n\n"
            "### Key Differentiators\n"
            "For each account: what makes them unique? "
            "Content style, audience size, engagement quality, posting frequency.\n\n"
            f"### {metric_focus.capitalize()} Breakdown\n"
            "Detailed comparison table focused on the requested metric.\n\n"
            "### Leader's Brand Strategy\n"
            "Who does the #1 account collaborate with? "
            "What can competitors learn from their collab network?\n\n"
            "### Strategic Takeaways\n"
            "Top 3 actionable insights from this competitive landscape."
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]

    # ── 4. account_audit ─────────────────────────────────────────────────────

    @mcp.prompt(
        name="account_audit",
        description=(
            "Full account health audit: activity status, growth signals, content "
            "consistency, red flags, overall verdict."
        ),
    )
    def account_audit(username: str, dead_threshold_days: int = 365) -> list:
        text = (
            f"Complete health audit of **@{username}**.\n\n"
            "## Execution plan\n\n"
            f"**Step 1 — Activity status**\n"
            f"Call `instagram_profile` with username={username!r}, "
            f"include_feed=False, check_alive=True, dead_threshold_days={dead_threshold_days}.\n"
            "→ Fast check: active / dead / private / not_found + last_post_days.\n\n"
            f"**Step 2 — Full profile + recent tags**\n"
            f"Call `instagram_profile` with username={username!r}, "
            f"include_feed=True, max_feed_posts=12, max_age_days=365, check_alive=False.\n"
            "→ Bio, category, website, recent tags, pinned post detection.\n\n"
            f"**Step 3 — Engagement analysis**\n"
            f"Call `instagram_analyze_engagement` with username={username!r}, "
            f"max_posts=50, max_age_days=180.\n"
            "→ ER%, content mix, posting consistency, top posts.\n\n"
            "## Audit Report\n\n"
            "### Account Health\n"
            f"Status (active/dead/private), last post age, "
            f"dead threshold used: {dead_threshold_days} days.\n\n"
            "### Growth Signals\n"
            "Follower count tier (nano/micro/mid/macro/mega). "
            "Following/follower ratio (healthy = ratio <1). "
            "Posts count and account age indicators.\n\n"
            "### Content Consistency\n"
            "Posting frequency from ER analysis. "
            "Content mix (% reels vs carousels vs images). "
            "Engagement trend (stable/growing/declining based on top vs avg posts).\n\n"
            "### Red Flags\n"
            "Check and report if present:\n"
            "- following >> followers (potential bot/spam)\n"
            "- ER < 1% despite large following\n"
            "- Gaps >60 days in posting\n"
            "- Zero website / bio\n"
            "- Very new account with high followers (suspicious growth)\n\n"
            "### Overall Verdict\n"
            "**Healthy / Needs Attention / Problematic.** "
            "Three key reasons. One recommended action."
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]

    # ── 5. discover_creators ─────────────────────────────────────────────────

    @mcp.prompt(
        name="discover_creators",
        description=(
            "Find similar creators by traversing the tag network of a seed account. "
            "Returns ranked list of active public creators discovered via usertags, "
            "mentions, and co-authored posts."
        ),
    )
    def discover_creators(
        seed_username: str,
        min_followers: int = 1000,
        min_frequency: int = 2,
        max_posts: int = 50,
    ) -> list:
        text = (
            f"Discover creators similar to **@{seed_username}** via their tag network.\n\n"
            "## Execution plan\n\n"
            f"**Step 1 — Seed account collab network**\n"
            f"Call `instagram_find_collab_network` with username={seed_username!r}, "
            f"max_posts={max_posts}, max_age_days=90, min_frequency={min_frequency}.\n"
            "→ Extracts every person @{seed_username} tags, mentions, or co-publishes with.\n\n"
            "**Step 2 — Profile check on discovered accounts**\n"
            "From the collab network results, collect all unique usernames "
            f"(usertags + mentions + coauthors). Filter to those appearing ≥{min_frequency} times.\n"
            "For each unique username: call `instagram_profile` with "
            "include_feed=False, check_alive=True.\n"
            f"→ Keep only: public + active + followers ≥ {min_followers:,}.\n\n"
            "**Step 3 — Engagement check for top 5**\n"
            "Sort remaining creators by follower count. For the top 5:\n"
            "Call `instagram_analyze_engagement` (max_posts=30, max_age_days=90).\n"
            "→ Adds ER% for more accurate ranking.\n\n"
            "## Output\n\n"
            "### Discovered Creators\n"
            "Ranked table:\n"
            "| Rank | Username | How found | Frequency | Followers | ER% | Active |\n"
            "|------|----------|-----------|-----------|-----------|-----|--------|\n"
            "(fill from data)\n\n"
            "How found = usertag / caption mention / co-author / paid sponsor.\n\n"
            "### Top Picks\n"
            "Top 3 creators with highest combined score "
            "(frequency × followers × engagement). Brief profile note for each.\n\n"
            "### Network Insights\n"
            "What type of creators does @{seed_username} engage with most? "
            "Any recurring brand accounts vs personal creators? "
            "Any unexpected discoveries?"
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]

    # ── 6. validate_prospect_list ─────────────────────────────────────────────

    @mcp.prompt(
        name="validate_prospect_list",
        description=(
            "Score and rank a list of Instagram accounts for outreach qualification. "
            "Filters out dead/private/not_found, scores remaining by "
            "followers + engagement + activity, returns a ranked shortlist."
        ),
    )
    def validate_prospect_list(
        usernames: str,
        min_followers: int = 1000,
        goal: str = "influencer outreach",
    ) -> list:
        names = [u.strip().lstrip("@") for u in usernames.split(",") if u.strip()]
        names_list = str(names)
        text = (
            f"Validate and rank prospects for: **{goal}**\n"
            f"Accounts to check: {', '.join(f'@{n}' for n in names)}\n\n"
            "## Execution plan\n\n"
            f"**Step 1 — Bulk status check**\n"
            f"Call `instagram_bulk_check` with usernames={names_list}, concurrency=5.\n"
            "→ Quick parallel check: found/not_found, followers, dead/private flags.\n\n"
            "**Step 2 — Filter disqualified accounts**\n"
            "Remove from the list:\n"
            f"- not_found accounts\n"
            "- private accounts (can't verify content quality)\n"
            "- dead accounts (no recent posts)\n"
            f"- followers < {min_followers:,}\n\n"
            "**Step 3 — Engagement for remaining prospects**\n"
            "For each account that passed Step 2 filters:\n"
            "Call `instagram_analyze_engagement` (max_posts=30, max_age_days=90).\n"
            "→ Gets ER% for accurate scoring.\n\n"
            "**Step 4 — Score each account**\n"
            "Score formula (0-100):\n"
            "- Engagement Rate (0-40): ≥6%→40, ≥3%→30, ≥1%→15, <1%→0\n"
            "- Followers (0-30): log scale, 10M→30, 1M→21, 100K→14, 10K→7\n"
            "- Activity (0-20): ≤7d→20, ≤30d→15, ≤90d→8, ≤365d→3\n"
            "- Quality (0-10): verified→5, business→2, highlights→2, reels→1\n\n"
            "## Output\n\n"
            "### Qualified Prospects (ranked)\n"
            "| Rank | Username | Followers | ER% | Score | Last post | Category |\n"
            "|------|----------|-----------|-----|-------|-----------|----------|\n\n"
            "### Disqualified\n"
            "| Username | Reason |\n"
            "|----------|--------|\n\n"
            "### Recommendation\n"
            f"Top 3 accounts best suited for {goal}. "
            "One sentence per account explaining why."
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]


# ═════════════════════════════════════════════════════════════════════════════
# TRANSPORT HELPER
# ═════════════════════════════════════════════════════════════════════════════

def _is_http_transport() -> bool:
    """Check if HTTP transport is requested via env var."""
    import os
    return os.environ.get("INSTAGRAM_MCP_TRANSPORT", "").lower() == "http"


def run_server() -> None:
    """
    Entry point — run the MCP server.

    Transport selection:
    - STDIO (default): for Claude Desktop / Claude Code
    - HTTP: set INSTAGRAM_MCP_TRANSPORT=http
           optionally set INSTAGRAM_MCP_HOST and INSTAGRAM_MCP_PORT
    """
    import os
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    mcp = create_mcp_server()

    if _is_http_transport():
        host = os.environ.get("INSTAGRAM_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("INSTAGRAM_MCP_PORT", "8000"))
        logger.info("Starting HTTP transport on %s:%d", host, port)
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        mcp.run(transport="stdio")


# Public exports
from .batch_runner import BatchConfig, BatchRunner, BatchStats
from .agents import (
    AccountHealthAgent,
    BulkScoringAgent,
    ContentAuditAgent,
    ContentAuditReport,
    CreatorDiscoveryAgent,
    InfluencerVettingAgent,
    ScoredAccount,
    VettingResult,
    HealthReport,
    DiscoveredCreator,
    compute_account_score,
    compute_er,
)

__all__ = [
    "create_mcp_server",
    "run_server",
    # Batch
    "BatchConfig",
    "BatchRunner",
    "BatchStats",
    # Agents
    "InfluencerVettingAgent",
    "AccountHealthAgent",
    "CreatorDiscoveryAgent",
    "BulkScoringAgent",
    "ContentAuditAgent",
    # Agent result types
    "VettingResult",
    "HealthReport",
    "DiscoveredCreator",
    "ScoredAccount",
    "ContentAuditReport",
    # Scoring helpers
    "compute_account_score",
    "compute_er",
    "__version__",
]
