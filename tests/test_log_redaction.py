"""Static check that no logger call concatenates known secret-shaped values.

Validates: Requirements 23.1, 23.2.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[1] / "instagram_mcp"
TARGET_FILES = [
    PKG / "client.py",
    PKG / "cookie_manager.py",
    PKG / "oauth_manager.py",
    PKG / "challenge.py",
    PKG / "agents.py",
]

# Patterns that indicate a logger call is leaking a secret. We grep raw lines
# and trigger on common bad patterns. Keep this list tight to avoid noise.
FORBIDDEN_PATTERNS = [
    # `logger.<lvl>("... %s ...", access_token, ...)` - bare token positional arg
    re.compile(r"logger\.\w+\([^)]*access_token\b(?!\[)"),
    # Same for raw cookie strings - but allow safe `len(self._cookies)` count usage
    re.compile(r"logger\.\w+\([^)]*(?<!len\()self\._cookies\b"),
    # Sending raw Cookie header content to log
    re.compile(r"logger\.\w+\([^)]*Cookie:\s*%"),
]


@pytest.mark.parametrize("path", TARGET_FILES)
def test_no_forbidden_log_patterns(path: Path) -> None:
    if not path.is_file():
        # agents.py / challenge.py may not exist in every checkout - skip gracefully
        pytest.skip(f"{path.name} not found")
    text = path.read_text(encoding="utf-8")
    for pat in FORBIDDEN_PATTERNS:
        m = pat.search(text)
        assert not m, (
            f"{path.name}: forbidden log pattern matched at {m.span()}: {m.group(0)!r}"
        )
