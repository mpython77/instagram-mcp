"""Static structural assertions on the tools/ package.

Validates: Requirements 1.5, 2.1, 2.2, 2.3, 2.5, 5.1, 22.3.
"""
from __future__ import annotations

import importlib
import inspect


CANONICAL_TOOLSETS = (
    "profile", "analysis", "content", "social_graph",
    "dm", "upload", "automation", "server",
)


def _get_module(toolset: str):
    return importlib.import_module(f"instagram_mcp.tools.{toolset}")


def test_every_submodule_exports_registrar_and_toolset_name() -> None:
    for toolset in CANONICAL_TOOLSETS:
        mod = _get_module(toolset)
        assert hasattr(mod, "TOOLSET_NAME"), f"{toolset}.TOOLSET_NAME missing"
        assert mod.TOOLSET_NAME == toolset, (
            f"{toolset}.TOOLSET_NAME = {mod.TOOLSET_NAME!r}, expected {toolset!r}"
        )
        registrar = getattr(mod, f"register_{toolset}", None)
        assert callable(registrar), f"register_{toolset} missing or not callable"


def test_registrar_signature_is_four_positional_params() -> None:
    for toolset in CANONICAL_TOOLSETS:
        mod = _get_module(toolset)
        registrar = getattr(mod, f"register_{toolset}")
        sig = inspect.signature(registrar)
        params = list(sig.parameters.values())
        assert len(params) == 4, (
            f"register_{toolset} has {len(params)} params, expected 4: {[p.name for p in params]}"
        )
        for p in params:
            assert p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            ), f"register_{toolset} param {p.name} is not positional"


def _build_mocks():
    from unittest.mock import MagicMock

    mock_mcp = MagicMock()
    # mcp.tool(...) returns a decorator; decorator returns the function
    mock_mcp.tool = lambda *a, **kw: (lambda fn: fn)
    mock_client = MagicMock()
    mock_client.cookie_manager = MagicMock(is_authenticated=False)
    mock_config = MagicMock()
    mock_config.hide_auth_when_no_cookies = False
    mock_config.enabled_toolsets = {"all"}
    mock_exporter = MagicMock()
    return mock_mcp, mock_client, mock_config, mock_exporter


def test_no_tool_name_collisions_across_submodules() -> None:
    """Stub-register every submodule and assert no two tools share a name."""
    from instagram_mcp.tools._helpers import ToolDescriptor

    mock_mcp, mock_client, mock_config, mock_exporter = _build_mocks()
    seen: dict[str, str] = {}

    for toolset in CANONICAL_TOOLSETS:
        mod = _get_module(toolset)
        registrar = getattr(mod, f"register_{toolset}")
        descriptors = registrar(mock_mcp, mock_client, mock_config, mock_exporter)
        assert isinstance(descriptors, list), f"{toolset} did not return list"
        for d in descriptors:
            assert isinstance(d, ToolDescriptor)
            assert d.name not in seen, (
                f"Tool name collision: {d.name!r} in {toolset!r} and {seen[d.name]!r}"
            )
            seen[d.name] = toolset


def test_every_tool_name_uses_instagram_prefix() -> None:
    mock_mcp, mock_client, mock_config, mock_exporter = _build_mocks()

    for toolset in CANONICAL_TOOLSETS:
        mod = _get_module(toolset)
        registrar = getattr(mod, f"register_{toolset}")
        descriptors = registrar(mock_mcp, mock_client, mock_config, mock_exporter)
        for d in descriptors:
            assert d.name.startswith("instagram_"), (
                f"{d.name!r} in {toolset!r} does not use the instagram_ prefix"
            )
