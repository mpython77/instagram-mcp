"""Tests for PostScheduler."""

import asyncio
import json
import os
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from instagram_mcp.scheduler import PostScheduler, _ts_str


class TestTsStr:
    def test_valid_timestamp(self):
        result = _ts_str(1716000000)
        assert "2024" in result or "UTC" in result

    def test_zero(self):
        result = _ts_str(0)
        assert result  # doesn't crash


@pytest.mark.asyncio
async def test_scheduler_add_list_cancel(tmp_path):
    """Full add → list → cancel cycle using a temp directory."""
    # Create a real image file to satisfy the validator
    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header

    scheduler = PostScheduler(export_dir=str(tmp_path))

    future_ts = int(time.time()) + 3600  # 1 hour from now

    entry = await scheduler.add(
        images=[str(img_path)],
        caption="Test caption",
        publish_at=future_ts,
    )
    assert entry["id"]
    assert entry["status"] == "pending"
    assert entry["caption"] == "Test caption"

    # List should contain the entry
    pending = await scheduler.list_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == entry["id"]

    # Cancel
    removed = await scheduler.cancel(entry["id"])
    assert removed is True

    # List should be empty
    pending = await scheduler.list_pending()
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_scheduler_add_past_time(tmp_path):
    """Adding with publish_at in the past raises ValueError."""
    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"\xff\xd8\xff")

    scheduler = PostScheduler(export_dir=str(tmp_path))
    past_ts = int(time.time()) - 3600

    with pytest.raises(ValueError, match="future"):
        await scheduler.add(images=[str(img_path)], caption="", publish_at=past_ts)


@pytest.mark.asyncio
async def test_scheduler_add_missing_image(tmp_path):
    """Adding with non-existent image raises ValueError."""
    scheduler = PostScheduler(export_dir=str(tmp_path))
    future_ts = int(time.time()) + 3600

    with pytest.raises(ValueError, match="not found"):
        await scheduler.add(images=["/nonexistent/path/image.jpg"], caption="", publish_at=future_ts)


@pytest.mark.asyncio
async def test_scheduler_cancel_nonexistent(tmp_path):
    """Cancelling a non-existent ID returns False."""
    scheduler = PostScheduler(export_dir=str(tmp_path))
    removed = await scheduler.cancel("nonexistent_id")
    assert removed is False


def test_scheduler_stats(tmp_path):
    scheduler = PostScheduler(export_dir=str(tmp_path))
    stats = scheduler.stats()
    assert stats["running"] is False
    assert stats["pending_count"] == 0
    assert stats["published_count"] == 0


@pytest.mark.asyncio
async def test_scheduler_persistence(tmp_path):
    """Entries persist across PostScheduler instances (same export_dir)."""
    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"\xff\xd8\xff")

    future_ts = int(time.time()) + 7200
    scheduler1 = PostScheduler(export_dir=str(tmp_path))
    entry = await scheduler1.add(images=[str(img_path)], caption="Persist test", publish_at=future_ts)

    # New instance, same dir — should load existing data
    scheduler2 = PostScheduler(export_dir=str(tmp_path))
    pending = await scheduler2.list_pending()
    assert any(e["id"] == entry["id"] for e in pending)


@pytest.mark.asyncio
async def test_scheduler_publishes_due_posts(tmp_path):
    """Scheduler calls upload_fn for posts whose time has passed."""
    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"\xff\xd8\xff")

    published = []

    async def mock_upload(images, caption, location=""):
        published.append({"images": images, "caption": caption})

    scheduler = PostScheduler(export_dir=str(tmp_path), upload_fn=mock_upload)

    # Manually insert a past-due entry
    past_ts = int(time.time()) - 10
    data = scheduler._load()
    data["scheduled"].append({
        "id": "test_id",
        "images": [str(img_path)],
        "caption": "Due post",
        "location": "",
        "publish_at": past_ts,
        "publish_at_str": "past",
        "created_at": past_ts,
        "status": "pending",
    })
    scheduler._save(data)

    await scheduler._publish_due()

    assert len(published) == 1
    assert published[0]["caption"] == "Due post"

    # Entry should now be marked published
    data_after = scheduler._load()
    entry = next(e for e in data_after["scheduled"] if e["id"] == "test_id")
    assert entry["status"] == "published"


@pytest.mark.asyncio
async def test_publish_due_marks_publishing_before_upload(tmp_path):
    """Race-condition fix: entry must be 'publishing' before upload_fn is called."""
    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"\xff\xd8\xff")

    statuses_during_upload = []

    async def mock_upload(images, caption, location=""):
        # Read the schedule file while "inside" the upload to check atomicity
        data = scheduler._load()
        entry = next((e for e in data["scheduled"] if e["id"] == "race_id"), None)
        if entry:
            statuses_during_upload.append(entry["status"])

    scheduler = PostScheduler(export_dir=str(tmp_path), upload_fn=mock_upload)

    past_ts = int(time.time()) - 10
    data = scheduler._load()
    data["scheduled"].append({
        "id": "race_id",
        "images": [str(img_path)],
        "caption": "Race test",
        "location": "",
        "publish_at": past_ts,
        "publish_at_str": "past",
        "created_at": past_ts,
        "status": "pending",
    })
    scheduler._save(data)

    await scheduler._publish_due()

    # During upload the status must already be "publishing", not "pending"
    assert statuses_during_upload == ["publishing"]

    # After upload it must be "published"
    data_after = scheduler._load()
    entry = next(e for e in data_after["scheduled"] if e["id"] == "race_id")
    assert entry["status"] == "published"
