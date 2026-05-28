"""Smoke checks for `.dockerignore` policy.

Validates: Requirements 23.4, 23.5.
"""
from __future__ import annotations

from pathlib import Path

import pytest


DOCKERIGNORE = Path(__file__).resolve().parents[1] / ".dockerignore"


def test_dockerignore_exists() -> None:
    assert DOCKERIGNORE.is_file(), ".dockerignore must exist (Requirement 23.4)"


REQUIRED_ENTRIES = [
    "cookie.txt",
    "cookies.json",
    "cookies.txt",
    "**/cookies.json",
    "**/cookies.txt",
    "*.env",
    "secrets.*",
    "MagicMock/",
    "exports/",
    "data/",
    "dist/",
    "*.mcpb",
    ".git/",
]


@pytest.mark.parametrize("entry", REQUIRED_ENTRIES)
def test_dockerignore_has_entry(entry: str) -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    assert entry in text, f".dockerignore missing required entry: {entry!r}"
