import pytest
import json
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
from instagram_mcp.cookie_manager import CookieManager, _parse_json_cookies, _parse_netscape_cookies, _fetch_csrf_tokens

def test_parse_json_cookies():
    raw = json.dumps([
        {"name": "sessionid", "value": "123", "domain": ".instagram.com"},
        {"name": "other", "value": "456", "domain": "google.com"},
        {"name": "", "value": "789"},
        {"name": "bad", "value": "1"}, # missing domain is ok, it skips domain check
        "invalid_entry"
    ])
    cookies = _parse_json_cookies(raw)
    assert cookies == {"sessionid": "123", "bad": "1"}
    
    # Single dict
    raw_single = json.dumps({"name": "sessionid", "value": "123", "domain": ".instagram.com"})
    assert _parse_json_cookies(raw_single) == {"sessionid": "123"}
    
    with pytest.raises(ValueError, match="Expected a JSON array"):
        _parse_json_cookies('"not_array"')

class BadDict(dict):
    def get(self, *args, **kwargs):
        raise Exception("mocked error")

def test_parse_json_cookies_exception():
    # simulate an exception during iteration
    data = [BadDict({"name": "sessionid", "value": "123"})]
    with patch("json.loads", return_value=data):
        cookies = _parse_json_cookies('[]')
        assert cookies == {}

def test_parse_netscape_cookies():
    raw = """
# Netscape HTTP Cookie File
.instagram.com	TRUE	/	TRUE	1234567890	sessionid	123
.google.com	TRUE	/	TRUE	1234567890	other	456
invalid_line_without_enough_tabs
	TRUE	/	TRUE	1234567890		emptyname
"""
    cookies = _parse_netscape_cookies(raw)
    assert cookies == {"sessionid": "123", "other": "456"}

class BadString(str):
    def strip(self, *args, **kwargs):
        raise Exception("mocked error")

class BadRaw(str):
    def splitlines(self):
        return [BadString(".instagram.com\tTRUE\t/\tTRUE\t1234567890\tsessionid\t123")]

def test_parse_netscape_cookies_exception():
    cookies = _parse_netscape_cookies(BadRaw())
    assert cookies == {}

def test_cookie_manager_load_no_file():
    # Patch _resolve_path to return None
    with patch("instagram_mcp.cookie_manager.CookieManager._resolve_path", return_value=None):
        cm = CookieManager()
        assert cm.load() is False
        assert cm.is_authenticated is False
        assert "Authentication required" in cm.auth_required_error()

def test_cookie_manager_load_json(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps([{"name": "sessionid", "value": "123", "domain": ".instagram.com"}]))
    cm = CookieManager(cookies_path=str(p))
    assert cm.load() is True
    assert cm.is_authenticated is True
    assert cm.cookies == {"sessionid": "123"}

def test_cookie_manager_load_netscape(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text(".instagram.com\tTRUE\t/\tTRUE\t1234567890\tsessionid\t123")
    cm = CookieManager(cookies_path=str(p))
    assert cm.load() is True

def test_cookie_manager_load_no_sessionid(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps([{"name": "csrftoken", "value": "123", "domain": ".instagram.com"}]))
    cm = CookieManager(cookies_path=str(p))
    assert cm.load() is False

def test_cookie_manager_load_invalid_file(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text("not json")
    cm = CookieManager(cookies_path=str(p))
    assert cm.load() is False

def test_cookie_manager_resolve_path_exception():
    # Cover the try...except block in _resolve_path
    cm = CookieManager(cookies_path="dummy")
    with patch("pathlib.Path.is_file", side_effect=Exception("Mocked error")):
        assert cm._resolve_path() is None

@pytest.mark.asyncio
async def test_fetch_csrf_tokens():
    session = AsyncMock()
    
    # Success response
    resp = AsyncMock()
    resp.text = '{"fb_dtsg":{"token":"fb123"}, "lsd":{"token":"lsd123"}'
    session.get.return_value = resp
    
    fb_dtsg, lsd = await _fetch_csrf_tokens(session, {})
    assert fb_dtsg == "fb123"
    assert lsd == "lsd123"
    
    # Alternative response
    resp.text = '"DTSGInitData", [], {"token": "fb123"} "LSD", [], {"token": "lsd123"}'
    session.get.return_value = resp
    fb_dtsg, lsd = await _fetch_csrf_tokens(session, {})
    assert fb_dtsg == "fb123"
    assert lsd == "lsd123"
    
    # Missing response
    resp.text = 'nothing'
    session.get.return_value = resp
    fb_dtsg, lsd = await _fetch_csrf_tokens(session, {})
    assert fb_dtsg is None
    assert lsd is None
    
    # Exception response
    session.get.side_effect = Exception("Network error")
    fb_dtsg, lsd = await _fetch_csrf_tokens(session, {})
    assert fb_dtsg is None
    assert lsd is None

@pytest.mark.asyncio
async def test_cookie_manager_ensure_csrf_tokens():
    cm = CookieManager()
    session = AsyncMock()
    resp = AsyncMock()
    resp.text = '{"fb_dtsg":{"token":"fb123"}, "lsd":{"token":"lsd123"}'
    session.get.return_value = resp
    
    tokens = await cm.ensure_csrf_tokens(session)
    assert tokens == ("fb123", "lsd123")
    assert cm.fb_dtsg == "fb123"
    assert cm.lsd == "lsd123"
    
    # Cached within TTL (lines 164-165)
    cm._csrf_cache = None
    tokens2 = await cm.ensure_csrf_tokens(session)
    assert tokens2 == ("fb123", "lsd123")
    
    # Fast path cache hit
    tokens3 = await cm.ensure_csrf_tokens(session)
    assert tokens3 == ("fb123", "lsd123")

@pytest.mark.asyncio
async def test_cookie_manager_ensure_csrf_tokens_failure():
    cm = CookieManager()
    session = AsyncMock()
    resp = AsyncMock()
    resp.text = 'nothing'
    session.get.return_value = resp
    
    with pytest.raises(RuntimeError, match="Could not extract fb_dtsg/lsd"):
        await cm.ensure_csrf_tokens(session)

def test_cookie_manager_load_with_missing_sessionid(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text(".instagram.com\tTRUE\t/\tTRUE\t1234567890\tnot_sessionid\t123")
    cm = CookieManager(cookies_path=str(p))
    assert cm.load() is False
    assert cm._loaded is True

def test_cookie_manager_load_parse_exception(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text("dummy")
    cm = CookieManager(cookies_path=str(p))
    with patch("instagram_mcp.cookie_manager._parse_cookies_file", side_effect=Exception("mocked error")):
        assert cm.load() is False
        assert cm._loaded is True

