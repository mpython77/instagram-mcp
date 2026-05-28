"""Smoke checks for the new tools/ package layout.

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.6.
"""
from __future__ import annotations

import importlib
from pathlib import Path


PKG_ROOT = Path(__file__).resolve().parents[1] / "instagram_mcp"


def test_legacy_tools_py_is_absent() -> None:
    """Requirement 1.4: instagram_mcp/tools.py was deleted."""
    assert not (PKG_ROOT / "tools.py").is_file(), (
        "Legacy instagram_mcp/tools.py still exists; it must have been removed by task 8.2"
    )


def test_tools_package_imports_cleanly() -> None:
    mod = importlib.import_module("instagram_mcp.tools")
    assert hasattr(mod, "register_tools")
    assert hasattr(mod, "ToolDescriptor")
    assert hasattr(mod, "sanitize_username")


def test_every_canonical_submodule_imports() -> None:
    for ts in ("profile", "analysis", "content", "social_graph",
               "dm", "upload", "automation", "server"):
        importlib.import_module(f"instagram_mcp.tools.{ts}")


def test_helpers_exposes_public_symbols() -> None:
    from instagram_mcp.tools import _helpers
    for sym in ("sanitize_username", "_tool_error", "_exception_to_tool_error", "ToolDescriptor"):
        assert hasattr(_helpers, sym), f"_helpers missing {sym}"


def test_audit_and_instructions_modules_present() -> None:
    from instagram_mcp.tools import _audit, _instructions
    assert hasattr(_audit, "run_annotation_audit")
    assert hasattr(_audit, "DESTRUCTIVE_TOOLS")
    assert hasattr(_audit, "AnnotationAuditError")
    assert hasattr(_instructions, "build_server_instructions")
    assert hasattr(_instructions, "CANONICAL_ORDER")
    assert hasattr(_instructions, "TIER_BADGE")
