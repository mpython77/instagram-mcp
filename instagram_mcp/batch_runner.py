"""
Batch scraper for instagram_mcp.

Features:
  - **Queue-based worker pool** (producer / N workers / writer)
    -> bounded memory (no `create_task(2000)` blowup)
  - Resume support (progress file tracks completed usernames)
  - Graceful Ctrl+C shutdown (saves progress before exit)
  - Real-time save every BATCH_SAVE_EVERY profiles
  - JSONL streaming **on by default** — memory-safe for huge batches
  - Auto-disable final aggregated JSON when batch is large (>500)
  - Date range filtering (since/until timestamps)
  - Cookie support
  - Detailed stats tracking
  - Optional async file IO via aiofiles (falls back to sync writes)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from .config import MCPConfig
from .formatter import format_feed_tags_json, format_profile_json
from .models import FeedTagResult
from .parser import (
    check_dead_account_from_items,
    parse_feed_items,
    parse_profile,
)

# Optional async file IO. Falls back to a thread-pool wrapper when missing.
try:  # pragma: no cover - import-time branch
    import aiofiles  # type: ignore
    _HAS_AIOFILES = True
except Exception:  # pragma: no cover
    aiofiles = None  # type: ignore
    _HAS_AIOFILES = False

logger = logging.getLogger("instagram_mcp.batch")

# Single shared parse-time config — no env reads, used purely for parser hints
_PARSE_CFG = MCPConfig()

# Threshold above which we skip writing the aggregated JSON
# (JSONL stream is preferred for memory safety on huge batches).
_AGGREGATE_JSON_AUTO_DISABLE = 500

# Internal sentinel for queue shutdown
_SENTINEL: Any = object()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date(date_str: str) -> Optional[int]:
    """Parse DD.MM.YYYY to Unix timestamp. Returns None if empty."""
    if not date_str:
        return None
    try:
        from datetime import timezone as _tz
        return int(datetime.strptime(date_str.strip().replace(",", "."), "%d.%m.%Y").replace(tzinfo=_tz.utc).timestamp())
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BatchConfig:
    """Configuration for a batch scrape run."""

    targets_file: str
    output_file: str
    progress_file: str = ""          # auto-derived if empty
    max_workers: int = 20
    max_retries: int = 5
    retry_base_delay: float = 0.5    # base seconds for exponential retry back-off
    since_timestamp: Optional[int] = None
    until_timestamp: Optional[int] = None
    since_date: str = ""             # "DD.MM.YYYY" convenience — converts to since_timestamp
    until_date: str = ""             # "DD.MM.YYYY" convenience
    use_cookies: bool = False
    max_posts: int = 1000
    max_age_days: int = 365
    save_every: int = 20             # save after every N completions
    proxy_url: str = ""
    profile_only: bool = False       # skip feed fetch — 30-60x faster for bulk metadata only
    stream_jsonl: bool = True        # stream JSONL by default (memory-safe)
    write_aggregate_json: bool = True  # auto-disabled if pending > _AGGREGATE_JSON_AUTO_DISABLE
    fail_fast_threshold: float = 0.0 # if > 0, stop when error_rate exceeds this (e.g. 0.5 = 50%)
    fail_fast_min_samples: int = 50  # only enforce fail_fast after this many completions

    def __post_init__(self) -> None:
        # Derive timestamps from convenience date strings
        if self.since_date and self.since_timestamp is None:
            self.since_timestamp = _parse_date(self.since_date)
        if self.until_date and self.until_timestamp is None:
            self.until_timestamp = _parse_date(self.until_date)

        # Auto-derive progress file path if not set
        if not self.progress_file:
            base, _ext = os.path.splitext(self.output_file)
            self.progress_file = base + ".progress.json"


@dataclass
class BatchStats:
    """Counters for a batch run."""

    total: int = 0
    completed: int = 0
    active: int = 0
    not_found: int = 0
    private: int = 0
    dead: int = 0
    error: int = 0
    elapsed_seconds: float = 0.0

    @property
    def rate(self) -> float:
        """Completions per second."""
        return self.completed / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

class BatchRunner:
    """
    Production-grade batch Instagram scraper using a Queue-based worker pool.

    Architecture:
        producer -> input_q (bounded) -> [N workers] -> output_q -> writer

    Memory footprint stays roughly O(max_workers) — independent of the number
    of targets. Suitable for 2000+ profile batches without OOM risk.

    Usage:
        config = BatchConfig(targets_file="users.txt", output_file="results.json")
        runner = BatchRunner(config, instagram_client)
        stats = await runner.run()

    progress_cb: optional async callable(completed, total, message) — called
                 after each periodic save so MCP tools can forward updates to the AI.
    """

    def __init__(self, config: BatchConfig, instagram_client: Any, progress_cb=None) -> None:
        self._config = config
        self._client = instagram_client
        self._progress_cb = progress_cb
        self._stats = BatchStats()
        self._results: Dict[str, Any] = {}        # username → result dict (cleared in low-mem mode)
        self._completed: Set[str] = set()         # already-done usernames (lowercase)
        self._stop_flag = False
        self._lock = asyncio.Lock()
        self._started_at: str = ""
        self._jsonl_fh = None                     # optional JSONL stream handle (sync fallback)
        self._jsonl_afh = None                    # optional async JSONL handle (aiofiles)
        self._fail_fast_triggered = False
        # `_low_memory_mode` is set True when batch is large -> avoid keeping
        # all parsed results in `self._results` (JSONL stream is the truth).
        self._low_memory_mode = False

    # ── Progress reporting ───────────────────────────────────────────────────

    async def _emit_progress(self, prefix: str = "") -> None:
        """Send a structured progress update to the MCP context (if set)."""
        if self._progress_cb is None:
            return
        s = self._stats
        pct = s.completed / s.total * 100 if s.total else 0
        remaining = s.total - s.completed
        eta = f"{remaining / s.rate:.0f}s" if s.rate > 0 else "?"
        msg = (
            f"{prefix}[{s.completed}/{s.total}] {pct:.0f}% | "
            f"✅ {s.active} active  💀 {s.dead} dead  "
            f"🔒 {s.private} private  ❌ {s.not_found} not_found  "
            f"⚠️ {s.error} error  | "
            f"{s.rate:.1f} profiles/s  ETA {eta}"
        )
        try:
            coro = self._progress_cb(s.completed, s.total, msg)
            if asyncio.iscoroutine(coro):
                await coro
        except Exception as exc:
            logger.debug("progress_cb failed: %s", exc)

    # ── Public entry point ───────────────────────────────────────────────────

    async def run(self) -> BatchStats:
        """Main entry point. Returns stats when done."""
        cfg = self._config
        self._started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Install signal handler for graceful Ctrl+C, but only when running
        # standalone (i.e. not under an MCP host that already owns SIGINT).
        # `add_signal_handler` will raise NotImplementedError on Windows; ignore.
        loop = asyncio.get_running_loop()
        prior_handler = None
        try:
            prior_handler = signal.getsignal(signal.SIGINT)
            # Only install if no custom handler is in place (default = SIG_DFL)
            if prior_handler in (signal.SIG_DFL, signal.SIG_IGN, None):
                loop.add_signal_handler(signal.SIGINT, self._handle_shutdown)
        except (NotImplementedError, ValueError, RuntimeError):
            # Not available on this platform / not in main thread → skip
            prior_handler = None

        # Load targets + prior progress
        targets = self._load_targets()
        self._completed = self._load_progress()

        # Load previously saved results (for resume).
        # In low-memory mode we may skip keeping this in RAM (JSONL has truth).
        self._results = self._load_existing_results()

        pending = [u for u in targets if u.lower() not in self._completed]

        self._stats.total = len(targets)
        self._stats.completed = len(self._completed)

        # Memory-safety: if the pending batch is large, disable aggregate JSON.
        if cfg.write_aggregate_json and len(pending) > _AGGREGATE_JSON_AUTO_DISABLE:
            cfg.write_aggregate_json = False
            self._low_memory_mode = True
            # Drop the in-memory map; JSONL is the source of truth for big runs.
            self._results = {}
            logger.info(
                "Low-memory mode: pending=%d > %d → aggregate JSON disabled (JSONL only).",
                len(pending),
                _AGGREGATE_JSON_AUTO_DISABLE,
            )

        logger.info(
            "Batch start | total=%d pending=%d already_done=%d | output=%s | workers=%d | mode=%s",
            len(targets),
            len(pending),
            len(self._completed),
            cfg.output_file,
            cfg.max_workers,
            "low-memory/jsonl" if self._low_memory_mode else "aggregate+jsonl",
        )

        start_time = time.monotonic()

        # ── Open JSONL streaming handle ──────────────────────────────────────
        # In low-memory mode the JSONL is mandatory (it's the only output).
        await self._open_jsonl_stream(force=self._low_memory_mode)

        # Emit start notification
        if self._progress_cb is not None:
            mode = "profile_only" if cfg.profile_only else "full"
            start_msg = (
                f"🚀 Batch started — {len(pending)} profiles to scrape "
                f"({len(self._completed)} already done), {cfg.max_workers} workers, mode={mode}"
            )
            try:
                coro = self._progress_cb(self._stats.completed, self._stats.total, start_msg)
                if asyncio.iscoroutine(coro):
                    await coro
            except Exception:
                pass

        # ── Build bounded queues + spawn producer / workers / writer ─────────
        # `maxsize` provides natural back-pressure so the producer doesn't
        # buffer all 2000 usernames in memory if workers fall behind.
        input_q: asyncio.Queue = asyncio.Queue(maxsize=max(cfg.max_workers * 3, 8))
        output_q: asyncio.Queue = asyncio.Queue(maxsize=max(cfg.max_workers * 5, 16))

        producer_task = asyncio.create_task(
            self._producer(pending, input_q, cfg.max_workers), name="batch.producer"
        )
        worker_tasks: List[asyncio.Task] = [
            asyncio.create_task(self._worker(input_q, output_q), name=f"batch.worker.{i}")
            for i in range(cfg.max_workers)
        ]
        writer_task = asyncio.create_task(
            self._writer(output_q, start_time), name="batch.writer"
        )

        # Use plain gather for Python 3.10 backwards compatibility
        # (TaskGroup is 3.11+; project targets >=3.10). gather + manual cancel
        # cleanup gives us identical structured-concurrency semantics here.
        try:
            await producer_task
            # Once producer is done, sentinels are in the queue → workers will
            # drain and exit naturally.
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            # All workers done → push writer sentinel so it stops.
            await output_q.put(_SENTINEL)
            await writer_task
        except asyncio.CancelledError:
            # Propagated from outer cancellation (e.g. test harness).
            for t in (producer_task, writer_task, *worker_tasks):
                if not t.done():
                    t.cancel()
            await asyncio.gather(
                producer_task, writer_task, *worker_tasks, return_exceptions=True
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Batch orchestration error: %s", exc)
            for t in (producer_task, writer_task, *worker_tasks):
                if not t.done():
                    t.cancel()
            await asyncio.gather(
                producer_task, writer_task, *worker_tasks, return_exceptions=True
            )
        finally:
            # Defensive cleanup — make sure nothing stays pending.
            for t in (producer_task, writer_task, *worker_tasks):
                if not t.done():
                    t.cancel()
            await asyncio.gather(
                producer_task, writer_task, *worker_tasks, return_exceptions=True
            )

            # Close JSONL streams if open
            await self._close_jsonl_stream()

        # Final save
        self._stats.elapsed_seconds = time.monotonic() - start_time
        self._save_progress()
        await self._emit_progress(prefix="✅ Done! ")

        logger.info(
            "Batch done | completed=%d active=%d not_found=%d dead=%d error=%d | %.1f/s | %.1fs",
            self._stats.completed,
            self._stats.active,
            self._stats.not_found,
            self._stats.dead,
            self._stats.error,
            self._stats.rate,
            self._stats.elapsed_seconds,
        )

        return self._stats

    # ── Queue actors ─────────────────────────────────────────────────────────

    async def _producer(
        self,
        pending: List[str],
        input_q: asyncio.Queue,
        n_workers: int,
    ) -> None:
        """Feed usernames to the input queue, then push N sentinels."""
        try:
            for username in pending:
                if self._stop_flag:
                    break
                await input_q.put(username)
        finally:
            # Always emit sentinels so workers can drain cleanly even on stop.
            for _ in range(n_workers):
                await input_q.put(None)

    async def _worker(
        self,
        input_q: asyncio.Queue,
        output_q: asyncio.Queue,
    ) -> None:
        """Pull usernames off `input_q`, scrape, push results to `output_q`."""
        while True:
            username = await input_q.get()
            try:
                if username is None:
                    return
                if self._stop_flag:
                    # Drain remaining items quickly without doing work.
                    continue
                try:
                    result = await self._scrape_one_no_semaphore(username)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.debug("Worker unexpected error for @%s: %s", username, exc)
                    result = {
                        "username": username,
                        "status": "error",
                        "error": str(exc),
                        "profile": None,
                        "feed_tags": None,
                        "is_dead": False,
                        "last_post_days": 0,
                    }
                await output_q.put(result)
            finally:
                input_q.task_done()

    async def _writer(
        self,
        output_q: asyncio.Queue,
        start_time: float,
    ) -> None:
        """Consume worker results: update stats, append JSONL, periodic save."""
        cfg = self._config
        done_since_save = 0

        while True:
            result = await output_q.get()
            try:
                if result is _SENTINEL:
                    return
                username = result.get("username", "")
                status = result.get("status", "error")

                async with self._lock:
                    # In low-memory mode, don't keep the per-username dict in
                    # RAM — JSONL is the source of truth.
                    if not self._low_memory_mode:
                        self._results[username] = result
                    self._completed.add(username.lower())
                    self._stats.completed += 1
                    done_since_save += 1

                    if status == "active":
                        self._stats.active += 1
                    elif status == "not_found":
                        self._stats.not_found += 1
                    elif status == "private":
                        self._stats.private += 1
                    elif status == "dead":
                        self._stats.dead += 1
                    else:
                        self._stats.error += 1

                # Stream JSONL incrementally (outside the lock; per-line atomic)
                await self._jsonl_write(result)

                # Fail-fast: abort if error rate is too high (IP-ban / dead cookies).
                if (
                    cfg.fail_fast_threshold > 0
                    and self._stats.completed >= cfg.fail_fast_min_samples
                    and not self._fail_fast_triggered
                ):
                    err_rate = self._stats.error / max(self._stats.completed, 1)
                    if err_rate >= cfg.fail_fast_threshold:
                        self._fail_fast_triggered = True
                        self._stop_flag = True
                        logger.error(
                            "Fail-fast triggered: error_rate=%.1f%% ≥ %.1f%% after %d completions",
                            err_rate * 100,
                            cfg.fail_fast_threshold * 100,
                            self._stats.completed,
                        )

                # Periodic save + log
                if done_since_save >= cfg.save_every:
                    elapsed = time.monotonic() - start_time
                    self._stats.elapsed_seconds = elapsed
                    self._save_progress()
                    done_since_save = 0
                    logger.info(
                        "[%d/%d] saved | active=%d not_found=%d dead=%d error=%d | %.1f/s",
                        self._stats.completed,
                        self._stats.total,
                        self._stats.active,
                        self._stats.not_found,
                        self._stats.dead,
                        self._stats.error,
                        self._stats.rate,
                    )
                    await self._emit_progress()
            finally:
                output_q.task_done()

    # ── JSONL streaming helpers ──────────────────────────────────────────────

    async def _open_jsonl_stream(self, force: bool = False) -> None:
        """Open JSONL handle (async if aiofiles available, otherwise sync)."""
        cfg = self._config
        if not (cfg.stream_jsonl or force):
            return
        jsonl_path = cfg.output_file + ".jsonl"
        try:
            if _HAS_AIOFILES:
                # Append mode — supports resume across runs.
                self._jsonl_afh = await aiofiles.open(jsonl_path, mode="a", encoding="utf-8")
                logger.info("Streaming JSONL (async): %s", jsonl_path)
            else:
                self._jsonl_fh = open(jsonl_path, "a", encoding="utf-8")
                logger.info("Streaming JSONL (sync): %s", jsonl_path)
        except Exception as exc:
            logger.warning("Could not open JSONL stream %s: %s", jsonl_path, exc)
            self._jsonl_fh = None
            self._jsonl_afh = None

    async def _close_jsonl_stream(self) -> None:
        """Flush + close JSONL handle (both backends)."""
        if self._jsonl_afh is not None:
            try:
                await self._jsonl_afh.flush()
                await self._jsonl_afh.close()
            except Exception:
                pass
            self._jsonl_afh = None
        if self._jsonl_fh is not None:
            try:
                self._jsonl_fh.close()
            except Exception:
                pass
            self._jsonl_fh = None

    async def _jsonl_write(self, result: Dict[str, Any]) -> None:
        """Append one record to the JSONL stream (no-op if disabled)."""
        if self._jsonl_afh is None and self._jsonl_fh is None:
            return
        try:
            line = json.dumps(result, ensure_ascii=False) + "\n"
            if self._jsonl_afh is not None:
                await self._jsonl_afh.write(line)
                await self._jsonl_afh.flush()
            elif self._jsonl_fh is not None:
                # Fall back to sync write. Cheap (one line) so we don't bother
                # with an executor — would just add overhead for small writes.
                self._jsonl_fh.write(line)
                self._jsonl_fh.flush()
        except Exception as exc:
            logger.debug("JSONL write failed: %s", exc)

    # ── Targets + progress ───────────────────────────────────────────────────

    def _load_targets(self) -> List[str]:
        """Load usernames from file, skip empty lines and 'target' header."""
        path = self._config.targets_file
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            logger.error("Targets file not found: %s", path)
            return []

        result: List[str] = []
        for line in lines:
            username = line.strip().lstrip("@")
            if not username:
                continue
            if username.lower() == "target":
                continue
            result.append(username.lower())

        return result

    def _load_progress(self) -> Set[str]:
        """Load set of already-completed usernames (lowercase)."""
        path = self._config.progress_file
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return set(u.lower() for u in data.get("completed", []))
        except FileNotFoundError:
            return set()
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Could not load progress file %s: %s", path, exc)
            return set()

    def _load_existing_results(self) -> Dict[str, Any]:
        """Load previously saved profile results from output file (for resume)."""
        path = self._config.output_file
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("profiles", {})
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Could not load existing results from %s: %s", path, exc)
            return {}

    def _save_progress(self) -> None:
        """Atomically save results + progress to disk."""
        cfg = self._config

        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = {
            "total": self._stats.total,
            "completed": self._stats.completed,
            "active": self._stats.active,
            "not_found": self._stats.not_found,
            "private": self._stats.private,
            "dead": self._stats.dead,
            "error": self._stats.error,
        }

        # Only emit the aggregated JSON when explicitly enabled. Big batches
        # (>500 profiles) auto-disable this in `run()` to avoid an OOM-sized
        # final dump; JSONL is the durable artefact in that case.
        if cfg.write_aggregate_json:
            output_data = {
                "metadata": {
                    "started_at": self._started_at,
                    "finished_at": finished_at,
                    "total_targets": self._stats.total,
                    "since_date": cfg.since_date,
                    "until_date": cfg.until_date,
                    "mode": "cookie" if cfg.use_cookies else "anonymous",
                },
                "profiles": self._results,
                "summary": summary,
            }

            # Atomic write: write to temp then rename
            output_dir = os.path.dirname(os.path.abspath(cfg.output_file)) or "."
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=output_dir,
                    delete=False,
                    suffix=".tmp",
                ) as tf:
                    json.dump(output_data, tf, ensure_ascii=False, indent=2)
                    tmp_path = tf.name
                os.replace(tmp_path, cfg.output_file)
            except Exception as exc:
                logger.error("Failed to save output file %s: %s", cfg.output_file, exc)
        else:
            # In low-memory mode we still drop a tiny summary file so callers
            # can see the run's status without reading the full JSONL.
            output_dir = os.path.dirname(os.path.abspath(cfg.output_file)) or "."
            output_data = {
                "metadata": {
                    "started_at": self._started_at,
                    "finished_at": finished_at,
                    "total_targets": self._stats.total,
                    "since_date": cfg.since_date,
                    "until_date": cfg.until_date,
                    "mode": "cookie" if cfg.use_cookies else "anonymous",
                    "note": (
                        "Aggregate JSON disabled (low-memory mode for large "
                        f"batches > {_AGGREGATE_JSON_AUTO_DISABLE} pending). "
                        "Per-profile results are in <output>.jsonl"
                    ),
                },
                "profiles": {},  # explicitly empty — see jsonl
                "summary": summary,
            }
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=output_dir,
                    delete=False,
                    suffix=".tmp",
                ) as tf:
                    json.dump(output_data, tf, ensure_ascii=False, indent=2)
                    tmp_path = tf.name
                os.replace(tmp_path, cfg.output_file)
            except Exception as exc:
                logger.error("Failed to save summary file %s: %s", cfg.output_file, exc)

        # Save progress file (always — small + cheap)
        progress_data = {"completed": sorted(self._completed)}
        progress_dir = os.path.dirname(os.path.abspath(cfg.progress_file)) or "."
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=progress_dir,
                delete=False,
                suffix=".tmp",
            ) as tf:
                json.dump(progress_data, tf, ensure_ascii=False, indent=2)
                tmp_path = tf.name
            os.replace(tmp_path, cfg.progress_file)
        except Exception as exc:
            logger.error("Failed to save progress file %s: %s", cfg.progress_file, exc)

    # ── Single profile scrape ────────────────────────────────────────────────

    async def _scrape_one(
        self,
        username: str,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> Dict[str, Any]:
        """
        Scrape a single profile.

        Kept for backwards compatibility (and existing tests). The new
        worker-pool path calls `_scrape_one_no_semaphore()` directly — the
        bounded pool itself provides concurrency limiting, so no semaphore
        is needed.
        """
        if semaphore is not None:
            async with semaphore:
                return await self._scrape_one_no_semaphore(username)
        return await self._scrape_one_no_semaphore(username)

    async def _scrape_one_no_semaphore(self, username: str) -> Dict[str, Any]:
        """Scrape a single profile without semaphore — used by worker pool."""
        if self._stop_flag:
            return {"username": username, "status": "error", "error": "shutdown"}

        cfg = self._config

        # Fetch with retries; the underlying client already does proxy rotation,
        # so we only need a thin outer retry for transient connectivity blips.
        user: Optional[Dict[str, Any]] = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, cfg.max_retries + 1):
            try:
                user = await self._client.fetch_user(username)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt >= cfg.max_retries:
                    break
                await asyncio.sleep(cfg.retry_base_delay * attempt)

        if last_exc is not None:
            logger.debug("@%s failed after %d retries: %s", username, cfg.max_retries, last_exc)
            return {
                "username": username,
                "status": "error",
                "error": str(last_exc),
                "profile": None,
                "feed_tags": None,
                "is_dead": False,
                "last_post_days": 0,
            }

        if user is None:
            return {
                "username": username,
                "status": "not_found",
                "profile": None,
                "feed_tags": None,
                "is_dead": False,
                "last_post_days": 0,
            }

        # Parse profile + tags using shared parser helpers
        try:
            profile = parse_profile(user, username, _PARSE_CFG)

            is_dead = False
            last_post_days = 0
            feed_tags_result = FeedTagResult()
            feed_skipped = False

            if profile.is_private:
                pass  # no feed for private accounts
            elif cfg.profile_only:
                # Fast path — skip the feed fetch entirely.
                # 'dead' detection falls back to posts_count == 0 (best effort).
                feed_skipped = True
                is_dead = profile.posts_count == 0
                last_post_days = 0 if profile.posts_count > 0 else 9999
            else:
                feed_items = await self._client.fetch_feed_items(
                    user_id=profile.user_id,
                    max_posts=cfg.max_posts,
                    since_timestamp=cfg.since_timestamp,
                )
                feed_tags_result = parse_feed_items(
                    feed_items,
                    max_posts=cfg.max_posts,
                    max_age_days=cfg.max_age_days,
                    since_timestamp=cfg.since_timestamp,
                    until_timestamp=cfg.until_timestamp,
                )
                is_dead, last_post_days = check_dead_account_from_items(
                    feed_items, profile.posts_count
                )

            # Determine status
            if profile.is_private:
                status = "private"
            elif is_dead:
                status = "dead"
            else:
                status = "active"

            feed_tags_data = format_feed_tags_json(feed_tags_result)

            return {
                "username": username,
                "status": status,
                "profile": format_profile_json(profile),
                "feed_tags": feed_tags_data,
                "is_dead": is_dead,
                "last_post_days": last_post_days,
                "feed_skipped": feed_skipped,
            }

        except Exception as exc:
            logger.warning("Parse error for @%s: %s", username, exc)
            return {
                "username": username,
                "status": "error",
                "error": f"parse_error: {exc}",
                "profile": None,
                "feed_tags": None,
                "is_dead": False,
                "last_post_days": 0,
            }

    # ── Signal handler ───────────────────────────────────────────────────────

    def _handle_shutdown(self) -> None:
        """Signal handler for SIGINT — sets stop flag and saves progress."""
        if not self._stop_flag:
            self._stop_flag = True
            logger.info("Ctrl+C received — saving progress and shutting down...")
            self._save_progress()
