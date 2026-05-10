"""
Batch scraper for instagram_mcp.

Features:
  - Resume support (progress file tracks completed usernames)
  - Graceful Ctrl+C shutdown (saves progress before exit)
  - Real-time save every BATCH_SAVE_EVERY profiles
  - Parallel async execution with configurable workers
  - Date range filtering (since/until timestamps)
  - Cookie support
  - Detailed stats tracking
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
from .models import DateRange, FeedTagResult
from .parser import (
    check_dead_account,
    extract_page_info,
    parse_feed_tags,
    parse_feed_tags_from_edges,
    parse_profile,
)

logger = logging.getLogger("instagram_mcp.batch")

# Single shared parse-time config — no env reads, used purely for parser hints
_PARSE_CFG = MCPConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date(date_str: str) -> Optional[int]:
    """Parse DD.MM.YYYY to Unix timestamp. Returns None if empty."""
    if not date_str:
        return None
    try:
        return int(datetime.strptime(date_str.strip().replace(",", "."), "%d.%m.%Y").timestamp())
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
    Production-grade batch Instagram scraper.

    Usage:
        config = BatchConfig(targets_file="users.txt", output_file="results.json")
        runner = BatchRunner(config, instagram_client)
        stats = await runner.run()
    """

    def __init__(self, config: BatchConfig, instagram_client: Any) -> None:
        self._config = config
        self._client = instagram_client
        self._stats = BatchStats()
        self._results: Dict[str, Any] = {}        # username → result dict
        self._completed: Set[str] = set()         # already-done usernames (lowercase)
        self._stop_flag = False
        self._lock = asyncio.Lock()
        self._started_at: str = ""

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

        # Load previously saved results (for resume)
        self._results = self._load_existing_results()

        pending = [u for u in targets if u.lower() not in self._completed]

        self._stats.total = len(targets)
        self._stats.completed = len(self._completed)

        logger.info(
            "Batch start | total=%d pending=%d already_done=%d | output=%s",
            len(targets),
            len(pending),
            len(self._completed),
            cfg.output_file,
        )

        start_time = time.monotonic()
        semaphore = asyncio.Semaphore(cfg.max_workers)

        tasks = [
            asyncio.create_task(self._scrape_one(username, semaphore))
            for username in pending
        ]

        done_since_save = 0

        for coro in asyncio.as_completed(tasks):
            if self._stop_flag:
                # Cancel remaining tasks
                for t in tasks:
                    if not t.done():
                        t.cancel()
                break

            try:
                result = await coro
            except asyncio.CancelledError:
                continue
            except Exception as exc:
                logger.error("Unexpected error from scrape task: %s", exc)
                continue
            username = result.get("username", "")
            status = result.get("status", "error")

            # Update stats
            async with self._lock:
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

        # Final save
        self._stats.elapsed_seconds = time.monotonic() - start_time
        self._save_progress()

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

        # Save progress file
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

    async def _scrape_one(self, username: str, semaphore: asyncio.Semaphore) -> Dict[str, Any]:
        """Scrape a single profile. Returns result dict."""
        async with semaphore:
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

                if not profile.is_private:
                    _date_range = DateRange(
                        since=cfg.since_timestamp,
                        until=cfg.until_timestamp,
                    ) if (cfg.since_timestamp or cfg.until_timestamp) else None

                    if cfg.max_posts <= 12:
                        # First page only — no extra requests needed
                        feed_tags_result = parse_feed_tags(
                            user,
                            cfg.max_posts,
                            cfg.max_age_days,
                            date_range=_date_range,
                        )
                    else:
                        # Paginate to collect up to max_posts across multiple pages
                        page_info = extract_page_info(user)
                        first_edges = page_info.get("first_page_edges", [])
                        end_cursor = page_info.get("end_cursor", "")
                        has_next = page_info.get("has_next_page", False)
                        all_edges = list(first_edges)
                        pages_fetched = 1
                        has_more = has_next

                        remaining = cfg.max_posts - len(all_edges)
                        if remaining > 0 and has_next and end_cursor:
                            feed_result = await self._client.fetch_user_feed(
                                user_id=profile.user_id,
                                username=username,
                                end_cursor=end_cursor,
                                max_posts=remaining,
                                max_age_days=cfg.max_age_days,
                                date_range=_date_range,
                            )
                            all_edges.extend(feed_result.get("edges", []))
                            pages_fetched += feed_result.get("pages_fetched", 0)
                            has_more = feed_result.get("has_more", False)

                        feed_tags_result = parse_feed_tags_from_edges(
                            edges=all_edges,
                            max_posts=cfg.max_posts,
                            max_age_days=cfg.max_age_days,
                            detect_pinned=True,
                            pages_fetched=pages_fetched,
                            has_more_posts=has_more,
                            date_range=_date_range,
                        )

                    is_dead, last_post_days = check_dead_account(user)

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
