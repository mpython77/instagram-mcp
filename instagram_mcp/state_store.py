"""
Persistent state store with periodic flush, schema versioning, and degraded fallback.

Persists circuit breaker, rate limiter, and account pool state to SQLite so that
restarts do not erase what the server has learned about its environment.

When the kill switch (INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1 or
INSTAGRAM_MCP_STATE_DISABLED=1) is active, every public method becomes a no-op:
`load` returns an empty StateSnapshot, `save` is a no-op, and the flush loop
never starts.

Public API:
    ProxySnapshot, RateLimiterSnapshot, AccountSnapshot, StateSnapshot,
    CURRENT_SCHEMA_VERSION, StateStore
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

__all__ = [
    "ProxySnapshot",
    "RateLimiterSnapshot",
    "AccountSnapshot",
    "StateSnapshot",
    "CURRENT_SCHEMA_VERSION",
    "StateStore",
]

logger = logging.getLogger(__name__)

# ── Schema version ───────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1

# ── Kill switch ──────────────────────────────────────────────────────────────


def _kill_switch() -> bool:
    """Return True when the state store is globally disabled."""
    obs = os.environ.get("INSTAGRAM_MCP_OBSERVABILITY_DISABLED", "").lower() in ("1", "true")
    state = os.environ.get("INSTAGRAM_MCP_STATE_DISABLED", "").lower() in ("1", "true")
    return obs or state


# ── Snapshot dataclasses ─────────────────────────────────────────────────────


@dataclass
class ProxySnapshot:
    """Per-proxy circuit breaker state."""

    proxy_url: str
    cb_state: Literal["closed", "open", "half_open"]
    cb_until_epoch: int
    consecutive_failures: int
    total_requests: int
    total_failures: int


@dataclass
class RateLimiterSnapshot:
    """Rate limiter state for a given scope."""

    scope: str  # "global" or proxy URL
    current_rps: float
    max_rate: float
    consecutive_429s: int
    consecutive_successes: int


@dataclass
class AccountSnapshot:
    """Per-account pool member state."""

    alias: str
    status: Literal["active", "rate_limited", "checkpoint", "expired"]
    cooldown_until_epoch: int
    consecutive_failures: int


@dataclass
class StateSnapshot:
    """Complete runtime state snapshot."""

    proxies: list[ProxySnapshot] = field(default_factory=list)
    rate_limiters: list[RateLimiterSnapshot] = field(default_factory=list)
    accounts: list[AccountSnapshot] = field(default_factory=list)
    schema_version: int = CURRENT_SCHEMA_VERSION


# ── Forward migrations registry ──────────────────────────────────────────────

# Each migration takes a sqlite3.Connection and upgrades from version N-1 to N.
# Version 1 is the initial schema (created in _init_db), so migrations start at 2.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


# ── StateStore class ─────────────────────────────────────────────────────────


class StateStore:
    """SQLite-backed persistent state store with periodic flush."""

    def __init__(self, db_path: Path, flush_interval_seconds: int = 30) -> None:
        self._db_path = db_path
        self._flush_interval = flush_interval_seconds
        self._conn: sqlite3.Connection | None = None
        self._degraded = False
        self._disabled = _kill_switch()
        self._flush_task: asyncio.Task | None = None

        if self._disabled:
            return

        try:
            self._init_db()
        except Exception as exc:
            logger.warning(
                "State store entering degraded mode: %s: %s",
                type(exc).__name__,
                exc,
            )
            self._degraded = True
            # Ensure any partially-opened connection is closed
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None

    # ── Initialization ────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create/open the SQLite database and initialize schema."""
        # Create parent directory with restricted permissions on POSIX
        parent = self._db_path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                os.chmod(parent, 0o700)

        # Open connection with WAL mode and manual transaction control
        self._conn = sqlite3.connect(
            str(self._db_path), timeout=5.0, isolation_level=None,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")

        # Create tables
        self._conn.execute("BEGIN")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '1')"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxies (
                proxy_url            TEXT PRIMARY KEY,
                cb_state             TEXT NOT NULL,
                cb_until_epoch       INTEGER NOT NULL,
                consecutive_failures INTEGER NOT NULL,
                total_requests       INTEGER NOT NULL,
                total_failures       INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limiters (
                scope                  TEXT PRIMARY KEY,
                current_rps            REAL NOT NULL,
                max_rate               REAL NOT NULL,
                consecutive_429s       INTEGER NOT NULL,
                consecutive_successes  INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                alias                  TEXT PRIMARY KEY,
                status                 TEXT NOT NULL,
                cooldown_until_epoch   INTEGER NOT NULL,
                consecutive_failures   INTEGER NOT NULL
            )
            """
        )
        self._conn.execute("COMMIT")

        # Set file permissions on POSIX
        if sys.platform != "win32":
            os.chmod(self._db_path, 0o600)

        # Schema version check
        self._check_schema_version()

    def _check_schema_version(self) -> None:
        """Verify schema version and run migrations if needed."""
        assert self._conn is not None
        cursor = self._conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        )
        row = cursor.fetchone()
        if row is None:
            # Should not happen after INSERT OR IGNORE, but handle gracefully
            self._conn.execute(
                "INSERT INTO metadata(key, value) VALUES ('schema_version', '1')"
            )
            self._conn.commit()
            return

        db_version = int(row[0])

        if db_version > CURRENT_SCHEMA_VERSION:
            # Future version — cannot downgrade
            logger.error(
                "State store schema version %d is newer than current %d; "
                "entering degraded mode",
                db_version,
                CURRENT_SCHEMA_VERSION,
            )
            self._degraded = True
            self._conn.close()
            self._conn = None
            return

        if db_version < CURRENT_SCHEMA_VERSION:
            # Run forward migrations
            for version in range(db_version + 1, CURRENT_SCHEMA_VERSION + 1):
                migration = _MIGRATIONS.get(version)
                if migration is not None:
                    migration(self._conn)
            # Update stored version
            self._conn.execute(
                "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
                (str(CURRENT_SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ── Public status methods ─────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """Return True when the state store is active (not disabled or degraded)."""
        return not self._disabled and not self._degraded

    def is_writable(self) -> bool:
        """Check if the state DB file is writable."""
        if self._disabled or self._degraded:
            return False
        try:
            return os.access(self._db_path, os.W_OK)
        except (OSError, ValueError):
            return False

    # ── Async load ────────────────────────────────────────────────────────

    async def load(self) -> StateSnapshot:
        """Read all tables and return a StateSnapshot. Empty in degraded/disabled mode."""
        if self._disabled or self._degraded:
            return StateSnapshot()
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> StateSnapshot:
        """Synchronous load implementation."""
        if self._conn is None:
            return StateSnapshot()

        proxies: list[ProxySnapshot] = []
        cursor = self._conn.execute(
            "SELECT proxy_url, cb_state, cb_until_epoch, "
            "consecutive_failures, total_requests, total_failures FROM proxies"
        )
        for row in cursor.fetchall():
            proxies.append(
                ProxySnapshot(
                    proxy_url=row[0],
                    cb_state=row[1],
                    cb_until_epoch=row[2],
                    consecutive_failures=row[3],
                    total_requests=row[4],
                    total_failures=row[5],
                )
            )

        rate_limiters: list[RateLimiterSnapshot] = []
        cursor = self._conn.execute(
            "SELECT scope, current_rps, max_rate, "
            "consecutive_429s, consecutive_successes FROM rate_limiters"
        )
        for row in cursor.fetchall():
            rate_limiters.append(
                RateLimiterSnapshot(
                    scope=row[0],
                    current_rps=row[1],
                    max_rate=row[2],
                    consecutive_429s=row[3],
                    consecutive_successes=row[4],
                )
            )

        accounts: list[AccountSnapshot] = []
        cursor = self._conn.execute(
            "SELECT alias, status, cooldown_until_epoch, "
            "consecutive_failures FROM accounts"
        )
        for row in cursor.fetchall():
            accounts.append(
                AccountSnapshot(
                    alias=row[0],
                    status=row[1],
                    cooldown_until_epoch=row[2],
                    consecutive_failures=row[3],
                )
            )

        return StateSnapshot(
            proxies=proxies,
            rate_limiters=rate_limiters,
            accounts=accounts,
            schema_version=CURRENT_SCHEMA_VERSION,
        )

    # ── Async save ────────────────────────────────────────────────────────

    async def save(self, snapshot: StateSnapshot) -> None:
        """Persist snapshot to SQLite. No-op in degraded/disabled mode."""
        if self._disabled or self._degraded:
            return
        await asyncio.to_thread(self._save_sync, snapshot)

    def _save_sync(self, snapshot: StateSnapshot) -> None:
        """Synchronous save implementation with transaction."""
        if self._conn is None:
            return

        cursor = self._conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")

            # Clear and rewrite proxies
            cursor.execute("DELETE FROM proxies")
            for p in snapshot.proxies:
                cursor.execute(
                    "INSERT OR REPLACE INTO proxies "
                    "(proxy_url, cb_state, cb_until_epoch, "
                    "consecutive_failures, total_requests, total_failures) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        p.proxy_url,
                        p.cb_state,
                        p.cb_until_epoch,
                        p.consecutive_failures,
                        p.total_requests,
                        p.total_failures,
                    ),
                )

            # Clear and rewrite rate_limiters
            cursor.execute("DELETE FROM rate_limiters")
            for rl in snapshot.rate_limiters:
                cursor.execute(
                    "INSERT OR REPLACE INTO rate_limiters "
                    "(scope, current_rps, max_rate, "
                    "consecutive_429s, consecutive_successes) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        rl.scope,
                        rl.current_rps,
                        rl.max_rate,
                        rl.consecutive_429s,
                        rl.consecutive_successes,
                    ),
                )

            # Clear and rewrite accounts
            cursor.execute("DELETE FROM accounts")
            for a in snapshot.accounts:
                cursor.execute(
                    "INSERT OR REPLACE INTO accounts "
                    "(alias, status, cooldown_until_epoch, consecutive_failures) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        a.alias,
                        a.status,
                        a.cooldown_until_epoch,
                        a.consecutive_failures,
                    ),
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        # Enforce 100MB disk cap
        self._enforce_size_cap()

    def _enforce_size_cap(self) -> None:
        """Run VACUUM if DB exceeds 100MB."""
        try:
            size = self._db_path.stat().st_size
            if size > 100 * 1024 * 1024:  # 100MB
                self._conn.execute("VACUUM")  # type: ignore[union-attr]
                new_size = self._db_path.stat().st_size
                if new_size > 100 * 1024 * 1024:
                    logger.warning(
                        "State store size %d bytes exceeds 100MB cap even after VACUUM",
                        new_size,
                    )
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Failed to check/enforce state store size cap: %s", exc)

    # ── Flush loop ────────────────────────────────────────────────────────

    async def start_flush_loop(
        self, snapshot_provider: Callable[[], StateSnapshot]
    ) -> None:
        """Start periodic save loop. No-op when disabled."""
        if self._disabled or self._degraded:
            return
        self._flush_task = asyncio.create_task(
            self._flush_loop(snapshot_provider)
        )

    async def _flush_loop(self, snapshot_provider: Callable[[], StateSnapshot]) -> None:
        """Periodically save snapshots."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                snapshot = snapshot_provider()
                await self.save(snapshot)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "State store flush failed, will retry next interval: %s: %s",
                    type(exc).__name__,
                    exc,
                )

    async def stop_flush_loop(self) -> None:
        """Cancel the flush loop task."""
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

    # ── Close ─────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
