"""Assert tools/__init__.py never declares inline @mcp.tool.

Validates: Requirement 3.4.
"""
from __future__ import annotations

import ast
from pathlib import Path


def test_no_inline_mcp_tool_decorators() -> None:
    pkg_init = Path(__file__).resolve().parents[1] / "instagram_mcp" / "tools" / "__init__.py"
    src = pkg_init.read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            # @mcp.tool(...) or @mcp.tool
            if isinstance(deco, ast.Call):
                fn = deco.func
                attr = getattr(fn, "attr", None)
                if attr == "tool":
                    offenders.append((node.lineno, node.name))
            elif isinstance(deco, ast.Attribute) and deco.attr == "tool":
                offenders.append((node.lineno, node.name))
    assert not offenders, f"Inline @mcp.tool decorators found: {offenders}"
