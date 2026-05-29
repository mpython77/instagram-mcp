import time
import logging
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from .cookie_manager import CookieManager
from ._path_guard import ensure_path

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
                        logger.info("Account '%s' recovered from rate-limiting cooldown", alias)

                if self.statuses[alias] == "active":
                    active_members.append(alias)

            if not active_members:
                logger.warning("No healthy active accounts available in the pool!")
                return None

            # Round-robin selection: index first, then advance, so the first
            # call returns active_members[0] and rotation stays stable.
            selected = active_members[self._index % len(active_members)]
            self._index += 1
            return selected, self.accounts[selected]

    def mark_rate_limited(self, alias: str, cooldown_seconds: int = 900) -> None:
        """Mark account as rate limited with a cooldown period."""
        if alias in self.accounts:
            self.statuses[alias] = "rate_limited"
            self.cooldowns[alias] = time.time() + cooldown_seconds
            logger.warning("Account '%s' marked as rate-limited for %d seconds", alias, cooldown_seconds)

    def mark_checkpoint(self, alias: str) -> None:
        """Mark account as requiring challenge / checkpoint verification."""
        if alias in self.accounts:
            self.statuses[alias] = "checkpoint_required"
            logger.error("Account '%s' marked as checkpoint_required (verification needed)", alias)

    def mark_expired(self, alias: str) -> None:
        """Mark account cookies as expired."""
        if alias in self.accounts:
            self.statuses[alias] = "expired"
            logger.error("Account '%s' marked as expired (session invalid)", alias)

    def restore_account(self, alias: str) -> bool:
        """Restore account state back to active."""
        if alias in self.accounts:
            self.statuses[alias] = "active"
            self.cooldowns[alias] = 0.0
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
