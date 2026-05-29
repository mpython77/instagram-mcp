"""
Post Scheduler — local JSON-backed scheduled post queue.

Stores scheduled posts in <export_dir>/schedule.json.
A background task (started in lifespan) checks every 60s and publishes due posts.

Usage via MCP tool instagram_schedule:
    action="list"       → view upcoming scheduled posts
    action="add"        → schedule a new post (image paths + caption + publish_at)
    action="cancel"     → remove a scheduled entry by ID
    action="status"     → show scheduler health
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("instagram_mcp.scheduler")

_SCHEDULE_FILE = "schedule.json"


class PostScheduler:
    """
    Manages a local queue of scheduled Instagram posts.

    Posts are persisted in <export_dir>/schedule.json.
    The upload_fn callback is called when a post is due — it should
    match the signature of the existing upload logic.
    """

    def __init__(
        self,
        export_dir: str,
        upload_fn: Optional[Callable[..., Any]] = None,
        check_interval: int = 60,
    ) -> None:
        self._export_dir = Path(export_dir)
        self._upload_fn = upload_fn
        self._check_interval = check_interval
        self._schedule_file = self._export_dir / _SCHEDULE_FILE
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._published_count = 0
        self._last_check: float = 0.0

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background scheduler loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._loop())
            logger.info("PostScheduler started (interval=%ds)", self._check_interval)

    async def stop(self) -> None:
        """Stop the background loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.info("PostScheduler stopped (%d published)", self._published_count)

    # ── Public API ───────────────────────────────────────────────────────────

    async def add(
        self,
        images: List[str],
        caption: str,
        publish_at: int,
        location: str = "",
    ) -> Dict[str, Any]:
        """
        Add a post to the schedule queue.

        Args:
            images: list of absolute file paths
            caption: post caption (max 2200 chars)
            publish_at: Unix timestamp when to publish
            location: optional location string

        Returns the new schedule entry.
        """
        now = int(time.time())
        if publish_at <= now:
            raise ValueError(
                f"publish_at must be in the future (got {publish_at}, now is {now})"
            )
        if not images:
            raise ValueError("At least one image path is required.")
        for p in images:
            if not Path(p).exists():
                raise ValueError(f"Image file not found: {p!r}")

        entry: Dict[str, Any] = {
            "id": str(uuid.uuid4())[:8],
            "images": images,
            "caption": caption[:2200],
            "location": location,
            "publish_at": publish_at,
            "publish_at_str": _ts_str(publish_at),
            "created_at": now,
            "status": "pending",
        }

        async with self._lock:
            data = self._load()
            data["scheduled"].append(entry)
            self._save(data)

        logger.info(
            "Scheduled post %s for %s (%d images)",
            entry["id"], entry["publish_at_str"], len(images),
        )
        return entry

    async def cancel(self, post_id: str) -> bool:
        """Remove a scheduled entry by ID. Returns True if found and removed."""
        async with self._lock:
            data = self._load()
            before = len(data["scheduled"])
            data["scheduled"] = [e for e in data["scheduled"] if e["id"] != post_id]
            removed = len(data["scheduled"]) < before
            if removed:
                self._save(data)
        return removed

    async def list_pending(self) -> List[Dict[str, Any]]:
        """Return all pending (not yet published) scheduled entries."""
        data = self._load()
        return [e for e in data["scheduled"] if e.get("status") == "pending"]

    def stats(self) -> Dict[str, Any]:
        """Return scheduler health stats."""
        data = self._load()
        pending = [e for e in data["scheduled"] if e.get("status") == "pending"]
        return {
            "running": self._task is not None and not self._task.done(),
            "pending_count": len(pending),
            "published_count": self._published_count,
            "check_interval_seconds": self._check_interval,
            "last_check_at": _ts_str(int(self._last_check)) if self._last_check else "never",
            "schedule_file": str(self._schedule_file),
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Background loop: check every check_interval seconds."""
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                self._last_check = time.time()
                await self._publish_due()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Scheduler loop error: %s", exc)

    async def _publish_due(self) -> None:
        """Find and publish posts whose publish_at has passed."""
        now = int(time.time())
        async with self._lock:
            data = self._load()
            due = [e for e in data["scheduled"] if e.get("status") == "pending" and e.get("publish_at", 0) <= now]
            # Mark as "publishing" atomically before releasing the lock so a
            # concurrent or restarted loop can't pick up the same entries again.
            for e in due:
                e["status"] = "publishing"
            if due:
                self._save(data)

        for entry in due:
            logger.info("Publishing scheduled post %s (due %s)", entry["id"], entry["publish_at_str"])
            try:
                if self._upload_fn is not None:
                    await self._upload_fn(
                        images=entry["images"],
                        caption=entry.get("caption", ""),
                        location=entry.get("location", ""),
                    )
                status = "published"
                logger.info("Scheduled post %s published successfully", entry["id"])
                self._published_count += 1
            except Exception as exc:
                status = "failed"
                logger.error("Scheduled post %s failed: %s", entry["id"], exc)

            async with self._lock:
                data = self._load()
                for e in data["scheduled"]:
                    if e["id"] == entry["id"]:
                        e["status"] = status
                        e["published_at"] = int(time.time()) if status == "published" else None
                self._save(data)

    def _load(self) -> Dict[str, Any]:
        """Load schedule from disk. Returns empty structure if file missing."""
        try:
            self._export_dir.mkdir(parents=True, exist_ok=True)
            if self._schedule_file.exists():
                return json.loads(self._schedule_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load schedule.json: %s", exc)
        return {"scheduled": [], "version": 1}

    def _save(self, data: Dict[str, Any]) -> None:
        """Save schedule to disk atomically."""
        try:
            self._export_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._schedule_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._schedule_file)
        except Exception as exc:
            logger.error("Failed to save schedule.json: %s", exc)


def _ts_str(ts: int) -> str:
    """Convert Unix timestamp to human-readable UTC string."""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)
