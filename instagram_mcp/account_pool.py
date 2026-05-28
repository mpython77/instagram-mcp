import os
import time
import logging
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from .cookie_manager import CookieManager
from ._path_guard import ensure_path
from .state_store import AccountSnapshot
from .metrics import ACCOUNT_POOL_STATE

logger = logging.getLogger("instagram_mcp.accounts")

class AccountPool:
    """Manages a rotating pool of Instagram accounts with health tracking and auto-failover."""
    def __init__(self, accounts_dir: Optional[str] = None):
        if accounts_dir:
            accounts_dir = ensure_path(accounts_dir, name="accounts_dir")
        self.accounts_dir = Path(accounts_dir) if accounts_dir else Path.cwd() / "data" / "accounts"
        self.accounts: Dict[str, CookieManager] = {}
        # Health states: "active", "rate_limited", "checkpoint_required", "expired"
        self.statuses: Dict[str, str] = {}
        self.cooldowns: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._index = 0

    def load_accounts(self) -> int:
        """Scan directory and load all valid account cookie files."""
        if not self.accounts_dir.is_dir():
            try:
                self.accounts_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error("Failed to create accounts directory at %s: %s", self.accounts_dir, e)
                return 0

        # Scan for .json and .txt files
        loaded_count = 0
        for path in self.accounts_dir.iterdir():
            if path.is_file() and path.suffix.lower() in (".json", ".txt"):
                alias = path.stem
                # Skip temp/standard files if they are in the directory
                if alias in ("cookies", "cookie"):
                    continue
                    
                cm = CookieManager(str(path))
                if cm.load():
                    self.accounts[alias] = cm
                    self.statuses[alias] = "active"
                    self.cooldowns[alias] = 0.0
                    loaded_count += 1
                    logger.info("Loaded account pool member: %s", alias)
                else:
                    logger.warning("Failed to load account from cookie file: %s", path.name)

        logger.info("Account pool loaded: %d active accounts", loaded_count)
        return loaded_count

    async def get_next_account(self) -> Optional[Tuple[str, CookieManager]]:
        """Fetch the next available active/healthy account in a round-robin fashion."""
        async with self._lock:
            if not self.accounts:
                return None

            active_members: List[str] = []
            now = time.time()
            
            for alias in list(self.accounts.keys()):
                # Recover rate-limited accounts if cooldown has passed
                if self.statuses[alias] == "rate_limited":
                    cooldown_until = self.cooldowns.get(alias, 0.0)
                    if now >= cooldown_until:
                        self.statuses[alias] = "active"
                        self._emit_account_state_metric(alias, "active")
                        logger.info("Account '%s' recovered from rate-limiting cooldown", alias)
                        
                if self.statuses[alias] == "active":
                    active_members.append(alias)

            if not active_members:
                logger.warning("No healthy active accounts available in the pool!")
                return None

            # Round-robin selection
            self._index = (self._index + 1) % len(active_members)
            selected = active_members[self._index]
            return selected, self.accounts[selected]

    def _emit_account_state_metric(self, alias: str, status: str) -> None:
        """Update ACCOUNT_POOL_STATE gauge so exactly one state label per alias holds value 1."""
        for s in ("active", "rate_limited", "checkpoint_required", "expired"):
            ACCOUNT_POOL_STATE.labels(alias=alias, state=s).set(1 if status == s else 0)

    def mark_rate_limited(self, alias: str, cooldown_seconds: int = 900) -> None:
        """Mark account as rate limited with a cooldown period."""
        if alias in self.accounts:
            self.statuses[alias] = "rate_limited"
            self.cooldowns[alias] = time.time() + cooldown_seconds
            self._emit_account_state_metric(alias, "rate_limited")
            logger.warning("Account '%s' marked as rate-limited for %d seconds", alias, cooldown_seconds)

    def mark_checkpoint(self, alias: str) -> None:
        """Mark account as requiring challenge / checkpoint verification."""
        if alias in self.accounts:
            self.statuses[alias] = "checkpoint_required"
            self._emit_account_state_metric(alias, "checkpoint_required")
            logger.error("Account '%s' marked as checkpoint_required (verification needed)", alias)

    def mark_expired(self, alias: str) -> None:
        """Mark account cookies as expired."""
        if alias in self.accounts:
            self.statuses[alias] = "expired"
            self._emit_account_state_metric(alias, "expired")
            logger.error("Account '%s' marked as expired (session invalid)", alias)

    def restore_account(self, alias: str) -> bool:
        """Restore account state back to active."""
        if alias in self.accounts:
            self.statuses[alias] = "active"
            self.cooldowns[alias] = 0.0
            self._emit_account_state_metric(alias, "active")
            logger.info("Account '%s' restored to active pool", alias)
            return True
        return False

    def get_pool_status(self) -> Dict[str, Dict[str, str]]:
        """Return status dictionary of all pool accounts."""
        return {
            alias: {
                "status": self.statuses.get(alias, "unknown"),
                "cooldown_remaining": f"{max(0.0, self.cooldowns.get(alias, 0.0) - time.time()):.1f}s"
                if self.statuses.get(alias) == "rate_limited" else "0.0s"
            }
            for alias in self.accounts
        }

    def to_snapshot(self) -> list[AccountSnapshot]:
        """Return a list of AccountSnapshot for all pool members (no cookies/tokens included)."""
        snapshots: list[AccountSnapshot] = []
        for alias in self.accounts.keys():
            snapshots.append(
                AccountSnapshot(
                    alias=alias,
                    status=self.statuses.get(alias, "active"),
                    cooldown_until_epoch=int(self.cooldowns.get(alias, 0)),
                    consecutive_failures=0,
                )
            )
        return snapshots

    def restore_from_snapshot(self, accounts: list[AccountSnapshot]) -> None:
        """Restore account pool state from a list of AccountSnapshot.

        Only restores state for aliases that currently exist in self.accounts.
        If cooldown_until_epoch is in the past, resets the account to active.
        """
        now = time.time()
        for snap in accounts:
            if snap.alias not in self.accounts:
                # Account may have been removed from the pool; skip
                continue
            if snap.cooldown_until_epoch < now:
                # Cooldown has expired; reset to active
                self.statuses[snap.alias] = "active"
                self.cooldowns[snap.alias] = 0
                self._emit_account_state_metric(snap.alias, "active")
            else:
                self.statuses[snap.alias] = snap.status
                self.cooldowns[snap.alias] = snap.cooldown_until_epoch
                self._emit_account_state_metric(snap.alias, snap.status)
