"""Tests for OAuthManager."""

import json
import time
import pytest
from pathlib import Path

from instagram_mcp.oauth_manager import OAuthManager, _ts_str


class TestTsStr:
    def test_valid(self):
        assert "UTC" in _ts_str(1716000000)

    def test_zero(self):
        _ts_str(0)


class TestOAuthManagerFromEnv:
    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("INSTAGRAM_MCP_OAUTH_APP_ID", raising=False)
        monkeypatch.delenv("INSTAGRAM_MCP_OAUTH_APP_SECRET", raising=False)
        result = OAuthManager.from_env("/tmp")
        assert result is None

    def test_configured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("INSTAGRAM_MCP_OAUTH_APP_ID", "app123")
        monkeypatch.setenv("INSTAGRAM_MCP_OAUTH_APP_SECRET", "secret456")
        result = OAuthManager.from_env(str(tmp_path))
        assert result is not None
        assert result._app_id == "app123"
        assert result._app_secret == "secret456"


class TestOAuthManager:
    def _make(self, tmp_path, app_id="app123", secret="secret"):
        return OAuthManager(app_id=app_id, app_secret=secret, redirect_uri="https://localhost/cb", export_dir=str(tmp_path))

    def test_get_auth_url(self, tmp_path):
        mgr = self._make(tmp_path)
        url = mgr.get_auth_url()
        assert "instagram.com/oauth/authorize" in url
        assert "app123" in url
        assert "client_id" in url

    def test_get_auth_url_custom_scopes(self, tmp_path):
        mgr = self._make(tmp_path)
        url = mgr.get_auth_url(scopes=["instagram_business_basic"])
        assert "instagram_business_basic" in url

    def test_status_no_token(self, tmp_path):
        mgr = self._make(tmp_path)
        status = mgr.status()
        assert status["has_token"] is False
        assert status["token_valid"] is False
        assert status["configured"] is True

    def test_status_with_valid_token(self, tmp_path):
        mgr = self._make(tmp_path)
        mgr._tokens = {
            "access_token": "sometoken",
            "expires_at": int(time.time()) + 86400 * 30,
            "obtained_at": int(time.time()),
        }
        status = mgr.status()
        assert status["has_token"] is True
        assert status["token_valid"] is True
        assert status["days_remaining"] > 0

    def test_status_with_expired_token(self, tmp_path):
        mgr = self._make(tmp_path)
        mgr._tokens = {
            "access_token": "oldtoken",
            "expires_at": int(time.time()) - 100,
            "obtained_at": int(time.time()) - 86400 * 70,
        }
        status = mgr.status()
        assert status["token_valid"] is False
        assert status["days_remaining"] == 0

    def test_needs_refresh_false_when_plenty_of_time(self, tmp_path):
        mgr = self._make(tmp_path)
        mgr._tokens = {"access_token": "t", "expires_at": int(time.time()) + 86400 * 30}
        assert mgr.needs_refresh is False

    def test_needs_refresh_true_when_soon(self, tmp_path):
        mgr = self._make(tmp_path)
        mgr._tokens = {"access_token": "t", "expires_at": int(time.time()) + 86400 * 3}
        assert mgr.needs_refresh is True

    def test_access_token_property(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr.access_token is None  # no token

        mgr._tokens = {"access_token": "valid", "expires_at": int(time.time()) + 1000}
        assert mgr.access_token == "valid"

        mgr._tokens = {"access_token": "expired", "expires_at": int(time.time()) - 1}
        assert mgr.access_token is None

    def test_is_configured(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr.is_configured is True

        empty_mgr = OAuthManager(app_id="", app_secret="", redirect_uri="", export_dir=str(tmp_path))
        assert empty_mgr.is_configured is False

    def test_persistence(self, tmp_path):
        """Tokens persist across OAuthManager instances."""
        mgr1 = self._make(tmp_path)
        future_ts = int(time.time()) + 86400 * 50
        mgr1._tokens = {"access_token": "persist_tok", "expires_at": future_ts}
        mgr1._save(mgr1._tokens)

        mgr2 = self._make(tmp_path)
        assert mgr2._tokens.get("access_token") == "persist_tok"
        assert mgr2.access_token == "persist_tok"

    def test_save_load_roundtrip(self, tmp_path):
        mgr = self._make(tmp_path)
        data = {"access_token": "abc", "expires_at": 9999999999}
        mgr._save(data)
        loaded = mgr._load()
        assert loaded == data

    def test_public_status_masks_app_id(self, tmp_path):
        mgr = self._make(tmp_path, app_id="verylongappid123")
        status = mgr._public_status()
        assert "***" in status["app_id"]
        assert "verylong" in status["app_id"]  # first 8 chars shown
