"""Tests for SessionManager."""

import pytest
from unittest.mock import MagicMock, patch

from instagram_mcp.session_manager import SessionManager
from instagram_mcp.config import MCPConfig


class TestSessionManager:
    def _make_config(self) -> MCPConfig:
        return MCPConfig()

    def test_from_env_default_only(self, monkeypatch):
        """Default session loaded even without extra aliases."""
        monkeypatch.delenv("INSTAGRAM_MCP_COOKIES_BRAND", raising=False)
        monkeypatch.delenv("INSTAGRAM_MCP_COOKIES_AGENCY", raising=False)

        config = self._make_config()
        mgr = SessionManager.from_env(config)

        assert "default" in mgr.list_aliases()

    def test_from_env_with_aliases(self, monkeypatch, tmp_path):
        """Extra aliases loaded from env vars."""
        cookies_file = tmp_path / "brand.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        monkeypatch.setenv("INSTAGRAM_MCP_COOKIES_BRAND", str(cookies_file))

        config = self._make_config()
        mgr = SessionManager.from_env(config)

        aliases = mgr.list_aliases()
        assert "default" in aliases
        assert "brand" in aliases

    def test_get_default(self):
        config = self._make_config()
        mgr = SessionManager.from_env(config)
        cm = mgr.get("default")
        assert cm is not None

    def test_get_nonexistent(self):
        config = self._make_config()
        mgr = SessionManager.from_env(config)
        cm = mgr.get("nonexistent_alias")
        assert cm is None

    def test_list_aliases(self):
        config = self._make_config()
        mgr = SessionManager.from_env(config)
        aliases = mgr.list_aliases()
        assert isinstance(aliases, list)
        assert "default" in aliases

    def test_authenticated_aliases_no_cookies(self):
        """Without a real cookies file, no aliases are authenticated."""
        config = self._make_config()
        mgr = SessionManager.from_env(config)
        authed = mgr.authenticated_aliases()
        assert isinstance(authed, list)

    def test_status_returns_dict(self):
        config = self._make_config()
        mgr = SessionManager.from_env(config)
        status = mgr.status()
        assert isinstance(status, dict)
        assert "default" in status
        assert "authenticated" in status["default"]

    def test_default_property(self):
        config = self._make_config()
        mgr = SessionManager.from_env(config)
        assert mgr.default is not None

    def test_case_insensitive_alias(self, monkeypatch, tmp_path):
        """Alias names are lowercased."""
        cookies_file = tmp_path / "agency.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        monkeypatch.setenv("INSTAGRAM_MCP_COOKIES_AGENCY", str(cookies_file))

        config = self._make_config()
        mgr = SessionManager.from_env(config)
        assert "agency" in mgr.list_aliases()
        assert mgr.get("AGENCY") is not None  # case-insensitive
        assert mgr.get("agency") is not None

    def test_ignores_default_alias_env_var(self, monkeypatch, tmp_path):
        """INSTAGRAM_MCP_COOKIES_DEFAULT should be skipped (conflicts with 'default')."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        monkeypatch.setenv("INSTAGRAM_MCP_COOKIES_DEFAULT", str(cookies_file))

        config = self._make_config()
        mgr = SessionManager.from_env(config)
        # Should only have one 'default' entry
        aliases = mgr.list_aliases()
        assert aliases.count("default") == 1
