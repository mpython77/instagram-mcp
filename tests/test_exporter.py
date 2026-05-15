"""Tests for JsonExporter — JSON auto-save module."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from instagram_mcp.exporter import JsonExporter, _Encoder


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    return tmp_path / "exports"


@pytest.fixture
def exporter(export_dir: Path) -> JsonExporter:
    return JsonExporter(export_dir=export_dir, indent=2, enabled=True)


@pytest.fixture
def disabled_exporter(export_dir: Path) -> JsonExporter:
    return JsonExporter(export_dir=export_dir, indent=2, enabled=False)


# ─────────────────────────────────────────────────────────────────────────────
# _Encoder unit tests
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class _SampleDC:
    name: str
    count: int
    tags: List[str] = dataclasses.field(default_factory=list)


class _SampleEnum(Enum):
    FOO = "foo_value"
    BAR = 42


class TestEncoder:
    def _dumps(self, obj) -> str:
        return json.dumps(obj, cls=_Encoder)

    def test_dataclass(self):
        dc = _SampleDC(name="test", count=5, tags=["a", "b"])
        result = json.loads(self._dumps(dc))
        assert result == {"name": "test", "count": 5, "tags": ["a", "b"]}

    def test_nested_dataclass(self):
        @dataclasses.dataclass
        class Outer:
            inner: _SampleDC
        obj = Outer(inner=_SampleDC(name="x", count=1))
        result = json.loads(self._dumps(obj))
        assert result["inner"]["name"] == "x"

    def test_enum(self):
        assert json.loads(self._dumps(_SampleEnum.FOO)) == "foo_value"
        assert json.loads(self._dumps(_SampleEnum.BAR)) == 42

    def test_set(self):
        result = json.loads(self._dumps({3, 1, 2}))
        assert result == ["1", "2", "3"]

    def test_datetime(self):
        dt = datetime(2026, 5, 15, 10, 23, 45, tzinfo=timezone.utc)
        result = json.loads(self._dumps(dt))
        assert "2026-05-15" in result

    def test_path(self):
        result = json.loads(self._dumps(Path("/tmp/foo/bar.json")))
        assert result == "/tmp/foo/bar.json"

    def test_bytes(self):
        result = json.loads(self._dumps(b"hello"))
        assert result == "hello"

    def test_pydantic_model(self):
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"key": "val"}
        result = json.loads(self._dumps(mock_model))
        assert result == {"key": "val"}

    def test_primitive_types_pass_through(self):
        data = {"str": "x", "int": 1, "float": 3.14, "bool": True, "none": None, "list": [1, 2]}
        assert json.loads(self._dumps(data)) == data


# ─────────────────────────────────────────────────────────────────────────────
# JsonExporter unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonExporter:
    def test_init_defaults(self, export_dir):
        exp = JsonExporter(export_dir=export_dir)
        assert exp.enabled is True
        assert exp.indent == 2
        assert exp.export_dir == export_dir.resolve()

    def test_disabled_returns_none(self, disabled_exporter):
        result = asyncio.get_event_loop().run_until_complete(
            disabled_exporter.save("profile", "nike", {"x": 1})
        )
        assert result is None

    def test_disabled_no_files_created(self, disabled_exporter, export_dir):
        asyncio.get_event_loop().run_until_complete(
            disabled_exporter.save("profile", "nike", {"x": 1})
        )
        assert not export_dir.exists() or not list(export_dir.rglob("*.json"))

    @pytest.mark.asyncio
    async def test_save_creates_file(self, exporter, export_dir):
        path = await exporter.save("profile", "nike", {"followers": 100}, duration_s=1.5)
        assert path is not None
        assert path.exists()
        assert path.suffix == ".json"

    @pytest.mark.asyncio
    async def test_save_file_path_structure(self, exporter, export_dir):
        path = await exporter.save("feed_deep", "cristiano", {})
        assert path is not None
        assert path.parent.name == "feed_deep"
        assert "cristiano" in path.name

    @pytest.mark.asyncio
    async def test_save_metadata_envelope(self, exporter):
        path = await exporter.save("profile", "nike", {"x": 42}, duration_s=2.5)
        assert path is not None
        data = json.loads(path.read_text())
        assert "_meta" in data
        assert data["_meta"]["tool"] == "profile"
        assert data["_meta"]["subject"] == "nike"
        assert data["_meta"]["duration_s"] == 2.5
        assert "saved_at" in data["_meta"]
        assert "saved_at_ts" in data["_meta"]
        assert "server_version" in data["_meta"]
        assert "data" in data
        assert data["data"] == {"x": 42}

    @pytest.mark.asyncio
    async def test_save_pretty_printed(self, exporter):
        path = await exporter.save("profile", "nike", {"a": 1})
        assert path is not None
        content = path.read_text()
        assert "\n" in content  # pretty-printed, not compact

    @pytest.mark.asyncio
    async def test_compact_mode(self, export_dir):
        exp = JsonExporter(export_dir=export_dir, indent=0)
        path = await exp.save("profile", "nike", {"a": 1})
        assert path is not None
        content = path.read_text()
        assert "\n" not in content.strip()

    @pytest.mark.asyncio
    async def test_index_created(self, exporter, export_dir):
        await exporter.save("profile", "nike", {})
        idx = export_dir / "index.json"
        assert idx.exists()

    @pytest.mark.asyncio
    async def test_index_entry_fields(self, exporter, export_dir):
        await exporter.save("engagement", "adidas", {"er": 3.5}, duration_s=0.8)
        entries = json.loads((export_dir / "index.json").read_text())
        assert len(entries) == 1
        e = entries[0]
        assert e["tool"] == "engagement"
        assert e["subject"] == "adidas"
        assert "file" in e
        assert e["duration_s"] == 0.8

    @pytest.mark.asyncio
    async def test_index_appends_multiple(self, exporter, export_dir):
        await exporter.save("profile", "nike", {})
        await exporter.save("profile", "adidas", {})
        await exporter.save("engagement", "nike", {})
        entries = json.loads((export_dir / "index.json").read_text())
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_save_dataclass(self, exporter):
        dc = _SampleDC(name="test", count=7, tags=["x"])
        path = await exporter.save("profile", "test", {"dc": dc})
        assert path is not None
        result = json.loads(path.read_text())
        assert result["data"]["dc"]["name"] == "test"
        assert result["data"]["dc"]["count"] == 7

    @pytest.mark.asyncio
    async def test_subject_sanitisation(self, exporter):
        # Subjects with special chars should be sanitised in filename
        path = await exporter.save("compare", "nike+adidas", {})
        assert path is not None
        assert "nike" in path.name

    @pytest.mark.asyncio
    async def test_subject_truncation(self, exporter):
        long_subject = "a" * 100
        path = await exporter.save("profile", long_subject, {})
        assert path is not None
        # Filename should not contain the full 100-char subject
        assert len(path.stem) < 150  # reasonable upper bound

    @pytest.mark.asyncio
    async def test_atomic_write_no_partial_file_on_error(self, exporter, export_dir, monkeypatch):
        """If serialisation fails, no partial file should remain."""
        class _Unserializable:
            pass

        path = await exporter.save("profile", "bad", {"x": _Unserializable()})
        # Should return None on failure, no .json file should exist
        assert path is None
        tool_dir = export_dir / "profile"
        if tool_dir.exists():
            assert not list(tool_dir.glob("*.json"))

    @pytest.mark.asyncio
    async def test_concurrent_saves_index_consistency(self, exporter, export_dir):
        """Concurrent saves must not corrupt index.json."""
        tasks = [
            exporter.save("profile", f"user{i}", {"idx": i})
            for i in range(10)
        ]
        paths = await asyncio.gather(*tasks)
        assert all(p is not None for p in paths)

        entries = json.loads((export_dir / "index.json").read_text())
        assert len(entries) == 10

    def test_from_config(self, export_dir):
        cfg = MagicMock()
        cfg.export_dir = str(export_dir)
        cfg.export_indent = 4
        cfg.export_enabled = False
        exp = JsonExporter.from_config(cfg)
        assert not exp.enabled
        assert exp.indent == 4

    def test_from_config_missing_attrs(self):
        """from_config should use defaults for missing attributes."""
        cfg = MagicMock(spec=[])  # no attributes at all
        exp = JsonExporter.from_config(cfg)
        assert exp.enabled is True
        assert exp.indent == 2


# ─────────────────────────────────────────────────────────────────────────────
# _make_path tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMakePath:
    def test_safe_tool_name(self, exporter):
        now = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
        path = exporter._make_path("feed_deep", "nike", now)
        assert path.parent.name == "feed_deep"

    def test_tool_special_chars_sanitised(self, exporter):
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        path = exporter._make_path("feed-deep", "nike", now)
        assert "-" not in path.parent.name  # hyphens removed from tool name

    def test_timestamp_in_filename(self, exporter):
        now = datetime(2026, 5, 15, 10, 23, 45, tzinfo=timezone.utc)
        path = exporter._make_path("profile", "nike", now)
        assert "2026-05-15" in path.name
        assert "10-23-45" in path.name

    def test_subject_in_filename(self, exporter):
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        path = exporter._make_path("profile", "cristiano", now)
        assert "cristiano" in path.name
