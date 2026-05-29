"""Plugin system - load third-party tools via entry_points."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("instagram_mcp.plugins")


class PluginManager:
    """Discover and load third-party tool registrars via entry_points."""

    ENTRY_POINT_GROUP = "instagram_mcp.tools"

    def __init__(self):
        self._plugins: List[Dict[str, Any]] = []
        self._loaded: bool = False

    def discover_plugins(self) -> List[Dict[str, Any]]:
        """Find all entry points in the instagram_mcp.tools group."""
        import importlib.metadata

        plugins = []
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.10+ returns SelectableGroups or dict
            if hasattr(eps, "select"):
                group_eps = eps.select(group=self.ENTRY_POINT_GROUP)
            elif isinstance(eps, dict):
                group_eps = eps.get(self.ENTRY_POINT_GROUP, [])
            else:
                group_eps = [ep for ep in eps if ep.group == self.ENTRY_POINT_GROUP]

            for ep in group_eps:
                plugins.append({
                    "name": ep.name,
                    "module": ep.value if hasattr(ep, "value") else str(ep),
                    "entry_point": ep,
                })
        except Exception as exc:
            logger.warning("Plugin discovery failed: %s", exc)

        return plugins

    def load_plugins(self) -> List[Callable]:
        """Load all discovered plugins. Returns list of registrar callables."""
        discovered = self.discover_plugins()
        registrars = []

        for plugin_info in discovered:
            ep = plugin_info["entry_point"]
            try:
                registrar = ep.load()
                if not callable(registrar):
                    logger.warning(
                        "Plugin '%s' entry point is not callable, skipping",
                        plugin_info["name"],
                    )
                    continue
                registrars.append(registrar)
                self._plugins.append({
                    "name": plugin_info["name"],
                    "module": plugin_info["module"],
                    "status": "loaded",
                    "error": None,
                })
                logger.info("Plugin loaded: %s", plugin_info["name"])
            except Exception as exc:
                self._plugins.append({
                    "name": plugin_info["name"],
                    "module": plugin_info["module"],
                    "status": "error",
                    "error": str(exc),
                })
                logger.warning(
                    "Plugin '%s' failed to load: %s", plugin_info["name"], exc
                )

        self._loaded = True
        return registrars

    def list_plugins(self) -> List[Dict[str, Any]]:
        """Return info about loaded plugins."""
        return list(self._plugins)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
