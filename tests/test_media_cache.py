import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from instagram_mcp.media_cache import MediaCache
from instagram_mcp.models import InstagramProfile, InstagramPost, FeedTagResult
from instagram_mcp.client import InstagramClient
from instagram_mcp.config import MCPConfig

@pytest.fixture
def temp_cache_dir():
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_media_cache_get_or_fetch(temp_cache_dir):
    cache = MediaCache(cache_dir=str(temp_cache_dir))
    
    # Mock session
    session = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake image bytes"
    session.get.return_value = mock_resp

    test_url = "https://instagram.fcc.fbcdn.net/v/abc.jpg?_nc_cat=1"
    
    # 1. First call (cache miss)
    cached_uri = await cache.get_or_fetch(test_url, session)
    assert cached_uri.startswith("file://")
    
    # Verify file is written
    clean_url = test_url.split("?")[0]
    expected_path = cache._get_cache_path(clean_url)
    assert expected_path.is_file()
    assert expected_path.read_bytes() == b"fake image bytes"
    
    session.get.assert_called_once_with(test_url, timeout=20)

    # 2. Second call (cache hit)
    session.get.reset_mock()
    cached_uri_2 = await cache.get_or_fetch(test_url, session)
    assert cached_uri_2 == cached_uri
    session.get.assert_not_called()

@pytest.mark.asyncio
async def test_client_cache_media_urls(temp_cache_dir):
    config = MCPConfig(media_cache_dir=str(temp_cache_dir))
    proxy_manager = MagicMock()
    rate_limiter = MagicMock()
    cache = MagicMock()
    client = InstagramClient(
        config=config,
        proxy_manager=proxy_manager,
        rate_limiter=rate_limiter,
        cache=cache,
    )
    
    # Mock low-level session
    session = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake profile pic"
    session.get.return_value = mock_resp
    client._get_session = AsyncMock(return_value=session)
    
    # Profile
    profile = InstagramProfile(
        user_id="123",
        username="test_user",
        full_name="Test User",
        biography="bio",
        followers=100,
        following=100,
        posts_count=5,
        profile_pic_url="https://ig.com/pic.jpg",
    )
    
    await client.cache_media_urls(profile)
    assert profile.profile_pic_url.startswith("file://")
    
    # Feed result
    post = InstagramPost(
        shortcode="XYZ",
        post_url="https://instagram.com/p/XYZ/",
        post_type="image",
        display_url="https://ig.com/post.jpg",
        thumbnail_url="https://ig.com/thumb.jpg",
    )
    feed_result = FeedTagResult(posts=[post])
    
    await client.cache_media_urls(feed_result)
    assert post.display_url.startswith("file://")
    assert post.thumbnail_url.startswith("file://")
    
    await client.close()
