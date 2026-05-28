"""
Path argument guard.

This module provides :func:`ensure_path`, a defensive runtime check used
by every component that accepts a directory or file path argument and
hands it to the filesystem (e.g. ``AccountPool``, ``MediaCache``,
``JsonExporter``, and ``MCPConfig.from_env``).

Background:

    Earlier test runs accidentally constructed components with a
    :class:`unittest.mock.MagicMock` instead of a real path, which
    caused 2247 empty directories named after auto-generated mock IDs
    (``MagicMock/mock.accounts_dir/<id>/``) to be created on disk.

    :func:`ensure_path` blocks that mistake at the boundary by
    rejecting any value that is not a :class:`str`, :class:`bytes`,
    or :class:`pathlib.PurePath` instance, raising :class:`TypeError`
    *before* any filesystem call is attempted.

Examples:

    Happy path — supported types are returned unchanged::

        >>> ensure_path("/tmp/data", name="accounts_dir")
        '/tmp/data'
        >>> import pathlib
        >>> ensure_path(pathlib.Path("/tmp/data"), name="accounts_dir")
        PosixPath('/tmp/data')
        >>> ensure_path(b"/tmp/data", name="accounts_dir")
        b'/tmp/data'

    Rejection — any other type raises ``TypeError`` with the offending
    parameter name and the received type::

        >>> from unittest.mock import MagicMock
        >>> ensure_path(MagicMock(), name="accounts_dir")
        Traceback (most recent call last):
            ...
        TypeError: accounts_dir must be a str, bytes, or pathlib.PurePath, got MagicMock
"""

from __future__ import annotations

import pathlib
from typing import Union

__all__ = ["PathLike", "ensure_path"]

#: Type alias for values accepted by :func:`ensure_path`.
PathLike = Union[str, bytes, pathlib.PurePath]


def ensure_path(value: object, *, name: str) -> PathLike:
    """Validate that ``value`` is a path-like object.

    Args:
        value: The candidate path argument to validate.
        name: The parameter name to surface in the error message
            (e.g. ``"accounts_dir"``, ``"media_cache_dir"``,
            ``"export_dir"``). Required keyword-only argument.

    Returns:
        The original ``value`` unchanged when it is a :class:`str`,
        :class:`bytes`, or :class:`pathlib.PurePath` instance.

    Raises:
        TypeError: When ``value`` is not one of the supported path
            types. The message identifies the offending parameter name
            and the received type's class name.
    """
    if not isinstance(value, (str, bytes, pathlib.PurePath)):
        raise TypeError(
            f"{name} must be a str, bytes, or pathlib.PurePath, "
            f"got {type(value).__name__}"
        )
    return value
