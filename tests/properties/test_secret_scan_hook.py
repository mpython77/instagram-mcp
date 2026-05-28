"""
Property 5: Secret-scan hook path-blocklist contract.

Feature: mcp-architecture-hardening, Property 5: Secret-scan hook path-blocklist contract.

Generates random POSIX-style paths, computes the expected blocked status using
the documented patterns, runs `scripts/check_no_secrets.py` as a subprocess,
and asserts:
  - exit code matches expected
  - blocked paths appear verbatim in stderr
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_no_secrets.py"


BLOCKLIST = (
    "cookie.txt",
    "cookies.json",
    "cookies.txt",
    "*.env",
    "secrets.*",
    "**/cookies.json",
    "**/cookies.txt",
)


def _expected_blocked(path: str) -> bool:
    """Reference matcher mirroring the documented patterns."""
    norm = path.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    for pat in BLOCKLIST:
        if fnmatch.fnmatch(base, pat):
            return True
        if fnmatch.fnmatch(norm, pat):
            return True
        # `**/...` style - strip the prefix and check tail match
        if pat.startswith("**/"):
            tail = pat[3:]
            if fnmatch.fnmatch(base, tail):
                return True
    return False


# Strategy: build random POSIX-style paths from controlled segments. We mix
# benign segments with deliberately-blocked segments so both branches see
# coverage.
SAFE_SEGMENTS = st.sampled_from(["src", "lib", "tests", "docs", "data", "tmp"])
FILENAME = st.one_of(
    st.sampled_from([
        "README.md", "manifest.json", "LICENSE", "config.yaml", "main.py",
        "cookie.txt", "cookies.json", "cookies.txt", ".env", "prod.env",
        "secrets.json", "secrets.yaml", "secrets.txt",
    ]),
    st.from_regex(r"[a-z][a-z0-9_]{0,16}\.(py|json|md|txt|yaml)", fullmatch=True),
)


@st.composite
def random_path(draw):
    depth = draw(st.integers(min_value=0, max_value=4))
    parts = [draw(SAFE_SEGMENTS) for _ in range(depth)]
    parts.append(draw(FILENAME))
    return "/".join(parts)


@pytest.mark.skipif(not SCRIPT.is_file(), reason="check_no_secrets.py not present")
@given(path=random_path())
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_secret_scan_blocklist_contract(path: str) -> None:
    expected = _expected_blocked(path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), path],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if expected:
        assert result.returncode == 1, (
            f"Path {path!r} should have been blocked but exit was {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        assert path in result.stderr, (
            f"Blocked path {path!r} not echoed verbatim in stderr: {result.stderr!r}"
        )
    else:
        assert result.returncode == 0, (
            f"Path {path!r} should have been allowed but exit was {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
