"""SECURITY.md must contain the required sections, env-var policy, and external links.

Validates: Requirements 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7.
"""
from __future__ import annotations

from pathlib import Path

import pytest


SECURITY = Path(__file__).resolve().parents[1] / "SECURITY.md"


def test_file_exists() -> None:
    assert SECURITY.is_file(), "SECURITY.md missing at repo root"


TEXT = SECURITY.read_text(encoding="utf-8") if SECURITY.is_file() else ""


REQUIRED_SECTION_MARKERS = [
    "Reporting a Vulnerability",
    "Secret",            # "Secret Environment Variables" or similar
    "Recommended",       # "Recommended Cookie Storage"
    "If a Secret",       # "If a Secret Was Committed"
    "Pre-commit",        # "Pre-commit Secret Scan"
]


@pytest.mark.parametrize("marker", REQUIRED_SECTION_MARKERS)
def test_section_present(marker: str) -> None:
    assert marker.lower() in TEXT.lower(), f"SECURITY.md missing section marker: {marker!r}"


REQUIRED_ENV_NAMES = [
    "INSTAGRAM_MCP_COOKIES",
    "INSTAGRAM_MCP_COOKIES_<ALIAS>",
    "INSTAGRAM_MCP_OAUTH",
    "proxies.txt",
]


@pytest.mark.parametrize("name", REQUIRED_ENV_NAMES)
def test_env_name_listed(name: str) -> None:
    assert name in TEXT, f"SECURITY.md does not mention {name!r} as a secret"


REQUIRED_LINKS = [
    "rtyley.github.io/bfg-repo-cleaner",
    "github.com/newren/git-filter-repo",
]


@pytest.mark.parametrize("link", REQUIRED_LINKS)
def test_external_link_present(link: str) -> None:
    assert link in TEXT, f"SECURITY.md missing external link to {link!r}"
