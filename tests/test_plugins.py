"""Tests for instagram_mcp.plugins module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from instagram_mcp.plugins import PluginManager


class TestPluginManager:
    """Tests for PluginManager class."""

    def test_discover_no_plugins(self):
        """Empty environment returns no plugins."""
        pm = PluginManager()
        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_result = MagicMock()
            mock_result.select.return_value = []
            mock_eps.return_value = mock_result
            plugins = pm.discover_plugins()
        assert plugins == []

    def test_load_plugins_empty(self):
        """Loading with no plugins returns empty list."""
        pm = PluginManager()
        with patch.object(pm, "discover_plugins", return_value=[]):
            registrars = pm.load_plugins()
        assert registrars == []
        assert pm.is_loaded is True

    def test_list_plugins_empty(self):
        """list_plugins returns empty before and after load with no plugins."""
        pm = PluginManager()
        assert pm.list_plugins() == []
        with patch.object(pm, "discover_plugins", return_value=[]):
            pm.load_plugins()
        assert pm.list_plugins() == []

    def test_plugin_error_handling(self):
        """Plugin that raises on load is recorded with error status."""
        pm = PluginManager()

        mock_ep = MagicMock()
        mock_ep.name = "bad_plugin"
        mock_ep.value = "bad_module:register"
        mock_ep.load.side_effect = ImportError("No module named 'bad_module'")

        discovered = [{
            "name": "bad_plugin",
            "module": "bad_module:register",
            "entry_point": mock_ep,
        }]

        with patch.object(pm, "discover_plugins", return_value=discovered):
            registrars = pm.load_plugins()

        assert registrars == []
        plugins = pm.list_plugins()
        assert len(plugins) == 1
        assert plugins[0]["name"] == "bad_plugin"
        assert plugins[0]["status"] == "error"
        assert "No module named" in plugins[0]["error"]

    def test_plugin_success(self):
        """Plugin that loads successfully is recorded and returned."""
        pm = PluginManager()

        def fake_registrar(mcp, client, config, exporter):
            return []

        mock_ep = MagicMock()
        mock_ep.name = "good_plugin"
        mock_ep.value = "good_module:register"
        mock_ep.load.return_value = fake_registrar

        discovered = [{
            "name": "good_plugin",
            "module": "good_module:register",
            "entry_point": mock_ep,
        }]

        with patch.object(pm, "discover_plugins", return_value=discovered):
            registrars = pm.load_plugins()

        assert len(registrars) == 1
        assert registrars[0] is fake_registrar
        plugins = pm.list_plugins()
        assert len(plugins) == 1
        assert plugins[0]["name"] == "good_plugin"
        assert plugins[0]["status"] == "loaded"
        assert plugins[0]["error"] is None

    def test_plugin_not_callable_skipped(self):
        """Plugin entry point that is not callable is skipped."""
        pm = PluginManager()

        mock_ep = MagicMock()
        mock_ep.name = "non_callable_plugin"
        mock_ep.value = "some_module:NOT_A_FUNC"
        mock_ep.load.return_value = "not a callable"

        discovered = [{
            "name": "non_callable_plugin",
            "module": "some_module:NOT_A_FUNC",
            "entry_point": mock_ep,
        }]

        with patch.object(pm, "discover_plugins", return_value=discovered):
            registrars = pm.load_plugins()

        assert registrars == []
        # non-callable plugins are just skipped, not recorded
        assert pm.list_plugins() == []

    def test_is_loaded_property(self):
        """is_loaded property is False before load and True after."""
        pm = PluginManager()
        assert pm.is_loaded is False
        with patch.object(pm, "discover_plugins", return_value=[]):
            pm.load_plugins()
        assert pm.is_loaded is True

    def test_discover_with_dict_entry_points(self):
        """Handles older Python where entry_points returns a dict."""
        pm = PluginManager()

        mock_ep = MagicMock()
        mock_ep.name = "dict_plugin"
        mock_ep.value = "dict_module:register"

        with patch("importlib.metadata.entry_points") as mock_eps:
            # Simulate dict-style return (no 'select' attribute)
            result = {"instagram_mcp.tools": [mock_ep]}
            mock_eps.return_value = result
            plugins = pm.discover_plugins()

        assert len(plugins) == 1
        assert plugins[0]["name"] == "dict_plugin"
