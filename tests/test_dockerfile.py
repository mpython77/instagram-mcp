"""Smoke check that the Dockerfile does not COPY secrets into the image.

Validates: Requirement 23.3.
"""
from __future__ import annotations

import re
from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
TEXT = DOCKERFILE.read_text(encoding="utf-8")

FORBIDDEN_TOKENS = ["cookies.json", "cookies.txt", "cookie.txt", "*.env", "secrets.*"]


def test_no_copy_line_includes_forbidden_tokens() -> None:
    """No `COPY ...` line may pull cookies / *.env / secrets.* into the image."""
    for lineno, raw in enumerate(TEXT.splitlines(), start=1):
        if not raw.lstrip().upper().startswith("COPY"):
            continue
        for token in FORBIDDEN_TOKENS:
            assert token not in raw, (
                f"Dockerfile line {lineno} copies forbidden token {token!r}: {raw!r}"
            )


def test_no_copy_dot_dot_pattern() -> None:
    """`COPY . .` is risky without a tight .dockerignore - flag explicitly."""
    pattern = re.compile(r"^\s*COPY\s+\.\s+\.\s*$", re.MULTILINE | re.IGNORECASE)
    matches = pattern.findall(TEXT)
    assert not matches, (
        "Dockerfile uses `COPY . .` which would ingest the whole tree. "
        "Use an explicit allowlist of paths instead."
    )
