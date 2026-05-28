"""
Property 6: ToolError taxonomy membership.

Feature: mcp-architecture-hardening, Property 6: ToolError taxonomy membership.

Walks every `.py` file under `instagram_mcp/` via AST, collects:
  1. Every `_tool_error(error_type=<literal>)` call with a literal `error_type`
  2. Every `error_type` class attribute on classes inheriting from
     `InstagramMCPError`

Asserts both sets are subsets of `ALLOWED_ERROR_TYPES` from
`instagram_mcp.exceptions`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from instagram_mcp.exceptions import ALLOWED_ERROR_TYPES


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "instagram_mcp"


def _iter_py_files() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py"))


def _collect_tool_error_literals(tree: ast.AST) -> list[tuple[str, int]]:
    """Find every `_tool_error(...)` call where `error_type` is a string literal.

    Returns list of (literal_value, line_number) pairs.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match _tool_error(...) — by name, regardless of import alias
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id == "_tool_error":
            pass
        elif isinstance(callee, ast.Attribute) and callee.attr == "_tool_error":
            pass
        else:
            continue

        # Positional arg 1 (after msg) is error_type, OR keyword 'error_type'
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            found.append((node.args[1].value, node.lineno))
        for kw in node.keywords:
            if kw.arg == "error_type" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                found.append((kw.value.value, node.lineno))
    return found


def _collect_class_error_types(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Find every `error_type = "..."` class attribute on InstagramMCPError subclasses.

    Returns list of (class_name, literal_value, line_number) tuples.
    """
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Filter to classes that look like exception types — heuristic:
        # they inherit from InstagramMCPError or another *Error name.
        bases = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                bases.append(b.id)
            elif isinstance(b, ast.Attribute):
                bases.append(b.attr)
        if not any("Error" in b or "Exception" in b for b in bases):
            continue
        # Walk the class body for `error_type = <constant>`
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "error_type":
                        if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                            found.append((node.name, stmt.value.value, stmt.lineno))
            elif isinstance(stmt, ast.AnnAssign):
                if (
                    isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "error_type"
                    and stmt.value is not None
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    found.append((node.name, stmt.value.value, stmt.lineno))
    return found


# Build the global inventory ONCE so hypothesis can sample from it.
_ALL_FILES = _iter_py_files()
_ALL_LITERALS: list[tuple[Path, str, int]] = []
_ALL_CLASS_ATTRS: list[tuple[Path, str, str, int]] = []
for _path in _ALL_FILES:
    try:
        _src = _path.read_text(encoding="utf-8")
        _tree = ast.parse(_src)
    except (SyntaxError, UnicodeDecodeError):
        continue
    for lit, lineno in _collect_tool_error_literals(_tree):
        _ALL_LITERALS.append((_path, lit, lineno))
    for cls, lit, lineno in _collect_class_error_types(_tree):
        _ALL_CLASS_ATTRS.append((_path, cls, lit, lineno))


@pytest.mark.skipif(not _ALL_LITERALS, reason="No _tool_error literals discovered.")
@given(idx=st.integers(min_value=0, max_value=max(0, len(_ALL_LITERALS) - 1)))
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_tool_error_literals_in_taxonomy(idx: int) -> None:
    """Every `_tool_error(error_type=<literal>)` call uses an allowed value."""
    path, literal, lineno = _ALL_LITERALS[idx]
    assert literal in ALLOWED_ERROR_TYPES, (
        f"{path.relative_to(PACKAGE_ROOT.parent)}:{lineno}: "
        f"_tool_error error_type={literal!r} is not in ALLOWED_ERROR_TYPES"
    )


@pytest.mark.skipif(not _ALL_CLASS_ATTRS, reason="No exception class error_type attrs discovered.")
@given(idx=st.integers(min_value=0, max_value=max(0, len(_ALL_CLASS_ATTRS) - 1)))
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_exception_class_error_types_in_taxonomy(idx: int) -> None:
    """Every InstagramMCPError subclass declares an allowed `error_type`."""
    path, cls, literal, lineno = _ALL_CLASS_ATTRS[idx]
    assert literal in ALLOWED_ERROR_TYPES, (
        f"{path.relative_to(PACKAGE_ROOT.parent)}:{lineno}: "
        f"class {cls}.error_type = {literal!r} is not in ALLOWED_ERROR_TYPES"
    )


def test_taxonomy_inventory_is_non_empty():
    """Sanity: we actually scanned the package and found >0 literals/attrs."""
    assert _ALL_LITERALS, "no _tool_error literals found — AST walk broken?"
    assert _ALL_CLASS_ATTRS, "no exception class error_type found — AST walk broken?"
