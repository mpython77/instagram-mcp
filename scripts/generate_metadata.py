#!/usr/bin/env python3
"""Generate metadata files from the runtime tool inventory — single source of truth.

Targets (all regenerated from one source):
  * ``manifest.json``                         (Desktop Extension / DXT manifest)
  * ``smithery.yaml``                          (Smithery.ai server descriptor)
  * ``.well-known/mcp/server-card.json``       (MCP server card)

Usage::

    python scripts/generate_metadata.py            # rewrite all three files
    python scripts/generate_metadata.py --check    # verify in sync; exit 1 on drift
    python scripts/generate_metadata.py --dry-run  # print generated files, write nothing

Source of truth
---------------
* The **set of tools**, their canonical **order**, ``toolset``, ``auth_tier``,
  ``title``, tool **annotations**, and **input schema** all come from
  ``create_mcp_server()._instagram_tool_inventory`` — the live registry.
* The **version** comes from ``instagram_mcp.__version__``.
* The per-tool one-line **descriptions** and the marketing **blurb** are curated
  here (``_DESCRIPTIONS`` / ``_FEATURES_*``). The runtime docstrings begin with a
  tier banner ("🌐 NO LOGIN REQUIRED …") that is not a useful one-line summary,
  so a curated map is kept instead.

Drift guard
-----------
The generator raises if a runtime tool has **no** curated description, or if
``_DESCRIPTIONS`` contains a key that is **not** a runtime tool. Combined with
``tests/test_metadata_sync.py`` this makes it impossible for the three metadata
files (or the curated map) to silently drift from the live tool surface.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST_PATH = REPO_ROOT / "manifest.json"
SMITHERY_PATH = REPO_ROOT / "smithery.yaml"
CARD_PATH = REPO_ROOT / ".well-known" / "mcp" / "server-card.json"


# ---------------------------------------------------------------------------
# Curated copy (the only hand-maintained pieces)
# ---------------------------------------------------------------------------

_LEAD = "Production-grade MCP server for Instagram."

# Feature blurb used by manifest.json (slightly shorter house style).
_FEATURES_MANIFEST = (
    "Profile scraping, engagement analytics, DM inbox/send, hashtag research, "
    "stories, reels, uploads, social actions, batch scraping up to 2000 profiles, "
    "post scheduling, account monitoring, multi-account sessions, OAuth support, "
    "comment sentiment analysis (EN/UZ/RU), multi-account pool with auto-failover, "
    "and challenge/2FA resolver."
)

# Feature blurb used by smithery.yaml and the server card (includes the stack note).
_FEATURES_FULL = (
    "Profile scraping, engagement analytics, DM inbox/send, hashtag research, "
    "stories, reels, uploads, social actions (like, follow, comment, block), "
    "batch scraping up to 2000 profiles, post scheduling, account monitoring, "
    "multi-account sessions, OAuth support, comment sentiment analysis (EN/UZ/RU), "
    "multi-account pool with auto-failover, and challenge/2FA resolver. Built on "
    "curl_cffi with Chrome TLS impersonation, adaptive rate limiting, and smart "
    "caching."
)

_AUTHOR = {
    "name": "mpython77",
    "url": "https://github.com/mpython77/instagram-mcp",
}

_SERVER = {
    "type": "python",
    "mcp_config": {
        "command": "uvx",
        "arguments": [
            "--from",
            "git+https://github.com/mpython77/instagram-mcp",
            "instagram-mcp",
        ],
        "env": {},
    },
}

_USER_CONFIG = {
    "INSTAGRAM_MCP_COOKIES": {
        "type": "string",
        "description": (
            "Path to your exported Instagram cookies file (cookies.json or "
            "cookies.txt). Required for authenticated tools."
        ),
        "default": "",
    },
    "INSTAGRAM_MCP_PROXIES": {
        "type": "string",
        "description": (
            "Optional comma-separated proxy URLs, e.g. "
            "http://user:pass@host:port"
        ),
        "default": "",
    },
}

# One-line human descriptions, keyed by runtime tool name. The generator asserts
# this map is a 1:1 match with the live inventory, so adding a tool to the server
# forces a description here (and vice-versa).
_DESCRIPTIONS: dict[str, str] = {
    # profile
    "instagram_profile": "Profile metadata, follower/following counts, bio, recent feed tags, activity status.",
    "instagram_feed_deep": "Paginated feed analysis up to 200 posts with date filtering and content breakdown.",
    "instagram_compare_profiles": "Side-by-side comparison table for 2–5 accounts.",
    "instagram_bulk_check": "Fetch up to 20 accounts in parallel with status for each.",
    "instagram_threads_profile": "Profile metadata from Threads (threads.net) — followers, bio, verification.",
    "instagram_threads_posts": "Recent posts for a Threads user.",
    # analysis
    "instagram_analyze_engagement": "Engagement rate %, content mix, best posting days, top hashtags across up to 200 posts.",
    "instagram_find_collab_network": "Map usertags, @mentions, co-authors, and paid sponsors across recent posts.",
    "instagram_hashtag_suggest": "Related hashtag suggestions by analyzing top posts under a seed tag.",
    "instagram_caption_analyze": "Caption patterns — avg length, hashtag density, emoji rates, CTA frequency.",
    "instagram_account_report": "Full profile + engagement analytics + collab network in one call.",
    "instagram_analyze_comments": "Sentiment analysis on post comments — positive/neutral/negative with emoji stats and keywords (EN/UZ/RU). No login required.",
    # content
    "instagram_post": "Full post details — location (GPS + Maps link), music, usertags, coauthors, exact timestamp.",
    "instagram_post_comments": "Fetch comments with likes and thread structure (up to 500).",
    "instagram_hashtag": "Top posts for a hashtag. Anon: 12 posts; authenticated: up to 300. Auto-upgrades.",
    "instagram_hashtag_deep": "Top accounts, content breakdown, best posting hour across up to 500 hashtag posts.",
    "instagram_post_bulk": "Fetch up to 50 posts in parallel by shortcode or URL.",
    "instagram_niche_top": "Account leaderboard for a hashtag ranked by engagement, post count, or total likes.",
    "instagram_stories": "Currently active stories (cached 2 min). (AUTH)",
    "instagram_highlights": "Highlights tray + optional story items inside. (AUTH)",
    "instagram_location_posts": "Top posts at a location by Instagram location ID or name. (AUTH)",
    "instagram_audio_reels": "Reels using a specific audio track by audio_cluster_id. (AUTH)",
    "instagram_reels": "Account's reels with play counts — the only tool that exposes play_count. (AUTH)",
    "instagram_tagged_by": "Posts by other accounts that tag this account. (AUTH)",
    "instagram_reposts": "Content this account actively reposted. (AUTH)",
    # social_graph
    "instagram_search": "Search users and hashtags by keyword. (AUTH)",
    "instagram_followers_list": "Recent followers with mutual follow status. (AUTH)",
    "instagram_following_list": "Full following list with close-friends detection. (AUTH)",
    "instagram_post_likers": "Users who liked a specific post (up to ~98). (AUTH)",
    "instagram_similar_accounts": "Accounts Instagram considers similar via internal chaining API. (AUTH)",
    "instagram_post_comment": "Post a comment. (AUTH)",
    "instagram_user_search": "User search with higher-quality ranking via authenticated API. (AUTH)",
    "instagram_user_followers": "Paginated followers for any user by numeric user_id. (AUTH)",
    "instagram_user_following": "Paginated following for any user by numeric user_id. (AUTH)",
    "instagram_story_mark_seen": "Mark stories as viewed. (AUTH)",
    "instagram_story_reply": "Reply to a story via DM. (AUTH)",
    "instagram_edit_profile": "Edit bio, display name, website, email, or phone. (AUTH)",
    "instagram_post_save": "Save or unsave (bookmark) a post. (AUTH)",
    "instagram_block_user": "Block or unblock a user. (AUTH)",
    "instagram_post_like": "Like or unlike a post. (AUTH)",
    "instagram_follow_user": "Follow or unfollow a user. (AUTH)",
    "instagram_delete_comment": "Delete a comment. (AUTH)",
    "instagram_publish_story": "Publish a photo as a Story (24h). (AUTH)",
    "instagram_broadcast_channel": "Read a creator's Broadcast Channel — info or messages. (AUTH)",
    "instagram_comment_reply": "Reply to a specific comment. (AUTH)",
    "instagram_comment_like": "Like or unlike a comment. (AUTH)",
    "instagram_comment_hide": "Hide a comment on your own post. (AUTH)",
    "instagram_post_delete": "Delete one of your own posts. (AUTH)",
    "instagram_toggle_comments": "Disable or enable comments on your post. (AUTH)",
    "instagram_media_insights": "Impressions, reach, saves for your own posts (Business/Creator accounts only). (AUTH)",
    "instagram_upload_video": "Upload an MP4 as a regular video post. (AUTH)",
    "instagram_account_privacy": "Switch account between public and private. (AUTH)",
    "instagram_home_feed": "Your authenticated home feed. (AUTH)",
    "instagram_saved_posts": "Your bookmarked posts. (AUTH)",
    "instagram_liked_posts": "Posts you have liked. (AUTH)",
    "instagram_activity_feed": "Your recent activity notifications. (AUTH)",
    "instagram_compare_followers": "Compare follower/following sets between two accounts — find unfollowers or non-mutuals.",
    "instagram_user_id_lookup": "Look up a user's numeric ID by username. (AUTH)",
    "instagram_submit_verification_code": "Submit SMS/Email/2FA code to resolve a pending Instagram checkpoint challenge and restore the account session. (AUTH)",
    # dm
    "instagram_dm_inbox": "Read DM inbox — list of threads with last message preview. (AUTH)",
    "instagram_dm_thread": "Fetch messages in a specific DM thread with pagination. (AUTH)",
    "instagram_dm_send": "Send a text DM to a user or thread. (AUTH)",
    "instagram_dm_send_photo": "Send a photo in a DM. (AUTH)",
    "instagram_dm_send_video": "Send a video in a DM. (AUTH)",
    "instagram_dm_react": "Add or remove an emoji reaction on a DM message. (AUTH)",
    "instagram_dm_unsend": "Delete a sent DM message. (AUTH)",
    "instagram_dm_mark_seen": "Mark a DM thread as seen. (AUTH)",
    # upload
    "instagram_upload_photo": "Upload 1–10 images as a post or carousel. (AUTH)",
    "instagram_upload_reel": "Upload an MP4 as a Reel. (AUTH)",
    "instagram_download": "Download all media from a post (single/video/carousel) to local disk.",
    # automation
    "instagram_batch_scrape": "Scrape up to 2000 profiles. profile_only=True gives 30–60x speedup for bulk bio/follower scraping.",
    "instagram_schedule": "Schedule posts for future publishing — add, list, cancel, status. (AUTH)",
    "instagram_monitor": "Poll accounts for new posts and fire webhooks on new content. (AUTH)",
    "instagram_sessions": "Manage multiple Instagram accounts via INSTAGRAM_MCP_COOKIES_<ALIAS> env vars.",
    "instagram_oauth": "Full Graph API OAuth 2.0 flow — init_flow, exchange_code, refresh_token, status.",
    # audience
    "instagram_best_time_to_post": "Best posting times and weekdays based on historical engagement across up to 200 posts.",
    # server
    "instagram_server": "Diagnostics and cache management — status, clear_cache, clear_user, reload_cookies.",
    "instagram_metrics": "Server metrics — per-tool request counts, latency, and error rates. Actions: get, reset.",
    "instagram_plugins": "List loaded server plugins and their status.",
}

# Smithery's startCommand block is hand-authored JS/JSON-Schema and is preserved
# verbatim. ``__ANON_COUNT__`` is substituted with the live anonymous-tier count.
_SMITHERY_START_COMMAND = """\
startCommand:
  type: stdio
  configSchema:
    type: object
    properties:
      cookies_path:
        type: string
        description: >
          Path to your exported Instagram cookies file (cookies.json from
          Cookie-Editor or cookies.txt in Netscape format). Required only for
          authenticated tools (DMs, likes, follow, upload, stories, etc.).
          Leave empty to use only the __ANON_COUNT__ anonymous tools.
        default: ""
      proxies:
        type: string
        description: >
          Optional comma-separated proxy URLs, e.g.
          "http://user:pass@host:port,http://host2:port2".
          Also accepts a path to a proxies.txt file (one proxy per line).
        default: ""
      toolsets:
        type: string
        description: >
          Comma-separated list of toolsets to enable. Available:
          profile, analysis, content, social_graph, batch, server, all.
          Default is "all".
        default: "all"
      hide_auth_tools:
        type: string
        description: >
          Set to "1" to hide authenticated-only tools when no cookies are
          loaded. Keeps the tool list clean for anonymous usage.
        default: ""
    additionalProperties: false
  commandFunction: |-
    (config) => {
      const env = {};
      if (config.cookies_path) env.INSTAGRAM_MCP_COOKIES = config.cookies_path;
      if (config.proxies)      env.INSTAGRAM_MCP_PROXIES = config.proxies;
      if (config.toolsets)     env.INSTAGRAM_MCP_TOOLSETS = config.toolsets;
      if (config.hide_auth_tools) env.INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES = config.hide_auth_tools;
      return { command: "python", args: ["-m", "instagram_mcp"], env };
    }"""


# ---------------------------------------------------------------------------
# Inventory loading
# ---------------------------------------------------------------------------

def load_inventory() -> tuple[list[Any], str]:
    """Build the live MCP server and return ``(inventory, version)``.

    ``INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES`` defaults to ``"0"`` so the full
    tool surface (including auth-tier tools) is advertised even without cookies.
    """
    os.environ.setdefault("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "0")
    import instagram_mcp
    from instagram_mcp import create_mcp_server

    mcp = create_mcp_server()
    inventory = list(mcp._instagram_tool_inventory)  # type: ignore[attr-defined]
    version = str(getattr(instagram_mcp, "__version__", "0.0.0"))
    _assert_descriptions_in_sync(inventory)
    return inventory, version


def _assert_descriptions_in_sync(inventory: list[Any]) -> None:
    runtime_names = {d.name for d in inventory}
    curated = set(_DESCRIPTIONS)
    missing = runtime_names - curated
    extra = curated - runtime_names
    if missing or extra:
        raise SystemExit(
            "Curated _DESCRIPTIONS is out of sync with the runtime inventory.\n"
            f"  Missing a description for: {sorted(missing)}\n"
            f"  Description for unknown tool: {sorted(extra)}\n"
            "Edit scripts/generate_metadata.py:_DESCRIPTIONS to fix."
        )


def _tier_counts(inventory: list[Any]) -> tuple[int, int, int]:
    """Return ``(anon, auth, auto)`` counts."""
    by_tier = Counter(d.auth_tier for d in inventory)
    return by_tier.get("anon", 0), by_tier.get("auth", 0), by_tier.get("auto", 0)


def _description(counts: tuple[int, int, int], features: str) -> str:
    anon, auth, auto = counts
    total = anon + auth + auto
    return (
        f"{_LEAD} {total} tools — {anon} anonymous (no credentials), "
        f"{auth} authenticated, {auto} auto-mode. {features}"
    )


# ---------------------------------------------------------------------------
# Input-schema simplifier (pydantic JSON schema -> minimal MCP inputSchema)
# ---------------------------------------------------------------------------

def _json_type(field: dict) -> str:
    """Resolve a single JSON type from a pydantic field schema fragment."""
    if "type" in field:
        return field["type"]
    for sub in field.get("anyOf", []):
        t = sub.get("type")
        if t and t != "null":
            return t
    return "string"


def _prop_schema(field: dict) -> dict:
    t = _json_type(field)
    out: dict[str, Any] = {"type": t}
    if t == "array":
        items = field.get("items")
        if items is None:
            for sub in field.get("anyOf", []):
                if sub.get("type") == "array":
                    items = sub.get("items")
                    break
        out["items"] = {"type": _json_type(items)} if items else {"type": "string"}
    return out


def _input_schema(model: Any) -> dict:
    raw = model.model_json_schema()
    props = {name: _prop_schema(field) for name, field in raw.get("properties", {}).items()}
    out: dict[str, Any] = {"type": "object", "properties": props}
    required = raw.get("required")
    if required:
        out["required"] = list(required)
    return out


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_manifest(inventory: list[Any], version: str) -> dict:
    counts = _tier_counts(inventory)
    tools = [
        {
            "name": d.name,
            "description": _DESCRIPTIONS[d.name],
            "inputSchema": _input_schema(d.input_model),
        }
        for d in inventory
    ]
    return {
        "manifest_version": "0.3",
        "name": "instagram-mcp",
        "version": version,
        "description": _description(counts, _FEATURES_MANIFEST),
        "author": _AUTHOR,
        "server": _SERVER,
        "user_config": _USER_CONFIG,
        "tools": tools,
        "tools_generated": True,
    }


def build_server_card(inventory: list[Any], version: str) -> dict:
    counts = _tier_counts(inventory)
    tools = [
        {
            "name": d.name,
            "title": d.annotations.get("title", d.name),
            "auth_tier": d.auth_tier,
            "toolset": d.toolset,
            "annotations": {
                "readOnlyHint": bool(d.annotations.get("readOnlyHint", False)),
                "idempotentHint": bool(d.annotations.get("idempotentHint", False)),
                "destructiveHint": bool(d.annotations.get("destructiveHint", False)),
                "openWorldHint": bool(d.annotations.get("openWorldHint", False)),
            },
        }
        for d in inventory
    ]
    return {
        "name": "instagram-mcp",
        "version": version,
        "description": _description(counts, _FEATURES_FULL),
        "tools": tools,
    }


def build_smithery(inventory: list[Any], version: str) -> str:
    counts = _tier_counts(inventory)
    anon = counts[0]
    desc = _description(counts, _FEATURES_FULL)

    lines: list[str] = ["name: instagram-mcp", f'version: "{version}"', "description: >-"]
    lines.extend(f"  {line}" for line in textwrap.wrap(desc, width=92))
    lines.append("")
    lines.append(_SMITHERY_START_COMMAND.replace("__ANON_COUNT__", str(anon)))
    lines.append("")
    lines.append("tools:")
    last_toolset: str | None = None
    for d in inventory:
        if d.toolset != last_toolset:
            lines.append(f"  # {d.toolset}")
            last_toolset = d.toolset
        lines.append(f"  - name: {d.name}")
        lines.append(f"    description: {json.dumps(_DESCRIPTIONS[d.name], ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _json_text(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Rendering + IO
# ---------------------------------------------------------------------------

def render_all(inventory: list[Any], version: str) -> dict[Path, str]:
    """Return a mapping of ``path -> generated text`` for every target file."""
    return {
        MANIFEST_PATH: _json_text(build_manifest(inventory, version)),
        SMITHERY_PATH: build_smithery(inventory, version),
        CARD_PATH: _json_text(build_server_card(inventory, version)),
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Verify files are in sync; exit 1 on drift.")
    group.add_argument("--dry-run", action="store_true", help="Print generated files; write nothing.")
    args = parser.parse_args(argv)

    inventory, version = load_inventory()
    rendered = render_all(inventory, version)

    if args.dry_run:
        for path, text in rendered.items():
            print(f"\n===== {path.relative_to(REPO_ROOT)} =====")
            print(text, end="")
        return 0

    if args.check:
        drift: list[str] = []
        for path, text in rendered.items():
            rel = path.relative_to(REPO_ROOT)
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != text:
                drift.append(str(rel))
        if drift:
            print(
                "Metadata files are OUT OF SYNC with the runtime inventory:\n  "
                + "\n  ".join(drift)
                + "\n\nRun: python scripts/generate_metadata.py",
                file=sys.stderr,
            )
            return 1
        print("Metadata files are in sync with the runtime inventory.")
        return 0

    for path, text in rendered.items():
        _atomic_write(path, text)
        print(f"Wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
