"""Snapshot the public API surface so regressions are caught.

Validates: Requirements 5.4, 5.5, 5.6, 5.7, 21.1, 21.2, 21.3, 24.1, 24.2, 24.3.
"""
from __future__ import annotations

from dataclasses import fields


def test_mcpconfig_field_names_are_stable() -> None:
    from instagram_mcp.config import MCPConfig
    expected = {
        # API
        "ig_endpoint", "ig_app_id", "ig_app_id_mobile", "ig_impersonate", "ig_user_agent",
        # Network
        "request_timeout", "max_retries", "max_workers", "async_max_clients",
        # Cache
        "cache_enabled", "cache_profile_ttl", "cache_tags_ttl", "cache_status_ttl",
        "cache_max_entries", "cache_feed_ttl", "cache_tagged_ttl", "cache_reposts_ttl",
        "cache_reels_ttl", "cache_comments_ttl",
        # Proxy
        "proxy_urls", "proxy_max_fails", "proxy_cooldown", "proxy_auto_fallback",
        "proxy_health_interval", "proxy_max_cooldown",
        "proxy_cb_fail_threshold", "proxy_cb_open_cooldown", "proxy_cb_max_cooldown",
        "proxy_max_concurrent",
        # Rate limiter
        "rate_limit_rps", "rate_limit_burst", "rate_limit_min_rps",
        "rate_backoff_factor", "rate_recovery_factor",
        "circuit_breaker_threshold", "circuit_breaker_cooldown",
        "per_proxy_rate_rps", "per_proxy_rate_burst",
        "retry_base_delay", "retry_jitter_std", "request_jitter",
        # Bulk
        "max_bulk_usernames", "default_bulk_concurrency",
        # Feed
        "default_max_feed_posts", "default_max_age_days", "dead_threshold_days",
        "ig_feed_endpoint", "ig_graphql_endpoint", "ig_graphql_doc_id",
        "ig_tagged_doc_id", "ig_reposts_doc_id", "ig_reels_doc_id",
        "max_pagination_posts", "pagination_page_size",
        # Export
        "export_enabled", "export_dir", "export_indent",
        # Auth
        "cookies_path", "accounts_dir", "media_cache_dir",
        # Toolsets
        "enabled_toolsets", "hide_auth_when_no_cookies",
        "delay_min_ms", "delay_max_ms",
        # Bio link filtering
        "social_domains",
    }
    actual = {f.name for f in fields(MCPConfig)}
    missing = expected - actual
    assert not missing, f"Public API regression — missing fields: {missing}"


def test_curl_cffi_impersonation_default_is_chrome142() -> None:
    """Requirement 24.1 — Chrome 142 fingerprint is the proven anti-bot config."""
    from instagram_mcp.config import MCPConfig
    cfg = MCPConfig()
    assert cfg.ig_impersonate == "chrome142"


def test_resource_uri_templates_are_stable() -> None:
    """Requirement 5.6 — resource URIs must not change."""
    from pathlib import Path as _P
    src = __import__("instagram_mcp", fromlist=["__file__"]).__file__
    text = _P(src).read_text(encoding="utf-8")
    for tmpl in (
        "instagram://profile/{username}",
        "instagram://feed/{username}",
        "instagram://server/status",
    ):
        assert tmpl in text, f"resource URI {tmpl!r} not registered"


def test_prompt_names_are_stable() -> None:
    """Requirement 5.7 — registered prompt names must remain unchanged."""
    from pathlib import Path as _P
    src = __import__("instagram_mcp", fromlist=["__file__"]).__file__
    text = _P(src).read_text(encoding="utf-8")
    for name in (
        "analyze_influencer",
        "find_brand_collaborations",
        "competitive_analysis",
        "account_audit",
    ):
        assert f'name="{name}"' in text, f"prompt {name!r} not registered"


def test_console_script_entry_point() -> None:
    """Requirement 21.3 — console script must remain `instagram_mcp:run_server`."""
    import tomllib
    from pathlib import Path as _P
    pyproject = _P(__file__).resolve().parents[1] / "pyproject.toml"
    cfg = tomllib.load(pyproject.open("rb"))
    scripts = cfg["project"]["scripts"]
    assert scripts.get("instagram-mcp") == "instagram_mcp:run_server"


def test_pyproject_dependency_floors() -> None:
    """Requirements 25.1, 25.2, 25.3."""
    import tomllib
    from pathlib import Path as _P
    pyproject = _P(__file__).resolve().parents[1] / "pyproject.toml"
    cfg = tomllib.load(pyproject.open("rb"))
    py = cfg["project"]["requires-python"]
    assert ">=3.10" in py
    deps = cfg["project"]["dependencies"]
    text = " ".join(deps)
    assert "mcp" in text and ">=1.0.0" in text
    assert "curl-cffi" in text and ">=0.7.0" in text
