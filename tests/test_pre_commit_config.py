"""Pre-commit config must wire the forbid-cookies hook.

Validates: Requirements 15.1, 15.4, 15.5.
"""
from __future__ import annotations

from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / ".pre-commit-config.yaml"


def test_config_exists() -> None:
    assert CONFIG.is_file(), ".pre-commit-config.yaml missing at repo root"


TEXT = CONFIG.read_text(encoding="utf-8") if CONFIG.is_file() else ""


def test_forbid_cookies_hook_referenced() -> None:
    assert "forbid-cookies" in TEXT, "pre-commit config missing forbid-cookies hook id"


def test_check_no_secrets_script_referenced() -> None:
    assert "scripts/check_no_secrets.py" in TEXT, (
        "pre-commit config does not call scripts/check_no_secrets.py"
    )


def test_gitleaks_hook_referenced() -> None:
    assert "gitleaks" in TEXT, "pre-commit config missing gitleaks hook"
