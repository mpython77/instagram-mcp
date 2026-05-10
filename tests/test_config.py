import os
from unittest import mock
from pathlib import Path

import pytest

from config import MCPConfig, _load_proxy_file

def test_mcp_config_defaults():
    cfg = MCPConfig()
    assert cfg.ig_app_id == "936619743392459"
    assert "chrome" in cfg.ig_impersonate
    assert "x-ig-app-id" in cfg.ig_headers
    assert cfg.ig_headers["x-ig-app-id"] == "936619743392459"
    assert "User-Agent" in cfg.ig_headers

@mock.patch.dict(os.environ, {
    "INSTAGRAM_MCP_APP_ID": "12345",
    "INSTAGRAM_MCP_IMPERSONATE": "firefox",
    "INSTAGRAM_MCP_TIMEOUT": "20",
    "INSTAGRAM_MCP_MAX_RETRIES": "5",
    "INSTAGRAM_MCP_MAX_WORKERS": "2",
    "INSTAGRAM_MCP_CACHE_DISABLED": "true",
    "INSTAGRAM_MCP_CACHE_TTL": "100",
    "INSTAGRAM_MCP_CACHE_MAX": "200",
    "INSTAGRAM_MCP_PROXIES": "http://p1,http://p2",
    "INSTAGRAM_MCP_PROXY_MAX_FAILS": "10",
    "INSTAGRAM_MCP_PROXY_COOLDOWN": "60",
    "INSTAGRAM_MCP_RATE_LIMIT_RPS": "50.5",
    "INSTAGRAM_MCP_RATE_LIMIT_BURST": "100",
    "INSTAGRAM_MCP_RATE_BACKOFF_FACTOR": "0.5",
    "INSTAGRAM_MCP_RATE_RECOVERY_FACTOR": "1.5",
    "INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD": "10",
    "INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN": "120",
    "INSTAGRAM_MCP_PROXY_MAX_COOLDOWN": "600",
    "INSTAGRAM_MCP_REQUEST_JITTER": "0.5",
    "INSTAGRAM_MCP_GRAPHQL_DOC_ID": "doc_id_123",
    "INSTAGRAM_MCP_MAX_PAGINATION": "500",
    "INSTAGRAM_MCP_COOKIES": "/tmp/cookies.txt"
}, clear=True)
def test_mcp_config_from_env_all():
    cfg = MCPConfig.from_env()
    assert cfg.ig_app_id == "12345"
    assert cfg.ig_impersonate == "firefox"
    assert cfg.request_timeout == 20
    assert cfg.max_retries == 5
    assert cfg.max_workers == 2
    assert cfg.cache_enabled is False
    assert cfg.cache_profile_ttl == 100
    assert cfg.cache_tags_ttl == 50
    assert cfg.cache_status_ttl == 200
    assert cfg.cache_max_entries == 200
    assert cfg.proxy_urls == ["http://p1", "http://p2"]
    assert cfg.proxy_max_fails == 10
    assert cfg.proxy_cooldown == 60
    assert cfg.rate_limit_rps == 50.5
    assert cfg.rate_limit_burst == 100
    assert cfg.rate_backoff_factor == 0.5
    assert cfg.rate_recovery_factor == 1.5
    assert cfg.circuit_breaker_threshold == 10
    assert cfg.circuit_breaker_cooldown == 120.0
    assert cfg.proxy_max_cooldown == 600.0
    assert cfg.request_jitter == 0.5
    assert cfg.ig_graphql_doc_id == "doc_id_123"
    assert cfg.max_pagination_posts == 500
    assert cfg.cookies_path == "/tmp/cookies.txt"

@mock.patch.dict(os.environ, {
    "INSTAGRAM_MCP_CACHE_DISABLED": "1",
}, clear=True)
def test_mcp_config_from_env_cache_disabled_1():
    cfg = MCPConfig.from_env()
    assert cfg.cache_enabled is False

def test_load_proxy_file_from_parent(tmp_path):
    # Mocking Path.is_file and Path.read_text
    parent_file = tmp_path / "proxies.txt"
    parent_file.write_text("http://p1\n# comment\nhttp://p2\n\n  \n")
    
    with mock.patch("config.Path") as mock_path:
        # Mock Path(__file__).parent.parent / "proxies.txt" to point to our temp file
        instance1 = mock.MagicMock()
        instance1.is_file.return_value = True
        instance1.read_text.return_value = parent_file.read_text()
        
        instance2 = mock.MagicMock()
        instance2.is_file.return_value = False
        
        # When Path is called, we return a mock that will eventually yield our mocked instances
        # It's easier to patch the candidates directly
        pass
        
    # Let's patch _load_proxy_file's internal Path directly by patching Path in config
    pass

@mock.patch("config.Path")
def test_load_proxy_file_mocked_path(mock_path_cls):
    # Setup mocks for the paths
    parent_path_mock = mock.MagicMock()
    cwd_path_mock = mock.MagicMock()
    
    mock_path_cls.cwd.return_value.__truediv__.return_value = cwd_path_mock
    mock_path_cls.return_value.parent.parent.__truediv__.return_value = parent_path_mock

    # Test 1: Parent has file
    parent_path_mock.is_file.return_value = True
    parent_path_mock.read_text.return_value = "http://p1\n# comment\nhttp://p2\n"
    
    res = _load_proxy_file()
    assert res == ["http://p1", "http://p2"]

    # Test 2: Parent raises exception, CWD has file
    parent_path_mock.is_file.side_effect = Exception("error")
    cwd_path_mock.is_file.return_value = True
    cwd_path_mock.read_text.return_value = "http://cwd1\n"
    
    res = _load_proxy_file()
    assert res == ["http://cwd1"]

    # Test 3: None have files
    parent_path_mock.is_file.side_effect = None
    cwd_path_mock.is_file.side_effect = None
    parent_path_mock.is_file.return_value = False
    cwd_path_mock.is_file.return_value = False
    
    res = _load_proxy_file()
    assert res == []

@mock.patch("config._load_proxy_file")
@mock.patch.dict(os.environ, {}, clear=True)
def test_mcp_config_proxies_fallback(mock_load):
    mock_load.return_value = ["http://file1"]
    cfg = MCPConfig.from_env()
    assert cfg.proxy_urls == ["http://file1"]
