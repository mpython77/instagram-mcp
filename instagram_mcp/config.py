"""
Central configuration — all settings in one place, overridable by env vars.

Environment variables:
    INSTAGRAM_MCP_APP_ID           — Instagram app ID (default: 936619743392459)
    INSTAGRAM_MCP_IMPERSONATE      — curl_cffi impersonate target (default: chrome142)
    INSTAGRAM_MCP_TIMEOUT          — Request timeout in seconds (default: 10)
    INSTAGRAM_MCP_MAX_RETRIES      — Max retry count (default: 3)
    INSTAGRAM_MCP_MAX_WORKERS      — Default concurrency for batch operations (default: 12)
    INSTAGRAM_MCP_MAX_CLIENTS      — curl_cffi AsyncSession internal handle pool size (default: 50)
    INSTAGRAM_MCP_PROXIES          — Proxy URLs separated by comma (or proxies.txt)
    INSTAGRAM_MCP_PROXY_MAX_FAILS  — Proxy max consecutive fails (default: 5)
    INSTAGRAM_MCP_PROXY_COOLDOWN   — Proxy cooldown in seconds (default: 30)
    INSTAGRAM_MCP_RATE_LIMIT_RPS              — Requests per second (default: 100.0)
    INSTAGRAM_MCP_RATE_LIMIT_BURST            — Burst token count (default: 50)
    INSTAGRAM_MCP_RATE_BACKOFF_FACTOR         — Rate multiplier on 429 (default: 0.7)
    INSTAGRAM_MCP_RATE_RECOVERY_FACTOR        — Rate multiplier on success (default: 1.15)
    INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD   — Consecutive 429s to open circuit (default: 5)
    INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN    — Seconds to sleep on open circuit (default: 60.0)
    INSTAGRAM_MCP_PROXY_MAX_COOLDOWN          — Max proxy cooldown in seconds (default: 300.0)
    INSTAGRAM_MCP_REQUEST_JITTER              — Max jitter for token-bucket sleep (default: 0.1)
    INSTAGRAM_MCP_CACHE_DISABLED              — Disable cache: '1' or 'true'
    INSTAGRAM_MCP_CACHE_TTL        — Global cache TTL in seconds (default: 300)
    INSTAGRAM_MCP_CACHE_MAX        — Max cache entries (default: 500)
    INSTAGRAM_MCP_GRAPHQL_DOC_ID   — GraphQL doc_id for feed pagination
    INSTAGRAM_MCP_MAX_PAGINATION   — Max posts for pagination (default: 200)
    INSTAGRAM_MCP_EXPORT_ENABLED   — '0' or 'false' disables JSON auto-save (default: enabled)
    INSTAGRAM_MCP_EXPORT_DIR       — Directory for saved JSON files (default: ./exports)
    INSTAGRAM_MCP_EXPORT_INDENT    — JSON indentation spaces, 0 = compact (default: 2)
    INSTAGRAM_MCP_TOOLSETS         — Comma-separated toolset names to enable, or 'all' (default).
                                     Valid: profile, analysis, content, social_graph, batch, server, all
    INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES — '1'/'true' hides auth-only tools when no cookies are loaded
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

from ._path_guard import ensure_path


@dataclass
class MCPConfig:
    """Instagram MCP server configuration."""

    # ── Instagram API ────────────────────────────────────────────────────────
    ig_endpoint: str = (
        "https://i.instagram.com/api/v1/users/web_profile_info/?username={}"
    )
    ig_app_id: str = "1217981644879628"   # web app ID (www.instagram.com)
    ig_app_id_mobile: str = "936619743392459"  # mobile app ID (i.instagram.com)
    ig_impersonate: str = "chrome142"
    ig_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    )

    # ── Network ──────────────────────────────────────────────────────────────
    request_timeout: int = 10
    max_retries: int = 3              # 3 retries = 3 different proxies
    max_workers: int = 12
    # curl_cffi AsyncSession's internal libcurl handle pool size.
    # Default in curl_cffi is 10 — catastrophic for 50-100 worker batch jobs
    # because all coroutines on the same proxy session contend for 10 handles.
    # Raise to 50 so high-concurrency batches actually run in parallel.
    async_max_clients: int = 50

    # ── Cache ────────────────────────────────────────────────────────────────
    cache_enabled: bool = True
    cache_profile_ttl: int = 300      # 5 min — profile data
    cache_tags_ttl: int = 120         # 2 min — tags (changes quickly)
    cache_status_ttl: int = 600       # 10 min — account status
    cache_max_entries: int = 500

    # ── Proxy ────────────────────────────────────────────────────────────────
    proxy_urls: List[str] = field(default_factory=list)
    proxy_max_fails: int = 5          # 5 consecutive fails → short cooldown
    proxy_cooldown: int = 30          # 30s cooldown — proxy returns quickly
    proxy_auto_fallback: bool = True  # All proxies down → direct connection
    proxy_health_interval: int = 30   # Health check interval (seconds)

    # ── Rate Limiting ────────────────────────────────────────────────────────
    rate_limit_rps: float = 100.0     # Proxy means limit is not needed
    rate_limit_burst: int = 50        # Large burst — proxy handles it
    rate_limit_min_rps: float = 5.0   # Never slow down too much

    # Adaptive backoff / recovery
    rate_backoff_factor: float = 0.7          # Multiply rate by this on 429 (was 0.5)
    rate_recovery_factor: float = 1.15        # Multiply rate by this on success (was 1.05)

    # Circuit breaker
    circuit_breaker_threshold: int = 5        # Consecutive 429s before circuit opens
    circuit_breaker_cooldown: float = 60.0    # Seconds to sleep when circuit opens

    # Proxy
    proxy_max_cooldown: float = 300.0         # Max proxy cooldown in seconds

    # ── Per-proxy circuit breaker (3-state CLOSED / OPEN / HALF_OPEN) ─────────
    proxy_cb_fail_threshold: int = 3          # consecutive failures → OPEN
    proxy_cb_open_cooldown: float = 30.0      # initial OPEN cooldown (seconds)
    proxy_cb_max_cooldown: float = 300.0      # max OPEN cooldown (5 min)
    proxy_max_concurrent: int = 30            # per-proxy bulkhead (concurrent reqs)

    # ── Per-proxy rate limiter (token bucket per IP) ─────────────────────────
    per_proxy_rate_rps: float = 1.0           # Instagram realistic limit per IP
    per_proxy_rate_burst: int = 3

    # ── Retry jitter (Gaussian) ──────────────────────────────────────────────
    retry_base_delay: float = 0.5             # base delay between retries (seconds)
    retry_jitter_std: float = 0.5             # Gaussian std-dev for sleep jitter

    # Jitter
    request_jitter: float = 0.1              # Max jitter added to token-bucket sleep (seconds)

    # ── Bulk ─────────────────────────────────────────────────────────────────
    max_bulk_usernames: int = 20
    default_bulk_concurrency: int = 5  # High concurrency with proxies

    # ── Feed Parsing ─────────────────────────────────────────────────────────
    default_max_feed_posts: int = 12
    default_max_age_days: int = 4
    dead_threshold_days: int = 365

    # ── Feed Pagination (v1/feed/user) ───────────────────────────────────────
    ig_feed_endpoint: str = "https://i.instagram.com/api/v1/feed/user/{}/"

    # ── Feed Pagination (GraphQL) ────────────────────────────────────────────
    ig_graphql_endpoint: str = "https://www.instagram.com/graphql/query/"
    ig_graphql_doc_id: str = "26442143102071041"  # PolarisProfilePostsTabContentQuery_connection
    ig_tagged_doc_id: str = "26707104818956021"   # PolarisProfileTaggedTabContentQuery_connection
    ig_reposts_doc_id: str = "35095888563388407"  # PolarisProfileRepostsTabContentRefetchQuery
    ig_reels_doc_id: str = "26292852833730510"    # PolarisProfileReelsTabContentQuery_connection
    max_pagination_posts: int = 200   # Hard ceiling for deep feed analysis
    pagination_page_size: int = 50    # Posts per GraphQL page
    cache_feed_ttl: int = 180         # 3 min — paginated feed data
    cache_tagged_ttl: int = 300       # 5 min — tagged-by feed
    cache_reposts_ttl: int = 300      # 5 min — reposts tab
    cache_reels_ttl: int = 300        # 5 min — reels tab
    cache_comments_ttl: int = 60      # 1 min — comments change quickly

    # ── JSON Auto-Export ─────────────────────────────────────────────────────
    export_enabled: bool = True       # Write every tool result to JSON file
    export_dir: str = "exports"       # Root output directory
    export_indent: int = 2            # JSON pretty-print indent (0 = compact)

    # ── Authentication (cookies.txt) ─────────────────────────────────────────
    cookies_path: str = ""            # Override via INSTAGRAM_MCP_COOKIES env var
    accounts_dir: str = ""            # Path to directory containing multiple accounts (e.g. data/accounts/)
    media_cache_dir: str = ""         # Path to directory containing cached media files (e.g. data/media_cache/)

    # ── Toolsets (tool registration gating) ──────────────────────────────────
    # Controls which groups of MCP tools are registered at startup.
    # Empty set or {"all"} means register every group.
    # Valid names: profile, analysis, content, social_graph, batch, server.
    # The "server" group (instagram_server) is always enabled regardless.
    enabled_toolsets: Set[str] = field(default_factory=lambda: {"all"})
    # Hide auth-only tools (instagram_search, instagram_tagged_by, instagram_reposts,
    # instagram_reels, instagram_stories, instagram_highlights, instagram_followers_list,
    # instagram_following_list, instagram_post_likers) when no cookies are loaded.
    hide_auth_when_no_cookies: bool = False
    delay_min_ms: int = 500
    delay_max_ms: int = 2000

    # ── Bio Link Filtering ───────────────────────────────────────────────────
    social_domains: Set[str] = field(default_factory=lambda: {
        "tiktok.com", "youtube.com", "twitter.com", "x.com",
        "facebook.com", "t.me", "telegram.me",
        "pinterest.com", "snapchat.com", "linkedin.com",
        "open.spotify.com", "music.apple.com",
        "linktr.ee", "beacons.ai", "bio.link",
        "campsite.bio", "withkoji.com",
    })

    @classmethod
    def from_env(cls) -> MCPConfig:
        """Create configuration from environment variables."""
        cfg = cls()

        # API
        if v := os.environ.get("INSTAGRAM_MCP_APP_ID"):
            cfg.ig_app_id = v
        if v := os.environ.get("INSTAGRAM_MCP_IMPERSONATE"):
            cfg.ig_impersonate = v
        if v := os.environ.get("INSTAGRAM_MCP_USER_AGENT"):
            cfg.ig_user_agent = v

        # Network
        if v := os.environ.get("INSTAGRAM_MCP_TIMEOUT"):
            cfg.request_timeout = int(v)
        if v := os.environ.get("INSTAGRAM_MCP_MAX_RETRIES"):
            cfg.max_retries = int(v)
        if v := os.environ.get("INSTAGRAM_MCP_MAX_WORKERS"):
            cfg.max_workers = int(v)
        if v := os.environ.get("INSTAGRAM_MCP_MAX_CLIENTS"):
            cfg.async_max_clients = max(1, int(v))

        # Cache
        if os.environ.get("INSTAGRAM_MCP_CACHE_DISABLED", "").lower() in ("1", "true"):
            cfg.cache_enabled = False
        if v := os.environ.get("INSTAGRAM_MCP_CACHE_TTL"):
            ttl = int(v)
            cfg.cache_profile_ttl = ttl
            cfg.cache_tags_ttl = max(ttl // 2, 30)
            cfg.cache_status_ttl = ttl * 2
        if v := os.environ.get("INSTAGRAM_MCP_CACHE_MAX"):
            cfg.cache_max_entries = int(v)

        # Proxy — environment variable
        if v := os.environ.get("INSTAGRAM_MCP_PROXIES"):
            cfg.proxy_urls = [u.strip() for u in v.split(",") if u.strip()]
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_MAX_FAILS"):
            cfg.proxy_max_fails = int(v)
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_COOLDOWN"):
            cfg.proxy_cooldown = int(v)

        # Proxy — proxies.txt fallback
        if not cfg.proxy_urls:
            cfg.proxy_urls = _load_proxy_file()

        # Rate limit
        if v := os.environ.get("INSTAGRAM_MCP_RATE_LIMIT_RPS"):
            cfg.rate_limit_rps = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_RATE_LIMIT_BURST"):
            cfg.rate_limit_burst = int(v)

        # Adaptive backoff / circuit breaker / jitter
        if v := os.environ.get("INSTAGRAM_MCP_RATE_BACKOFF_FACTOR"):
            cfg.rate_backoff_factor = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_RATE_RECOVERY_FACTOR"):
            cfg.rate_recovery_factor = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD"):
            cfg.circuit_breaker_threshold = int(v)
        if v := os.environ.get("INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN"):
            cfg.circuit_breaker_cooldown = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_MAX_COOLDOWN"):
            cfg.proxy_max_cooldown = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_REQUEST_JITTER"):
            cfg.request_jitter = float(v)

        # Per-proxy circuit breaker / bulkhead
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_CB_FAIL_THRESHOLD"):
            cfg.proxy_cb_fail_threshold = max(1, int(v))
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_CB_OPEN_COOLDOWN"):
            cfg.proxy_cb_open_cooldown = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_CB_MAX_COOLDOWN"):
            cfg.proxy_cb_max_cooldown = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_PROXY_MAX_CONCURRENT"):
            cfg.proxy_max_concurrent = max(1, int(v))

        # Per-proxy token-bucket
        if v := os.environ.get("INSTAGRAM_MCP_PER_PROXY_RPS"):
            cfg.per_proxy_rate_rps = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_PER_PROXY_BURST"):
            cfg.per_proxy_rate_burst = max(1, int(v))

        # Retry jitter
        if v := os.environ.get("INSTAGRAM_MCP_RETRY_BASE_DELAY"):
            cfg.retry_base_delay = float(v)
        if v := os.environ.get("INSTAGRAM_MCP_RETRY_JITTER_STD"):
            cfg.retry_jitter_std = float(v)

        # GraphQL pagination
        if v := os.environ.get("INSTAGRAM_MCP_GRAPHQL_DOC_ID"):
            cfg.ig_graphql_doc_id = v
        if v := os.environ.get("INSTAGRAM_MCP_MAX_PAGINATION"):
            cfg.max_pagination_posts = int(v)

        # Authentication
        if v := os.environ.get("INSTAGRAM_MCP_COOKIES"):
            cfg.cookies_path = ensure_path(v, name="instagram_mcp_cookies")
        if v := os.environ.get("INSTAGRAM_MCP_ACCOUNTS_DIR"):
            cfg.accounts_dir = ensure_path(v, name="instagram_mcp_accounts_dir")
        if v := os.environ.get("INSTAGRAM_MCP_MEDIA_CACHE_DIR"):
            cfg.media_cache_dir = ensure_path(v, name="instagram_mcp_media_cache_dir")

        # JSON auto-export
        if os.environ.get("INSTAGRAM_MCP_EXPORT_ENABLED", "").lower() in ("0", "false"):
            cfg.export_enabled = False
        if v := os.environ.get("INSTAGRAM_MCP_EXPORT_DIR"):
            cfg.export_dir = ensure_path(v, name="instagram_mcp_export_dir")
        if v := os.environ.get("INSTAGRAM_MCP_EXPORT_INDENT"):
            cfg.export_indent = int(v)

        # Toolset selection
        if v := os.environ.get("INSTAGRAM_MCP_TOOLSETS"):
            parts = {p.strip().lower() for p in v.split(",") if p.strip()}
            cfg.enabled_toolsets = parts or {"all"}
        if os.environ.get("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "").lower() in ("1", "true"):
            cfg.hide_auth_when_no_cookies = True

        if v := os.environ.get("INSTAGRAM_MCP_DELAY_MIN"):
            cfg.delay_min_ms = int(v)
        if v := os.environ.get("INSTAGRAM_MCP_DELAY_MAX"):
            cfg.delay_max_ms = int(v)

        return cfg

    @property
    def ig_headers(self) -> Dict[str, str]:
        """HTTP headers for Instagram API."""
        return {
            "x-ig-app-id": self.ig_app_id,
            "x-ig-www-claim": "0",
            "User-Agent": self.ig_user_agent,
        }


def _load_proxy_file() -> List[str]:
    """Read proxy URLs from proxies.txt file."""
    candidates = [
        Path(__file__).parent.parent / "proxies.txt",
        Path.cwd() / "proxies.txt",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return [
                    line.strip()
                    for line in path.read_text().splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
        except Exception:
            continue
    return []
