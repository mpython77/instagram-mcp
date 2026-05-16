import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
import asyncio
from unittest.mock import MagicMock

# ── Shared MCP mock setup ────────────────────────────────────────────────────
# Set up once here so all test files share the same MockToolError class.
# This prevents conflicts when multiple test files mock these modules.

class MockToolError(Exception):
    pass

_mock_exceptions_mod = MagicMock()
_mock_exceptions_mod.ToolError = MockToolError

_mock_fastmcp_mod = MagicMock()
_mock_fastmcp_mod.Context = MagicMock
_mock_fastmcp_mod.FastMCP = MagicMock

sys.modules.setdefault("mcp", MagicMock())
sys.modules.setdefault("mcp.server", MagicMock())
sys.modules["mcp.server.fastmcp"] = _mock_fastmcp_mod
sys.modules["mcp.server.fastmcp.exceptions"] = _mock_exceptions_mod

pytest_plugins = ('pytest_asyncio',)
