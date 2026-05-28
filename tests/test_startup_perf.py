"""Bound startup performance so refactors don't regress cold-start.

Validates: Requirements 20.1, 20.3.

We do NOT track an absolute baseline (machines vary too much); instead we
assert two things:
  1. `import instagram_mcp.tools` completes in <2 s on this machine.
  2. `create_mcp_server()` completes in <5 s on this machine.

Both bounds are generous to absorb CI / Windows variance. They protect
against catastrophic regressions (e.g. accidentally importing the entire
batch_runner or scheduler at module top level).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest


REPO_PYTHON = sys.executable


def _run_with_timing(snippet: str) -> float:
    """Run the snippet in a fresh interpreter; return wall-clock seconds."""
    env = os.environ.copy()
    env.setdefault("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "0")
    t0 = time.perf_counter()
    result = subprocess.run(
        [REPO_PYTHON, "-c", snippet],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        pytest.fail(f"subprocess failed: stderr={result.stderr!r}")
    return elapsed


def test_tools_package_cold_import_under_2s() -> None:
    """20.1 — `import instagram_mcp.tools` must not regress catastrophically."""
    elapsed = _run_with_timing("import instagram_mcp.tools")
    # Generous absolute cap; the orchestrator should be fast.
    assert elapsed < 2.0, f"cold import of instagram_mcp.tools took {elapsed:.2f}s"


def test_create_mcp_server_under_5s() -> None:
    """20.3 — `create_mcp_server()` cold-start must stay snappy."""
    elapsed = _run_with_timing(
        "from instagram_mcp import create_mcp_server; create_mcp_server()"
    )
    # The full pipeline runs registration, audit, and instructions build.
    # 5s is generous on slow CI runners.
    assert elapsed < 5.0, f"create_mcp_server() took {elapsed:.2f}s"
