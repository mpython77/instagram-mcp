"""Smoke checks for `.gitignore` policy.

Validates: Requirements 11.2, 12.3, 12.4, 12.5, 13.1.
"""
from __future__ import annotations

from pathlib import Path

import pytest


GITIGNORE = Path(__file__).resolve().parents[1] / ".gitignore"
TEXT = GITIGNORE.read_text(encoding="utf-8")


REQUIRED_ENTRIES = [
    # Cookies / secrets
    "cookie.txt",
    "cookies.json",
    "cookies.txt",
    "data/cookies.json",
    "**/cookies.json",
    "**/cookies.txt",
    "*.env",
    "secrets.*",
    # Hygiene
    "MagicMock/",
    "exports/",
    "data/media_cache/",
    "dist/",
    "*.mcpb",
    ".pytest_cache/",
    ".state/",
    ".venv/",
    ".mypy_cache/",
    ".ruff_cache/",
]


@pytest.mark.parametrize("entry", REQUIRED_ENTRIES)
def test_gitignore_has_entry(entry: str) -> None:
    assert entry in TEXT, f".gitignore missing required entry: {entry!r}"


def test_gitignore_has_no_blanket_json_or_txt_rule() -> None:
    """12.5 - no `*.json` / `*.txt` blanket rule that would force `!`-negations."""
    for line in TEXT.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert stripped not in ("*.json", "*.txt", "*.jsonl"), (
            f".gitignore has a blanket rule {stripped!r}; use specific entries instead"
        )


def test_gitignore_has_no_negations() -> None:
    """12.5 - explicit, non-negated entries only."""
    for line in TEXT.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert not stripped.startswith("!"), (
            f".gitignore should not contain negations after the rewrite: {stripped!r}"
        )
