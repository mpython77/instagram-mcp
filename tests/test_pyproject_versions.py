"""Lock pyproject.toml dependency floors and entry points.

Validates: Requirements 21.3, 25.1, 25.2, 25.3.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
CFG = tomllib.load(PYPROJECT.open("rb"))


def test_requires_python_at_least_310() -> None:
    """25.1 — Python >= 3.10."""
    py = CFG["project"]["requires-python"]
    assert ">=3.10" in py, f"requires-python should pin >=3.10, got {py!r}"


def _dep_text() -> str:
    return " ".join(CFG["project"]["dependencies"])


def test_mcp_cli_floor_is_at_least_1_0_0() -> None:
    """25.2 — mcp[cli]>=1.0.0."""
    deps = _dep_text()
    assert re.search(r"mcp\[cli\][^,\s]*>=\s*1\.0\.0", deps), (
        f"mcp[cli]>=1.0.0 not pinned; got: {deps!r}"
    )


def test_curl_cffi_floor_is_at_least_0_7_0() -> None:
    """25.3 — curl-cffi>=0.7.0."""
    deps = _dep_text()
    assert re.search(r"curl-cffi[^,\s]*>=\s*0\.7\.0", deps), (
        f"curl-cffi>=0.7.0 not pinned; got: {deps!r}"
    )


def test_console_script_entry_is_run_server() -> None:
    """21.3 — console-script must remain `instagram-mcp = "instagram_mcp:run_server"`."""
    scripts = CFG["project"].get("scripts", {})
    assert scripts.get("instagram-mcp") == "instagram_mcp:run_server", (
        f"console-script regression: {scripts!r}"
    )


def test_description_does_not_quote_a_specific_tool_count() -> None:
    """10.1 — description must not contradict runtime inventory."""
    desc = CFG["project"]["description"]
    # Must not mention "<digits> tools" — that drifts whenever a tool is added/removed
    assert not re.search(r"\b\d+\s+tools\b", desc, re.IGNORECASE), (
        f"description still hardcodes a tool count: {desc!r}"
    )
