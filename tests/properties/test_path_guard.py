"""
Property 4: Path-argument guard contract.

Feature: mcp-architecture-hardening, Property 4: Path-argument guard contract.

Generates random non-path values (MagicMock, int, list, None, custom objs)
and random valid path values (str, bytes, pathlib.Path/PurePosixPath/PureWindowsPath).
Asserts `ensure_path` raises `TypeError` iff value is not in the allowed set,
and that the message mentions the parameter name and `type(v).__name__`.

Plus regression cases: AccountPool, MediaCache, JsonExporter constructed with
a MagicMock raise TypeError before any filesystem call.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, strategies as st

from instagram_mcp._path_guard import ensure_path


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_paths = st.one_of(
    st.text(min_size=0, max_size=128),
    st.binary(min_size=0, max_size=128),
    st.builds(pathlib.PurePosixPath, st.text(min_size=1, max_size=64)),
    st.builds(pathlib.PureWindowsPath, st.text(min_size=1, max_size=64).filter(lambda s: ":" not in s)),
    st.builds(pathlib.Path, st.text(min_size=1, max_size=64).filter(lambda s: not any(c in s for c in '\x00<>"|?*'))),
)

# Anything definitely NOT a path-like
invalid_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.lists(st.integers(), max_size=4),
    st.dictionaries(st.text(max_size=8), st.integers(), max_size=4),
    st.none(),
    st.tuples(st.integers(), st.integers()),
    st.builds(MagicMock),
    st.builds(object),
)

param_names = st.sampled_from([
    "accounts_dir", "media_cache_dir", "export_dir", "cookies_path",
    "instagram_mcp_cookies", "config_dir",
])


@given(value=valid_paths, name=param_names)
@settings(max_examples=200)
def test_ensure_path_accepts_path_like(value, name) -> None:
    """Property: ensure_path returns the value unchanged for str/bytes/PurePath."""
    result = ensure_path(value, name=name)
    assert result is value or result == value


@given(value=invalid_values, name=param_names)
@settings(max_examples=200)
def test_ensure_path_rejects_non_path(value, name) -> None:
    """Property: ensure_path raises TypeError for everything else, with name + type in message."""
    with pytest.raises(TypeError) as exc:
        ensure_path(value, name=name)
    msg = str(exc.value)
    assert name in msg, f"param name {name!r} not in error message: {msg!r}"
    assert type(value).__name__ in msg, (
        f"type {type(value).__name__!r} not in error message: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Regression cases — components reject MagicMock at the boundary
# ---------------------------------------------------------------------------

def test_account_pool_rejects_magicmock_dir() -> None:
    from instagram_mcp.account_pool import AccountPool

    with pytest.raises(TypeError, match="accounts_dir"):
        AccountPool(accounts_dir=MagicMock())


def test_media_cache_rejects_magicmock_dir() -> None:
    from instagram_mcp.media_cache import MediaCache

    with pytest.raises(TypeError, match="media_cache_dir"):
        MediaCache(cache_dir=MagicMock())


def test_json_exporter_rejects_magicmock_dir() -> None:
    from instagram_mcp.exporter import JsonExporter

    with pytest.raises(TypeError, match="export_dir"):
        JsonExporter(export_dir=MagicMock())
