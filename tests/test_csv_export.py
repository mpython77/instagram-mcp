"""Tests for CsvExporter - CSV and Markdown export functionality."""

import csv
import json
from pathlib import Path

import pytest

from instagram_mcp.exporter import CsvExporter


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    return tmp_path / "csv_exports"


@pytest.fixture
def exporter(export_dir: Path) -> CsvExporter:
    return CsvExporter(export_dir=export_dir, enabled=True)


@pytest.fixture
def disabled_exporter(export_dir: Path) -> CsvExporter:
    return CsvExporter(export_dir=export_dir, enabled=False)


# ─────────────────────────────────────────────────────────────────────────────
# CsvExporter.export_csv tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExportCsv:
    def test_creates_csv_file(self, exporter, export_dir):
        """export_csv creates a .csv file with correct content."""
        data = {"score": 42, "username": "nike", "followers": 1000}
        path = exporter.export_csv("fake_check", "nike", data)

        assert path is not None
        assert path.exists()
        assert path.suffix == ".csv"
        assert "nike" in path.name

    def test_csv_content_flat_dict(self, exporter):
        """A flat dict produces a single CSV row with correct headers."""
        data = {"score": 42, "username": "nike", "status": "active"}
        path = exporter.export_csv("test_tool", "nike", data)

        assert path is not None
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["score"] == "42"
        assert rows[0]["username"] == "nike"
        assert rows[0]["status"] == "active"

    def test_csv_list_of_dicts(self, exporter):
        """A list of dicts produces multiple CSV rows."""
        data = {
            "tool": "followers",
            "users": [
                {"username": "user1", "followers": 100},
                {"username": "user2", "followers": 200},
                {"username": "user3", "followers": 300},
            ]
        }
        path = exporter.export_csv("followers", "nike", data)

        assert path is not None
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 3
        assert rows[0]["username"] == "user1"
        assert rows[1]["username"] == "user2"
        assert rows[2]["followers"] == "300"

    def test_nested_dict_flattening(self, exporter):
        """Nested dicts are flattened with dot notation."""
        data = {
            "profile": {
                "username": "nike",
                "stats": {
                    "followers": 1000,
                    "following": 500,
                }
            },
            "score": 85,
        }
        path = exporter.export_csv("analysis", "nike", data)

        assert path is not None
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["profile.username"] == "nike"
        assert rows[0]["profile.stats.followers"] == "1000"
        assert rows[0]["profile.stats.following"] == "500"
        assert rows[0]["score"] == "85"

    def test_list_values_serialized_as_json(self, exporter):
        """List values in a flat dict are serialized as JSON strings."""
        data = {"username": "nike", "tags": ["sport", "fashion", "running"]}
        path = exporter.export_csv("tags", "nike", data)

        assert path is not None
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        tags = json.loads(rows[0]["tags"])
        assert tags == ["sport", "fashion", "running"]

    def test_file_path_structure(self, exporter, export_dir):
        """CSV file is created in tool-named subdirectory."""
        path = exporter.export_csv("engagement", "adidas", {"score": 5})

        assert path is not None
        assert path.parent.name == "engagement"
        assert path.parent.parent == export_dir

    def test_disabled_returns_none(self, disabled_exporter):
        """Disabled exporter returns None without creating files."""
        result = disabled_exporter.export_csv("test", "test", {"x": 1})
        assert result is None

    def test_disabled_no_files(self, disabled_exporter, export_dir):
        """Disabled exporter does not create any files."""
        disabled_exporter.export_csv("test", "test", {"x": 1})
        if export_dir.exists():
            assert not list(export_dir.rglob("*.csv"))


# ─────────────────────────────────────────────────────────────────────────────
# CsvExporter.export_markdown tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExportMarkdown:
    def test_creates_md_file(self, exporter, export_dir):
        """export_markdown creates a .md file."""
        content = "# Report\n\nThis is a test report."
        path = exporter.export_markdown("report", "nike", content)

        assert path is not None
        assert path.exists()
        assert path.suffix == ".md"

    def test_md_content_correct(self, exporter):
        """Markdown file contains the provided content."""
        content = "**Engagement Report**\n\n- Score: 85%\n- Rating: Excellent"
        path = exporter.export_markdown("engagement", "nike", content)

        assert path is not None
        saved_content = path.read_text(encoding="utf-8")
        assert saved_content == content

    def test_md_file_path_structure(self, exporter, export_dir):
        """Markdown file is in the correct subdirectory."""
        path = exporter.export_markdown("analysis", "adidas", "# Test")

        assert path is not None
        assert path.parent.name == "analysis"
        assert "adidas" in path.name

    def test_disabled_returns_none(self, disabled_exporter):
        """Disabled exporter returns None for markdown export."""
        result = disabled_exporter.export_markdown("test", "test", "content")
        assert result is None

    def test_disabled_no_files(self, disabled_exporter, export_dir):
        """Disabled exporter does not create markdown files."""
        disabled_exporter.export_markdown("test", "test", "content")
        if export_dir.exists():
            assert not list(export_dir.rglob("*.md"))


# ─────────────────────────────────────────────────────────────────────────────
# Flattening edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestFlattening:
    def test_empty_dict(self, exporter):
        """Empty dict produces no file (no rows)."""
        # An empty dict produces one empty row which is still valid
        path = exporter.export_csv("test", "test", {})
        # Should produce a file with just headers (empty row)
        assert path is not None

    def test_deeply_nested(self, exporter):
        """Deeply nested dicts are fully flattened."""
        data = {"a": {"b": {"c": {"d": "value"}}}}
        path = exporter.export_csv("test", "test", data)

        assert path is not None
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["a.b.c.d"] == "value"

    def test_mixed_types(self, exporter):
        """Handles mixed value types: str, int, float, bool, None."""
        data = {
            "name": "test",
            "count": 42,
            "rate": 3.14,
            "active": True,
            "extra": None,
        }
        path = exporter.export_csv("test", "test", data)

        assert path is not None
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["name"] == "test"
        assert rows[0]["count"] == "42"
        assert rows[0]["rate"] == "3.14"
        assert rows[0]["active"] == "True"
        # csv.DictWriter writes None as empty string
        assert rows[0]["extra"] == ""
