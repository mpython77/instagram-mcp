"""
Idempotency store with SQLite backend for deduplicating destructive tool calls.

When the kill switch (INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1) or the per-module
switch (INSTAGRAM_MCP_IDEMPOTENCY_DISABLED=1) is active, every method degrades
to a no-op: `get` returns None, `begin` returns True (proceed), and
`complete`/`fail`/`cleanup_expired` are silent no-ops.

Public API:
    IdempotencyEntry, IdempotencyStore, is_enabled
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

__all__ = [
    "IdempotencyEntry",
    "IdempotencyStore",
    "is_enabled",
]

logger = logging.getLogger(__name__)

# ── Kill switch ──────────────────────────────────────────────────────────────


def _kill_switch() -> bool:
    """Return True when idempotency is disabled via env vars."""
    obs_disabled = os.environ.get(
        "INSTAGRAM_MCP_OBSERVABILITY_DISABLED", ""
    ).lower() in ("1", "true")
    idem_disabled = os.environ.get(
        "INSTAGRAM_MCP_IDEMPOTENCY_DISABLED", ""
    ).lower() in ("1", "true")
    return obs_disabled or idem_disabled


# ── Public helper ────────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """Return True when the idempotency store is active."""
    return not _kill_switch()


# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class IdempotencyEntry:
    """A single idempotency record."""

    key: str
    tool: str
    status: Literal["in_progress", "completed", "error"]
    result_json: Optional[str]
    error_json: Optional[str]
    created_at: int  # epoch seconds
    expires_at: int


# ── SQL constants ────────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT PRIMARY KEY,
    tool        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'error')),
    result_json TEXT,
    error_json  TEXT,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
"""

_CREATE_INDEX_EXPIRES = (
    "CREATE INDEX IF NOT EXISTS idx_idempotency_expires "
    "ON idempotency_keys(expires_at);"
)

_CREATE_INDEX_TOOL = (
    "CREATE INDEX IF NOT EXISTS idx_idempotency_tool "
    "ON idempotency_keys(tool);"
)

# 50 MB disk cap for the idempotency database
_MAX_DB_SIZE_BYTES = 50 * 1024 * 1024


# ── IdempotencyStore ─────────────────────────────────────────────────────────


