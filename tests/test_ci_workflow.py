"""CI workflow must include a gitleaks step.

Validates: Requirements 16.1, 16.3.
"""
from __future__ import annotations

from pathlib import Path


CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_exists() -> None:
    assert CI.is_file(), ".github/workflows/ci.yml missing"


def test_ci_includes_gitleaks_step() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "gitleaks" in text.lower(), "CI workflow does not run gitleaks"


def test_ci_runs_on_push_and_pull_request() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "push:" in text or "push" in text
    assert "pull_request" in text
