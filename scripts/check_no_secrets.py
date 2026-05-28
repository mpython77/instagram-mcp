#!/usr/bin/env python3
"""Block commits that touch known cookie / secret-shaped files.

CLI usage:
    python scripts/check_no_secrets.py <path> [<path> ...]

The script is invoked by the ``forbid-cookies`` pre-commit hook with the
list of staged file paths. Each path is matched against a fixed blocklist
of patterns (cookie files, ``.env`` files, ``secrets.*`` files). If any
staged path matches, the offending path is written verbatim to ``stderr``
and the script exits with status ``1``; otherwise it exits with status
``0``.

The implementation depends only on the Python standard library so it can
run without internet access.
"""
from __future__ import annotations

import fnmatch
import sys
from pathlib import PurePosixPath

# Blocklist patterns. Order is preserved for deterministic stderr output.
BLOCKLIST_PATTERNS: tuple[str, ...] = (
    "cookie.txt",
    "cookies.json",
    "cookies.txt",
    "*.env",
    "secrets.*",
    "**/cookies.json",
    "**/cookies.txt",
)


def _is_blocked(path: str) -> bool:
    """Return True if ``path`` matches any blocklist pattern.

    The path is checked both by its basename (``fnmatch.fnmatch`` against
    each pattern) and by its full normalized form (``PurePath.match``).
    POSIX-style paths are used for matching so the same patterns work on
    Windows and *nix.
    """
    # Normalize to POSIX separators so glob-style ``**`` patterns work
    # the same way on Windows checkouts.
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    basename = pure.name

    for pattern in BLOCKLIST_PATTERNS:
        # Basename match catches plain filename patterns like
        # ``cookies.json``, ``*.env``, ``secrets.*``.
        if fnmatch.fnmatch(basename, pattern):
            return True
        # Full-path match catches recursive patterns like
        # ``**/cookies.json`` and any pattern that includes a separator.
        try:
            if pure.match(pattern):
                return True
        except ValueError:
            # ``PurePath.match`` raises ValueError on empty patterns; treat
            # such patterns as non-matching rather than aborting the scan.
            continue
        # Fall back to fnmatch on the full normalized path for ``**``-style
        # patterns, which ``PurePath.match`` does not support natively.
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """Entry point. ``argv`` defaults to ``sys.argv[1:]``."""
    paths = list(argv if argv is not None else sys.argv[1:])
    offending: list[str] = []
    for path in paths:
        if _is_blocked(path):
            print(path, file=sys.stderr)
            offending.append(path)
    return 1 if offending else 0


if __name__ == "__main__":
    sys.exit(main())