class IdempotencyStore:
    """SQLite-backed idempotency store with async CRUD and periodic cleanup."""

    def __init__(self, db_path: Path, ttl_seconds: int = 86400) -> None:
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._conn: Optional[sqlite3.Connection] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._disabled = _kill_switch()

        if not self._disabled:
            self._init_db()

    # ── Initialization ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create parent dir, open SQLite with WAL, create schema."""
        parent = self._db_path.parent

        # Create parent directory with restricted permissions on POSIX
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                os.chmod(parent, 0o700)

        # Open connection
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")

        # Create table and indexes
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX_EXPIRES)
        self._conn.execute(_CREATE_INDEX_TOOL)
        self._conn.commit()

        # Set file permissions on POSIX
        if sys.platform != "win32":
            try:
                os.chmod(self._db_path, 0o600)
            except OSError:
                pass

    # ── Public properties ────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """Return True when the store is active (not in no-op mode)."""
        return not self._disabled

    # ── Async CRUD methods ───────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[IdempotencyEntry]:
        """Retrieve an idempotency entry by key. Returns None if not found or disabled."""
        if self._disabled:
            return None
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> Optional[IdempotencyEntry]:
        """Synchronous get implementation."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT key, tool, status, result_json, error_json, created_at, expires_at "
            "FROM idempotency_keys WHERE key = ?",
            (key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return IdempotencyEntry(
            key=row[0],
            tool=row[1],
            status=row[2],
            result_json=row[3],
            error_json=row[4],
            created_at=row[5],
            expires_at=row[6],
        )

    async def begin(self, key: str, tool: str) -> bool:
        """
        Atomically insert a new in_progress entry.

        Returns True if newly inserted (caller should proceed with tool execution).
        Returns False if the key already exists (caller should call get() and react).
        In no-op mode, always returns True (proceed).
        """
        if self._disabled:
            return True
        return await asyncio.to_thread(self._begin_sync, key, tool)

    def _begin_sync(self, key: str, tool: str) -> bool:
        """Synchronous begin implementation using INSERT OR ABORT."""
        assert self._conn is not None
        now = int(time.time())
        try:
            self._conn.execute(
                "INSERT INTO idempotency_keys(key, tool, status, created_at, expires_at) "
                "VALUES (?, ?, 'in_progress', ?, ?)",
                (key, tool, now, now + self._ttl_seconds),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    async def complete(self, key: str, result_json: str) -> None:
        """Mark an entry as completed with the given result JSON."""
        if self._disabled:
            return
        await asyncio.to_thread(self._complete_sync, key, result_json)

    def _complete_sync(self, key: str, result_json: str) -> None:
        """Synchronous complete implementation."""
        assert self._conn is not None
        self._conn.execute(
            "UPDATE idempotency_keys SET status = 'completed', result_json = ? "
            "WHERE key = ?",
            (result_json, key),
        )
        self._conn.commit()

    async def fail(self, key: str, error_json: str) -> None:
        """Mark an entry as error with the given error JSON."""
        if self._disabled:
            return
        await asyncio.to_thread(self._fail_sync, key, error_json)

    def _fail_sync(self, key: str, error_json: str) -> None:
        """Synchronous fail implementation."""
        assert self._conn is not None
        self._conn.execute(
            "UPDATE idempotency_keys SET status = 'error', error_json = ? "
            "WHERE key = ?",
            (error_json, key),
        )
        self._conn.commit()

    async def cleanup_expired(self) -> int:
        """
        Delete expired entries and enforce 50MB disk cap.

        Returns the number of deleted rows.
        """
        if self._disabled:
            return 0
        return await asyncio.to_thread(self._cleanup_expired_sync)

    def _cleanup_expired_sync(self) -> int:
        """Synchronous cleanup implementation."""
        assert self._conn is not None
        now = int(time.time())

        # Delete expired entries
        cur = self._conn.execute(
            "DELETE FROM idempotency_keys WHERE expires_at < ?", (now,)
        )
        deleted = cur.rowcount
        self._conn.commit()

        # Enforce 50MB disk cap
        try:
            db_size = self._db_path.stat().st_size
            if db_size > _MAX_DB_SIZE_BYTES:
                # Delete oldest entries (by created_at) until under cap
                # Remove in batches of 100 to avoid holding lock too long
                while db_size > _MAX_DB_SIZE_BYTES:
                    batch_cur = self._conn.execute(
                        "DELETE FROM idempotency_keys WHERE rowid IN "
                        "(SELECT rowid FROM idempotency_keys ORDER BY created_at ASC LIMIT 100)"
                    )
                    batch_deleted = batch_cur.rowcount
                    deleted += batch_deleted
                    self._conn.commit()
                    if batch_deleted == 0:
                        break
                    db_size = self._db_path.stat().st_size
        except OSError:
            pass

        return deleted

    # ── Cleanup loop ─────────────────────────────────────────────────────────

    async def start_cleanup_loop(self) -> None:
        """Start the background cleanup loop (runs every 60 seconds)."""
        if self._disabled:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        """Background loop that invokes cleanup_expired every 60 seconds."""
        try:
            while True:
                await asyncio.sleep(60)
                try:
                    count = await self.cleanup_expired()
                    if count > 0:
                        logger.debug("Idempotency cleanup removed %d expired entries", count)
                except Exception:
                    logger.warning("Idempotency cleanup failed", exc_info=True)
        except asyncio.CancelledError:
            return

    async def stop_cleanup_loop(self) -> None:
        """Cancel the cleanup loop within 3 seconds."""
        if self._cleanup_task is None:
            return
        self._cleanup_task.cancel()
        try:
            await asyncio.wait_for(self._cleanup_task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        self._cleanup_task = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Stop the cleanup loop and close the SQLite connection."""
        await self.stop_cleanup_loop()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
