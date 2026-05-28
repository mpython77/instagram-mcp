"""
OAuthManager — Instagram Graph API OAuth 2.0 token lifecycle.

Manages the full OAuth flow for the official Instagram Basic Display API
or Business/Creator account access via the Instagram Graph API:

    1. Generate an authorization URL (init_flow)
    2. Exchange the authorization code for an access token (exchange_code)
    3. Exchange for a long-lived token (valid 60 days)
    4. Auto-refresh before expiry (refresh_token)
    5. Persist tokens to <export_dir>/oauth_tokens.json

Environment variables:
    INSTAGRAM_MCP_OAUTH_APP_ID       — your Meta app ID (client_id)
    INSTAGRAM_MCP_OAUTH_APP_SECRET   — your Meta app secret
    INSTAGRAM_MCP_OAUTH_REDIRECT_URI — redirect URI registered in your app

Note: OAuth tokens grant access to official Graph API endpoints
(business/creator accounts only). Cookies-based tools continue to
work independently and are not affected by this module.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urljoin

logger = logging.getLogger("instagram_mcp.oauth")

_TOKEN_FILE = "oauth_tokens.json"
_LONG_LIVED_EXPIRES = 60 * 24 * 3600   # 60 days in seconds
_REFRESH_THRESHOLD = 7 * 24 * 3600     # refresh when < 7 days remaining

# Instagram OAuth endpoints
_AUTH_URL = "https://www.instagram.com/oauth/authorize"
_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_LONG_LIVED_URL = "https://graph.instagram.com/access_token"
_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
_GRAPH_BASE = "https://graph.instagram.com/v19.0"


class OAuthManager:
    """
    Manages Instagram Graph API OAuth 2.0 tokens.

    Tokens are stored in <export_dir>/oauth_tokens.json so they
    survive server restarts.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        export_dir: str,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._token_file = Path(export_dir) / _TOKEN_FILE
        self._tokens: Dict[str, Any] = self._load()

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, export_dir: str) -> Optional["OAuthManager"]:
        """
        Create an OAuthManager from environment variables.
        Returns None if required env vars are not set.
        """
        app_id = os.environ.get("INSTAGRAM_MCP_OAUTH_APP_ID", "")
        app_secret = os.environ.get("INSTAGRAM_MCP_OAUTH_APP_SECRET", "")
        redirect_uri = os.environ.get(
            "INSTAGRAM_MCP_OAUTH_REDIRECT_URI",
            "https://localhost/callback",
        )
        if not app_id or not app_secret:
            return None
        
        # Use a hidden .state directory to avoid leaking tokens in exports/
        state_dir = os.path.join(os.getcwd(), ".state")
        os.makedirs(state_dir, exist_ok=True)
        return cls(app_id=app_id, app_secret=app_secret, redirect_uri=redirect_uri, export_dir=state_dir)

    # ── OAuth Flow ───────────────────────────────────────────────────────────

    def get_auth_url(self, scopes: Optional[list] = None) -> str:
        """
        Step 1: Generate the OAuth authorization URL.
        Direct users here to grant permissions.
        """
        if scopes is None:
            scopes = ["instagram_business_basic", "instagram_business_manage_messages"]
        params = {
            "client_id": self._app_id,
            "redirect_uri": self._redirect_uri,
            "scope": ",".join(scopes),
            "response_type": "code",
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """
        Step 2: Exchange authorization code for short-lived token,
        then immediately exchange for a long-lived token.
        """
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            raise RuntimeError("curl_cffi required for OAuth token exchange")

        async with AsyncSession() as session:
            # Short-lived token
            resp = await session.post(
                _TOKEN_URL,
                data={
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                    "code": code.strip(),
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}"
                )
            short_data = resp.json()
            short_token = short_data.get("access_token", "")
            if not short_token:
                raise RuntimeError(f"No access_token in response: {short_data}")

            # Long-lived token
            long_resp = await session.get(
                _LONG_LIVED_URL,
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": self._app_secret,
                    "access_token": short_token,
                },
            )
            if long_resp.status_code != 200:
                raise RuntimeError(
                    f"Long-lived token exchange failed (HTTP {long_resp.status_code})"
                )
            long_data = long_resp.json()

        token = long_data.get("access_token", short_token)
        expires_in = int(long_data.get("expires_in", _LONG_LIVED_EXPIRES))
        token_type = long_data.get("token_type", "bearer")

        entry = {
            "access_token": token,
            "token_type": token_type,
            "expires_in": expires_in,
            "obtained_at": int(time.time()),
            "expires_at": int(time.time()) + expires_in,
        }
        self._tokens = entry
        self._save(entry)
        # Log only the expiry timestamp; the access_token itself is never logged
        # (Requirement 23.1 — OAuth tokens redacted from logs).
        logger.info("OAuth tokens saved — expires %s", _ts_str(entry["expires_at"]))
        return self._public_status()

    async def refresh_token(self) -> Dict[str, Any]:
        """
        Refresh the long-lived token (call before it expires — valid for 60 days,
        refreshable at any time while active).
        """
        token = self._tokens.get("access_token", "")
        if not token:
            raise RuntimeError("No token to refresh. Run exchange_code first.")

        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            raise RuntimeError("curl_cffi required for token refresh")

        async with AsyncSession() as session:
            resp = await session.get(
                _REFRESH_URL,
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": token,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}"
                )
            data = resp.json()

        new_token = data.get("access_token", token)
        expires_in = int(data.get("expires_in", _LONG_LIVED_EXPIRES))
        entry = {
            "access_token": new_token,
            "token_type": data.get("token_type", "bearer"),
            "expires_in": expires_in,
            "obtained_at": int(time.time()),
            "expires_at": int(time.time()) + expires_in,
        }
        self._tokens = entry
        self._save(entry)
        # Same redaction policy as exchange_code — only expiry is logged
        # (Requirement 23.1).
        logger.info("OAuth token refreshed — expires %s", _ts_str(entry["expires_at"]))
        return self._public_status()

    # ── Status ───────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return self._public_status()

    @property
    def access_token(self) -> Optional[str]:
        """Return the access token if valid, None otherwise."""
        token = self._tokens.get("access_token", "")
        expires_at = self._tokens.get("expires_at", 0)
        if token and int(time.time()) < expires_at:
            return token
        return None

    @property
    def needs_refresh(self) -> bool:
        """True if token will expire within REFRESH_THRESHOLD."""
        expires_at = self._tokens.get("expires_at", 0)
        remaining = expires_at - int(time.time())
        return 0 < remaining < _REFRESH_THRESHOLD

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        try:
            if self._token_file.exists():
                return json.loads(self._token_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load oauth_tokens.json: %s", exc)
        return {}

    def _save(self, data: Dict[str, Any]) -> None:
        try:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._token_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._token_file)
        except Exception as exc:
            logger.error("Failed to save oauth_tokens.json: %s", exc)

    def _public_status(self) -> Dict[str, Any]:
        token = self._tokens.get("access_token", "")
        expires_at = self._tokens.get("expires_at", 0)
        now = int(time.time())
        remaining = max(0, expires_at - now)
        return {
            "configured": self.is_configured,
            "has_token": bool(token),
            "token_valid": bool(token) and remaining > 0,
            "expires_at": _ts_str(expires_at) if expires_at else "—",
            "days_remaining": round(remaining / 86400, 1) if remaining > 0 else 0,
            "needs_refresh": self.needs_refresh,
            "app_id": self._app_id[:8] + "***" if self._app_id else "—",
            "redirect_uri": self._redirect_uri,
        }


def _ts_str(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)
