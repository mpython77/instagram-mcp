"""Assert heavy optional modules are NOT imported at submodule top level.

Validates: Requirement 20.2.
"""
from __future__ import annotations

import ast
from pathlib import Path


PKG_ROOT = Path(__file__).resolve().parents[1] / "instagram_mcp" / "tools"
HEAVY_MODULES = {"scheduler", "monitor", "oauth_manager", "session_manager"}


def _top_level_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # `from .scheduler import …` → module = "scheduler"; level may be >0
                head = node.module.split(".")[0] if node.level == 0 else node.module
                imports.add(head)
    return imports


def test_no_heavy_top_level_imports_in_tools_submodules() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(PKG_ROOT.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        top = _top_level_imports(tree)
        bad = top & HEAVY_MODULES
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        f"Heavy top-level imports found (should be lazy/in-function): {offenders}"
    )
