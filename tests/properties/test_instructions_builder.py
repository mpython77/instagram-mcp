"""
Property 2: Server instructions builder invariants.

Feature: mcp-architecture-hardening, Property 2: Server instructions builder invariants.

Generates random `(inventory, auth_status)` tuples and asserts:
  - 6.2 toolset header order matches CANONICAL_ORDER
  - 6.3 tier badges are correct (anon→🌐, auth→🔐, auto→🌐/🔐)
  - 6.4 total count line reflects len(inventory)
  - 6.5 per-toolset and per-tier counts match the input
  - 6.6 empty-inventory case returns non-empty string with zero counts (no raise)
  - 6.7 auth_status appears verbatim in the header

Each generated descriptor has all five fields populated. Annotations contain
a non-empty title, the four bool hints, and an `auth_tier` chosen from anon/auth/auto.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import BaseModel

from instagram_mcp.tools._helpers import ToolDescriptor
from instagram_mcp.tools._instructions import (
    CANONICAL_ORDER,
    TIER_BADGE,
    build_server_instructions,
)


class _DummyInputModel(BaseModel):
    """Placeholder pydantic model — the builder doesn't introspect it."""
    pass


tool_name_st = st.from_regex(r"instagram_[a-z][a-z0-9_]{0,30}", fullmatch=True)
toolset_st = st.sampled_from(CANONICAL_ORDER)
tier_st = st.sampled_from(["anon", "auth", "auto"])
title_st = st.text(min_size=1, max_size=60).filter(lambda s: s.strip())
bool_st = st.booleans()
desc_first_line_st = st.text(min_size=1, max_size=80).filter(lambda s: s.strip())
auth_status_st = st.sampled_from([
    "authenticated",
    "anonymous (no cookies.txt)",
    "test-status-xyz",
])


def _build_descriptor(name: str, toolset: str, tier: str, title: str, desc_line: str) -> ToolDescriptor:
    badge = TIER_BADGE[tier]
    return ToolDescriptor(
        name=name,
        toolset=toolset,
        auth_tier=tier,  # type: ignore[arg-type]
        annotations={
            "title": title,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=_DummyInputModel,
        description_first_line=f"{badge} {desc_line}",
    )


inventory_st = st.lists(
    st.builds(_build_descriptor, tool_name_st, toolset_st, tier_st, title_st, desc_first_line_st),
    max_size=20,
).map(lambda lst: list({d.name: d for d in lst}.values()))  # dedupe by name


@given(inventory=inventory_st, auth_status=auth_status_st)
@settings(max_examples=200)
def test_invariants(inventory: list[ToolDescriptor], auth_status: str) -> None:
    out = build_server_instructions(inventory, auth_status)

    # 6.7 auth_status appears verbatim in the header
    assert auth_status in out

    # 6.4 total count line
    assert f"TOOLS (total: {len(inventory)})" in out

    # 6.5 per-tier counts in AUTH TIERS block
    by_tier = Counter(d.auth_tier for d in inventory)
    assert f"🌐 Anonymous: {by_tier.get('anon', 0)} tools" in out
    assert f"🔐 Authenticated: {by_tier.get('auth', 0)} tools" in out
    assert f"🌐/🔐 Auto-mode: {by_tier.get('auto', 0)} tools" in out

    # 6.5 per-toolset counts (only present toolsets)
    by_toolset = Counter(d.toolset for d in inventory)
    for ts, cnt in by_toolset.items():
        assert f"[{ts} — {cnt} tools]" in out

    # 6.2 canonical toolset order — present toolsets must appear in CANONICAL_ORDER
    present_indices = []
    for ts in by_toolset:
        header = f"[{ts} — "
        idx = out.find(header)
        assert idx >= 0
        present_indices.append((CANONICAL_ORDER.index(ts), idx))
    sorted_by_canonical = sorted(present_indices, key=lambda p: p[0])
    sorted_by_position = sorted(present_indices, key=lambda p: p[1])
    assert sorted_by_canonical == sorted_by_position, (
        "Toolset sections must appear in CANONICAL_ORDER"
    )

    # 6.3 every tool entry has the right badge
    for d in inventory:
        badge = TIER_BADGE[d.auth_tier]
        assert f"• {badge} {d.name} —" in out, f"missing entry for {d.name} ({d.auth_tier})"


def test_empty_inventory_does_not_raise() -> None:
    """6.6 — empty inventory returns a non-empty string with zero counts."""
    out = build_server_instructions([], "anonymous (no cookies.txt)")
    assert out  # non-empty
    assert "TOOLS (total: 0)" in out
    assert "🌐 Anonymous: 0 tools" in out
    assert "🔐 Authenticated: 0 tools" in out
    assert "🌐/🔐 Auto-mode: 0 tools" in out
    # No section headers when zero tools
    for ts in CANONICAL_ORDER:
        assert f"[{ts} —" not in out
