"""Metadata parity tests — manifest.json / smithery.yaml / server-card.json.

Analogous to ``tests/test_readme_sync.py``: assert the three machine-readable
metadata files stay 1:1 with the runtime tool inventory.

The runtime inventory is built in a subprocess because the shared
``tests/conftest.py`` stubs ``mcp.server.fastmcp.FastMCP`` with ``MagicMock``,
which is not a faithful enough double for ``create_mcp_server()``. The
subprocess uses the real installed ``mcp[cli]`` library and reports the
inventory (names, tiers, version) back as JSON over stdout.

The strongest guarantee comes from ``test_generator_check_passes``: it runs
``scripts/generate_metadata.py --check``, which byte-compares every committed
file against freshly generated content and fails on any drift. The remaining
tests provide targeted, human-readable failures for the most common drifts
(missing/phantom tools, wrong counts, stale version).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "generate_metadata.py"
MANIFEST = REPO_ROOT / "manifest.json"
SMITHERY = REPO_ROOT / "smithery.yaml"
CARD = REPO_ROOT / ".well-known" / "mcp" / "server-card.json"


# ---------------------------------------------------------------------------
# Runtime inventory via subprocess
# ---------------------------------------------------------------------------

_INVENTORY_SNIPPET = """
import json, os, sys
os.environ.setdefault('INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES', '0')
import instagram_mcp
from instagram_mcp import create_mcp_server
mcp = create_mcp_server()
out = {
    'version': str(getattr(instagram_mcp, '__version__', '0.0.0')),
    'tools': [
        {'name': d.name, 'toolset': d.toolset, 'auth_tier': d.auth_tier}
        for d in mcp._instagram_tool_inventory
    ],
}
sys.stdout.write(json.dumps(out))
"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "0")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
    )


@pytest.fixture(scope="module")
def runtime() -> dict:
    proc = _run([sys.executable, "-c", _INVENTORY_SNIPPET])
    if proc.returncode != 0:
        pytest.fail(f"could not build runtime inventory: stderr={proc.stderr!r}")
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def runtime_names(runtime) -> set[str]:
    return {t["name"] for t in runtime["tools"]}


@pytest.fixture(scope="module")
def tier_counts(runtime) -> dict[str, int]:
    return dict(Counter(t["auth_tier"] for t in runtime["tools"]))


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def _manifest_names() -> set[str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {t["name"] for t in data["tools"]}


def _card_names() -> set[str]:
    data = json.loads(CARD.read_text(encoding="utf-8"))
    return {t["name"] for t in data["tools"]}


def _smithery_names() -> set[str]:
    # Parse "  - name: instagram_xxx" without a YAML dependency.
    text = SMITHERY.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*-\s*name:\s*(instagram_[a-z0-9_]+)\s*$", text, re.MULTILINE))


# ---------------------------------------------------------------------------
# Strong guarantee: the generator agrees with every committed file
# ---------------------------------------------------------------------------

def test_generator_exists() -> None:
    assert GENERATOR.is_file(), "scripts/generate_metadata.py missing"


def test_generator_check_passes() -> None:
    """`generate_metadata.py --check` byte-compares all three files to the live surface."""
    proc = _run([sys.executable, str(GENERATOR), "--check"])
    assert proc.returncode == 0, (
        "Metadata files are out of sync with the runtime inventory. "
        "Run `python scripts/generate_metadata.py`.\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Targeted parity assertions (one file per concern, readable failures)
# ---------------------------------------------------------------------------

def test_every_runtime_tool_present_in_all_files(runtime_names) -> None:
    for label, names in (
        ("manifest.json", _manifest_names()),
        ("smithery.yaml", _smithery_names()),
        ("server-card.json", _card_names()),
    ):
        missing = runtime_names - names
        assert not missing, f"{label} is missing runtime tools: {sorted(missing)}"


def test_no_phantom_tools_in_any_file(runtime_names) -> None:
    for label, names in (
        ("manifest.json", _manifest_names()),
        ("smithery.yaml", _smithery_names()),
        ("server-card.json", _card_names()),
    ):
        phantom = names - runtime_names
        assert not phantom, f"{label} references non-existent tools: {sorted(phantom)}"


def test_description_counts_match_runtime(tier_counts) -> None:
    """Every description sentence reports the live per-tier counts."""
    anon = tier_counts.get("anon", 0)
    auth = tier_counts.get("auth", 0)
    auto = tier_counts.get("auto", 0)

    manifest_desc = json.loads(MANIFEST.read_text(encoding="utf-8"))["description"]
    card_desc = json.loads(CARD.read_text(encoding="utf-8"))["description"]
    smithery_text = SMITHERY.read_text(encoding="utf-8")

    # Collapse whitespace so a folded YAML scalar (where "56\n  authenticated"
    # spans two lines) still matches the contiguous "56 authenticated" phrase.
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", text)

    for label, text in (
        ("manifest.json", manifest_desc),
        ("server-card.json", card_desc),
        ("smithery.yaml", smithery_text),
    ):
        norm = _norm(text)
        assert f"{anon} anonymous" in norm, f"{label} count for anonymous != {anon}"
        assert f"{auth} authenticated" in norm, f"{label} count for authenticated != {auth}"
        assert f"{auto} auto-mode" in norm, f"{label} count for auto-mode != {auto}"


def test_versions_match_package_version(runtime) -> None:
    version = runtime["version"]

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == version, "manifest.json version drifted from package"

    card = json.loads(CARD.read_text(encoding="utf-8"))
    assert card["version"] == version, "server-card.json version drifted from package"

    smithery_text = SMITHERY.read_text(encoding="utf-8")
    assert re.search(rf'^version:\s*"{re.escape(version)}"\s*$', smithery_text, re.MULTILINE), (
        "smithery.yaml version drifted from package"
    )


def test_manifest_marked_generated() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest.get("tools_generated") is True, (
        "manifest.json should set tools_generated: true (it is generated by "
        "scripts/generate_metadata.py)"
    )
