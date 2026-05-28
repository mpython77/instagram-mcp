"""Assert the server card is in sync with the runtime tool inventory.

Validates: Requirement 21.4.

We use subprocesses for both the script invocation AND the runtime inventory
lookup. The shared `tests/conftest.py` mocks `FastMCP` with `MagicMock`, which
isn't a faithful enough double for `create_mcp_server()` to run end-to-end in
the test process; subprocesses use the real installed `mcp[cli]` library.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "regenerate_server_card.py"
CARD = REPO_ROOT / ".well-known" / "mcp" / "server-card.json"


_INVENTORY_SNIPPET = """
import json, os, sys
os.environ.setdefault('INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES', '0')
from instagram_mcp import create_mcp_server
mcp = create_mcp_server()
sys.stdout.write(json.dumps([d.name for d in mcp._instagram_tool_inventory]))
"""


def _runtime_names() -> set[str]:
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
    return set(json.loads(proc.stdout))


@pytest.mark.skipif(not SCRIPT.is_file(), reason="regenerate_server_card.py not present")
def test_dry_run_card_matches_runtime_inventory() -> None:
    """Run regenerate_server_card.py --dry-run and verify the tool list matches runtime."""
    env = os.environ.copy()
    env.setdefault("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "0")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"script failed: stderr={result.stderr!r}"

    card = json.loads(result.stdout)
    if "capabilities" in card and isinstance(card["capabilities"], dict) and "tools" in card["capabilities"]:
        tools = card["capabilities"]["tools"]
    else:
        tools = card.get("tools", [])

    card_names = {t["name"] for t in tools}
    runtime_names = _runtime_names()

    only_in_card = card_names - runtime_names
    only_in_runtime = runtime_names - card_names
    assert not only_in_card and not only_in_runtime, (
        f"server-card drift detected.\n  only in card: {only_in_card}\n  only in runtime: {only_in_runtime}"
    )


@pytest.mark.skipif(not CARD.is_file(), reason="server-card.json not present")
def test_committed_card_top_level_keys_preserved() -> None:
    """The script must not erase non-tool top-level keys (Requirement 21.4)."""
    card = json.loads(CARD.read_text(encoding="utf-8"))
    for k in ("name", "description"):
        assert k in card, f"server-card.json missing top-level key {k!r}"
