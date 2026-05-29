"""
Property 1: Toolset gating contract.

Feature: mcp-architecture-hardening, Property 1: Toolset gating contract.

Generates random `(enabled_toolsets, hide_auth_when_no_cookies, is_authenticated,
per-module ToolDescriptor lists)`, monkeypatches each submodule's
`register_<toolset>` to return the generated list, runs `register_tools`,
and asserts:
  - `mcp._instagram_tool_inventory` matches the gated/concatenated expected list
  - The `server` submodule is always invoked regardless of `enabled_toolsets`
  - Submodules NOT in `enabled_toolsets` are not invoked (except `server`)
  - The orchestrator iterates in CANONICAL_ORDER

The hide_auth_when_no_cookies flag is per-submodule responsibility (not the
orchestrator), so this property treats per-module return lists as already
gating-resolved. The orchestrator only resolves `enabled_toolsets` and
canonical ordering.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import BaseModel

from instagram_mcp.tools import CANONICAL_ORDER, register_tools
from instagram_mcp.tools._helpers import ToolDescriptor


class _DummyInputModel(BaseModel):
    pass


def _make_descriptor(toolset: str, idx: int, tier: str = "anon") -> ToolDescriptor:
    return ToolDescriptor(
        name=f"instagram_{toolset}_{idx}",
        toolset=toolset,
        auth_tier=tier,  # type: ignore[arg-type]
        annotations={
            "title": f"Test {toolset} {idx}",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        input_model=_DummyInputModel,
        description_first_line=f"🌐 test {toolset} {idx}",
    )


# Strategy: per-toolset list of N descriptors, where N can be 0..3
counts_st = st.fixed_dictionaries({
    ts: st.integers(min_value=0, max_value=3) for ts in CANONICAL_ORDER
})

# Strategy: enabled_toolsets — either {"all"} OR a non-empty subset of CANONICAL_ORDER
enabled_st = st.one_of(
    st.just({"all"}),
    st.sets(st.sampled_from(CANONICAL_ORDER), min_size=0, max_size=len(CANONICAL_ORDER)),
)


@given(counts=counts_st, enabled=enabled_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_orchestrator_gating_contract(monkeypatch, counts, enabled) -> None:
    """register_tools obeys CANONICAL_ORDER and toolset gating."""
    # Build per-toolset descriptors
    per_toolset: dict[str, list[ToolDescriptor]] = {
        ts: [_make_descriptor(ts, i) for i in range(counts[ts])]
        for ts in CANONICAL_ORDER
    }

    # Track which registrars were invoked, in order
    invocations: list[str] = []

    def _make_fake_registrar(ts: str):
        def _registrar(mcp, client, config, exporter):
            invocations.append(ts)
            return list(per_toolset[ts])
        return _registrar

    # Monkeypatch the _REGISTRARS dispatch table so the test does not actually
    # invoke real registrars (which would try to call into FastMCP).
    fake_registrars = {ts: _make_fake_registrar(ts) for ts in CANONICAL_ORDER}
    monkeypatch.setattr("instagram_mcp.tools._REGISTRARS", fake_registrars)

    mcp = MagicMock()
    client = MagicMock()
    client.cookie_manager = MagicMock(is_authenticated=False)
    config = MagicMock()
    config.enabled_toolsets = enabled
    config.hide_auth_when_no_cookies = False
    exporter = MagicMock()

    register_tools(mcp, client, config, exporter)

    # Compute expected enabled set with the same rules as _resolve_enabled_toolsets
    if not enabled or "all" in enabled:
        expected_enabled = set(CANONICAL_ORDER)
    else:
        expected_enabled = set(enabled) | {"server"}  # server always

    # The `server` toolset is always invoked
    assert "server" in invocations, "server submodule must always run (Requirement 4.3)"

    # Only enabled toolsets were invoked
    assert set(invocations) == expected_enabled, (
        f"invocations={set(invocations)!r}, expected={expected_enabled!r}, "
        f"enabled_toolsets={enabled!r}"
    )

    # Invocation order matches CANONICAL_ORDER
    canonical_ordered = [ts for ts in CANONICAL_ORDER if ts in expected_enabled]
    assert invocations == canonical_ordered, (
        f"invocations={invocations!r} not in CANONICAL_ORDER {canonical_ordered!r}"
    )

    # Inventory equals concatenation of per-toolset lists in canonical order
    expected_inventory: list[ToolDescriptor] = []
    for ts in canonical_ordered:
        expected_inventory.extend(per_toolset[ts])
    actual = mcp._instagram_tool_inventory
    assert [d.name for d in actual] == [d.name for d in expected_inventory], (
        "inventory does not match expected concatenation"
    )


def test_server_registrar_failure_is_logged_in_degraded_mode(monkeypatch, caplog) -> None:
    """If server registrar raises, orchestrator logs ERROR and continues."""
    import logging

    boom = RuntimeError("boom from server")
    fake_registrars = {
        "profile": lambda *a, **kw: [_make_descriptor("profile", 0)],
        "analysis": lambda *a, **kw: [],
        "content": lambda *a, **kw: [],
        "social_graph": lambda *a, **kw: [],
        "dm": lambda *a, **kw: [],
        "upload": lambda *a, **kw: [],
        "automation": lambda *a, **kw: [],
        "audience": lambda *a, **kw: [],
        "server": lambda *a, **kw: (_ for _ in ()).throw(boom),
    }
    monkeypatch.setattr("instagram_mcp.tools._REGISTRARS", fake_registrars)

    mcp = MagicMock()
    client = MagicMock()
    config = MagicMock()
    config.enabled_toolsets = {"all"}
    config.hide_auth_when_no_cookies = False
    exporter = MagicMock()

    with caplog.at_level(logging.ERROR, logger="instagram_mcp.tools"):
        register_tools(mcp, client, config, exporter)

    # Server failure was logged at ERROR
    assert any("Server toolset registration failed" in r.message for r in caplog.records), (
        f"degraded-mode error log not emitted; got {[r.message for r in caplog.records]!r}"
    )
    # Other tools still registered
    assert len(mcp._instagram_tool_inventory) == 1
    assert mcp._instagram_tool_inventory[0].name == "instagram_profile_0"
