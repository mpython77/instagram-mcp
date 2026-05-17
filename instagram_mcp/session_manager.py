"""
SessionManager — multi-account Instagram session support.

Loads additional named cookie sessions from environment variables:
    INSTAGRAM_MCP_COOKIES_<ALIAS>=<path>

Example:
    INSTAGRAM_MCP_COOKIES_BRAND=/home/user/brand_cookies.txt
    INSTAGRAM_MCP_COOKIES_AGENCY=/home/user/agency_cookies.txt

Sessions are keyed by lowercase alias. The default session (from
INSTAGRAM_MCP_COOKIES) is always available as alias "default".

Usage:
    manager = SessionManager.from_env(config)
    cookie_manager = manager.get("brand")   # or None if alias unknown
    aliases = manager.list_aliases()        # ["default", "brand", "agency"]
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from .config import MCPConfig
from .cookie_manager import CookieManager

logger = logging.getLogger("instagram_mcp.session_manager")

_ENV_PREFIX = "INSTAGRAM_MCP_COOKIES_"


class SessionManager:
    """Holds multiple named CookieManager instances."""

    def __init__(self) -> None:
        self._sessions: Dict[str, CookieManager] = {}

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, config: MCPConfig) -> "SessionManager":
        """
        Build a SessionManager from environment variables.

        Loads the default session (INSTAGRAM_MCP_COOKIES) plus any
        INSTAGRAM_MCP_COOKIES_<ALIAS> sessions.
        """
        mgr = cls()

        # Default session
        default_cm = CookieManager(cookies_path=config.cookies_path or None)
        try:
            default_cm.load()
        except Exception as e:
            logger.warning("Default session load failed: %s", e)
        mgr._sessions["default"] = default_cm
        if default_cm.is_authenticated:
            logger.info("SessionManager: default session loaded")

        # Named sessions from INSTAGRAM_MCP_COOKIES_<ALIAS>
        for key, val in os.environ.items():
            if key.startswith(_ENV_PREFIX) and val.strip():
                alias = key[len(_ENV_PREFIX):].lower()
                if alias == "default":
                    continue
                cm = CookieManager(cookies_path=val.strip())
                try:
                    cm.load()
                    mgr._sessions[alias] = cm
                    if cm.is_authenticated:
                        logger.info("SessionManager: alias %r loaded from %s", alias, val)
                    else:
                        logger.warning("SessionManager: alias %r cookies invalid at %s", alias, val)
                except Exception as e:
                    logger.warning("SessionManager: alias %r load failed: %s", alias, e)

        logger.info(
            "SessionManager: %d session(s) loaded — %s",
            len(mgr._sessions),
            ", ".join(mgr._sessions.keys()),
        )
        return mgr

    # ── Access ───────────────────────────────────────────────────────────────

    def get(self, alias: str = "default") -> Optional[CookieManager]:
        """Return the CookieManager for the given alias, or None."""
        return self._sessions.get(alias.lower())

    def list_aliases(self) -> List[str]:
        """Return all loaded session aliases."""
        return list(self._sessions.keys())

    def authenticated_aliases(self) -> List[str]:
        """Return aliases where cookies are valid."""
        return [k for k, v in self._sessions.items() if v.is_authenticated]

    def status(self) -> Dict[str, Dict]:
        """Return status dict for all sessions."""
        return {
            alias: {
                "authenticated": cm.is_authenticated,
                "cookies_path": str(cm._path) if getattr(cm, "_path", None) else "",
            }
            for alias, cm in self._sessions.items()
        }

    @property
    def default(self) -> Optional[CookieManager]:
        """The default CookieManager (alias='default')."""
        return self._sessions.get("default")
