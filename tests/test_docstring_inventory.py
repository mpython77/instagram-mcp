"""Assert __init__.py and tools/__init__.py docstrings have no hardcoded counts.

Validates: Requirements 7.1, 7.2, 7.3, 7.4.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[1] / "instagram_mcp"
TARGETS = [PKG / "__init__.py", PKG / "tools" / "__init__.py"]
FORBIDDEN = re.compile(r"\b\d+\s+(tools|anonymous|auth)\b", re.IGNORECASE)


@pytest.mark.parametrize("path", TARGETS)
def test_module_docstring_has_no_hardcoded_counts(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = ast.get_docstring(tree) or ""
    matches = FORBIDDEN.findall(doc)
    assert not matches, (
        f"{path.name} docstring still mentions hardcoded counts: {matches}"
    )
