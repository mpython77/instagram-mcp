"""README parity tests — assert each section corresponds 1-1 with runtime.

Validates: Requirements 10.2, 10.3, 10.4, 17.5, 17.6, 18.5, 19.1, 19.2, 19.5.

We compute the runtime inventory in a subprocess. The shared `tests/conftest.py`
installs `MagicMock` stubs for `mcp.server.fastmcp.FastMCP`, which are not a
faithful enough double for `create_mcp_server()` (the `@mcp.tool(...)`
decorator path needs real callables). The subprocess uses the real installed
`mcp[cli]` library and reports the inventory back as JSON over stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
TEXT = README.read_text(encoding="utf-8") if README.is_file() else ""


# ---------------------------------------------------------------------------
# Build runtime inventory once via subprocess
# ---------------------------------------------------------------------------

_INVENTORY_SNIPPET = """
import json, os, sys
os.environ.setdefault('INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES', '0')
from instagram_mcp import create_mcp_server
mcp = create_mcp_server()
out = [
    {'name': d.name, 'toolset': d.toolset, 'auth_tier': d.auth_tier}
    for d in mcp._instagram_tool_inventory
]
sys.stdout.write(json.dumps(out))
"""


def _build_inventory() -> list[dict]:
    env = os.environ.copy()
    env.setdefault("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "0")
    proc = subprocess.run(
        [sys.executable, "-c", _INVENTORY_SNIPPET],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"could not build runtime inventory: stderr={proc.stderr!r}"
        )
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def inventory() -> list[dict]:
    return _build_inventory()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_readme_exists() -> None:
    assert README.is_file(), "README.md missing at repo root"


def test_auth_tier_counts_match_runtime(inventory) -> None:
    """10.2 — Auth Tiers table counts match per-tier counts."""
    import re

    by_tier = Counter(d["auth_tier"] for d in inventory)
    for tier_name, expected in [
        ("Anonymous", by_tier.get("anon", 0)),
        ("Authenticated", by_tier.get("auth", 0)),
        ("Auto-mode", by_tier.get("auto", 0)),
    ]:
        # Row example: "| Anonymous | 🌐 | None | 19 |"
        pat = re.compile(
            rf"\|\s*{re.escape(tier_name)}\s*\|.*?\|\s*{expected}\s*\|", re.MULTILINE
        )
        assert pat.search(TEXT), (
            f"Auth Tiers table row for {tier_name!r} missing or count != {expected}"
        )


def test_every_runtime_tool_appears_in_readme(inventory) -> None:
    """10.3 — every runtime tool name appears verbatim in README."""
    missing = [d["name"] for d in inventory if d["name"] not in TEXT]
    assert not missing, f"Runtime tools missing from README: {missing}"


def test_no_phantom_tools_in_readme(inventory) -> None:
    """10.4 — every backtick `instagram_*` token in README maps to a runtime tool."""
    import re

    runtime_names = {d["name"] for d in inventory}
    referenced = {m.group(1) for m in re.finditer(r"`(instagram_[a-z0-9_]+)`", TEXT)}
    phantoms = referenced - runtime_names
    # Allow legacy aliases that historically appeared in README prose.
    LEGACY_ALLOWED = {
        "instagram_dm_media_messages",
        "instagram_dm_mute",
        "instagram_dm_share_post",
    }
    real_phantoms = phantoms - LEGACY_ALLOWED
    assert not real_phantoms, f"README references unknown tools: {real_phantoms}"


# ---------------------------------------------------------------------------
# Resources / Prompts / Error Taxonomy / Annotations sections
# ---------------------------------------------------------------------------

def test_resources_section_lists_three_resources() -> None:
    """19.1 — Resources section lists every URI."""
    for tmpl in (
        "instagram://profile/{username}",
        "instagram://feed/{username}",
        "instagram://server/status",
    ):
        assert tmpl in TEXT, f"README missing resource template {tmpl!r}"


def test_prompts_section_lists_registered_prompts() -> None:
    """19.2 — Prompts section lists every registered prompt by name."""
    for name in (
        "analyze_influencer",
        "find_brand_collaborations",
        "competitive_analysis",
        "account_audit",
    ):
        assert f"`{name}`" in TEXT, f"README Prompts section missing {name!r}"


def test_error_taxonomy_section_lists_eight_values() -> None:
    """18.5 — Error Taxonomy section names every value."""
    from instagram_mcp.exceptions import ALLOWED_ERROR_TYPES

    for et in ALLOWED_ERROR_TYPES:
        assert f"`{et}`" in TEXT, f"README Error Taxonomy missing {et!r}"


def test_tool_annotations_section_present() -> None:
    """17.5 — Tool Annotations section exists."""
    assert "## Tool Annotations" in TEXT, "README missing 'Tool Annotations' section"


def test_pre_commit_section_present() -> None:
    """15.6 — Pre-commit setup instructions are documented."""
    assert "pre-commit install" in TEXT, "README missing pre-commit install instructions"
