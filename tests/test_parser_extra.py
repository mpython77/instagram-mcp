import pytest
from unittest.mock import MagicMock
from instagram_mcp.parser import _extract_location, parse_tagged_tab_edges, parse_reels_edges, parse_post_html

def test_extract_location_exception():
    # Trigger exception in _extract_location
    # We need loc.get("name") to not be a method or something that raises exception?
    # Actually loc.get("name") is called. If loc is not a dict... 
    # but the caller ensures it's a dict. 
    # Let's use a proxy object that raises Exception on get
    class EvilDict(dict):
        def get(self, *args, **kwargs):
            raise Exception("Evil")
    
    assert _extract_location({"location": EvilDict()}) is None

def test_parse_tagged_tab_edges_empty_node():
    # Cover line 604: code = node.get("code") or ""
    # if node is {}
    result = parse_tagged_tab_edges([{"node": {}}])
    assert len(result) == 1
    assert result[0].shortcode == ""

def test_parse_reels_edges_empty_media():
    # Cover line 782: code = str(media.get("code") or "")
    result = parse_reels_edges([{"node": {"media": {}}}])
    assert len(result) == 1
    assert result[0].shortcode == ""

def test_parse_post_html_location_exception():
    # Cover lines 1019-1020: except (ValueError, KeyError): pass
    html = '<script>{"location": {"lat": "invalid"}}</script>'
    # This might not trigger it if it doesn't match the RE.
    # We need it to match _POST_LOCATION_RE but fail during parsing.
    from instagram_mcp.parser import PostInfo
    info = PostInfo()
    # Mocking the RE match to return something that triggers ValueError in float()
    with MagicMock() as mock_re:
        import re
        mock_match = MagicMock()
        mock_match.group.return_value = '{"lat": "abc"}'
        # This is hard to mock because of internal imports.
        # Let's just try to provide a string that matches.
    
    # Actually, let's just use a string that matches:
    # "location":\s*({.*?})
    html = '"location": {"lat": "not_a_float"}'
    parse_post_html(html, "abc") # should pass silently

def test_parse_post_html_caption_exception():
    # Cover lines 1093-1094: except (ValueError, UnicodeDecodeError)
    # "edge_media_to_caption":{"edges":[{"node":{"text":"...
    # We need raw_cap to be something that json.loads(f'"{raw_cap}"') fails on.
    # e.g. unescaped double quote?
    # loads('"a"b"') fails.
    html = '"edge_media_to_caption":{"edges":[{"node":{"text":"a"b"}}]}'
    parse_post_html(html, "abc") # should fallback
