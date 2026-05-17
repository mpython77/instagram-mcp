"""
Tests for instagram_upload_photo tool.
Covers: UploadPhotoInput validation, _prepare_image, _jpeg_dimensions,
        format_upload_result_markdown, client.upload_photo happy/error paths,
        and tool handler error propagation.
"""
from __future__ import annotations

import asyncio
import io
import struct
import sys
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

# ── shared mock setup (from conftest.py) ────────────────────────────────────
MockToolError = sys.modules["mcp.server.fastmcp.exceptions"].ToolError

# ── import under test ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from instagram_mcp.models import UploadPhotoInput
from instagram_mcp.formatter import format_upload_result_markdown
from instagram_mcp.client import InstagramClient
from instagram_mcp.exceptions import FetchError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid JPEG with the given dimensions in its SOF marker."""
    # FF D8 FF E0  (SOI + APP0 marker)
    # APP0 segment (16 bytes incl length)
    app0 = (
        b"\xff\xe0"          # marker
        + struct.pack(">H", 16)   # length = 16
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    # FF C0 (SOF0) – precision + height + width + 1 component
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 11)   # length
        + b"\x08"                  # precision = 8 bits
        + struct.pack(">H", height)
        + struct.pack(">H", width)
        + b"\x01"                  # components = 1
        + b"\x01\x11\x00"
    )
    # FF D9 (EOI)
    eoi = b"\xff\xd9"
    return b"\xff\xd8" + app0 + sof0 + eoi


def _make_png(width: int = 200, height: int = 150) -> bytes:
    """Create a minimal valid PNG header (first 24 bytes are sufficient for IHDR detection)."""
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk: length(4) + "IHDR" + width(4) + height(4) + depth(1) + colortype(1)
    ihdr_data = struct.pack(">I", width) + struct.pack(">I", height) + b"\x08\x02"
    ihdr_len  = struct.pack(">I", len(ihdr_data) + 4 + 1)  # rough
    ihdr = ihdr_len + b"IHDR" + ihdr_data + b"\x00"
    return sig + ihdr + b"\x00" * 50  # pad so slice [16:24] works


# ─────────────────────────────────────────────────────────────────────────────
# Model validation
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadPhotoInput:
    def test_single_image_valid(self):
        m = UploadPhotoInput(images=["/tmp/a.jpg"])
        assert m.images == ["/tmp/a.jpg"]
        assert m.caption == ""
        assert m.disable_comments is False
        assert m.hide_like_count is False
        assert m.location_id == ""

    def test_carousel_valid(self):
        paths = [f"/tmp/img{i}.jpg" for i in range(5)]
        m = UploadPhotoInput(images=paths)
        assert len(m.images) == 5

    def test_max_10_images(self):
        paths = [f"/tmp/img{i}.jpg" for i in range(10)]
        m = UploadPhotoInput(images=paths)
        assert len(m.images) == 10

    def test_too_many_images_rejected(self):
        from pydantic import ValidationError
        paths = [f"/tmp/img{i}.jpg" for i in range(11)]
        with pytest.raises(ValidationError):
            UploadPhotoInput(images=paths)

    def test_empty_list_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UploadPhotoInput(images=[])

    def test_caption_max_2200(self):
        m = UploadPhotoInput(images=["/tmp/a.jpg"], caption="x" * 2200)
        assert len(m.caption) == 2200

    def test_caption_over_limit(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UploadPhotoInput(images=["/tmp/a.jpg"], caption="x" * 2201)

    def test_flags(self):
        m = UploadPhotoInput(
            images=["/tmp/a.jpg"],
            disable_comments=True,
            hide_like_count=True,
            location_id="123456",
        )
        assert m.disable_comments is True
        assert m.hide_like_count is True
        assert m.location_id == "123456"


# ─────────────────────────────────────────────────────────────────────────────
# _prepare_image & _jpeg_dimensions
# ─────────────────────────────────────────────────────────────────────────────

class TestPrepareImage:
    def test_jpeg_passthrough(self):
        jpeg = _make_jpeg(320, 240)
        out_bytes, w, h = InstagramClient._prepare_image(jpeg, "test.jpg")
        assert out_bytes == jpeg
        assert w == 320
        assert h == 240

    def test_jpeg_dimensions_fallback(self):
        # No SOF marker — should return (1080, 1080)
        fake = b"\xff\xd8\xff" + b"\x00" * 100
        w, h = InstagramClient._jpeg_dimensions(fake)
        assert w == 1080 and h == 1080

    def test_png_dimensions_from_header(self):
        png = _make_png(640, 480)
        # Just check dimensions are extracted correctly without full Pillow conversion
        # The actual conversion requires valid PNG data, so we patch PIL.Image.open
        with patch("PIL.Image.open") as mock_open_img:
            mock_img = MagicMock()
            mock_img.size = (640, 480)
            mock_img.convert.return_value = mock_img
            buf = io.BytesIO()
            mock_img.save = lambda f, **kw: f.write(b"\xff\xd8\xff" + b"\x00" * 20)
            mock_open_img.return_value = mock_img

            out, w, h = InstagramClient._prepare_image(png, "test.png")
            assert w == 640
            assert h == 480

    def test_non_jpeg_without_pillow_raises(self):
        png = _make_png()
        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            with pytest.raises(FetchError, match="not a JPEG"):
                InstagramClient._prepare_image(png, "test.png")

    def test_unknown_format_without_pillow_raises(self):
        random_bytes = b"\x00\x01\x02\x03" * 100
        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            with pytest.raises(FetchError, match="not a JPEG"):
                InstagramClient._prepare_image(random_bytes, "test.bmp")

    def test_jpeg_dimensions_valid_sof(self):
        jpeg = _make_jpeg(1080, 1350)
        w, h = InstagramClient._jpeg_dimensions(jpeg)
        assert w == 1080
        assert h == 1350


# ─────────────────────────────────────────────────────────────────────────────
# format_upload_result_markdown
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatUploadResult:
    def _single_result(self):
        return {
            "ok": True,
            "post_type": "single",
            "shortcode": "DXjuqH9nDVE",
            "url": "https://www.instagram.com/p/DXjuqH9nDVE/",
            "media_id": "3456789012345678901",
            "caption": "Hello world! #test",
            "images_uploaded": 1,
        }

    def _carousel_result(self):
        return {
            "ok": True,
            "post_type": "carousel",
            "shortcode": "ABCxyzDEF",
            "url": "https://www.instagram.com/p/ABCxyzDEF/",
            "media_id": "1234567890123456789",
            "caption": "Three images",
            "images_uploaded": 3,
        }

    def test_single_contains_url(self):
        result = format_upload_result_markdown(self._single_result(), ["/tmp/a.jpg"])
        assert "DXjuqH9nDVE" in result
        assert "instagram.com/p/DXjuqH9nDVE" in result

    def test_single_says_single_photo(self):
        result = format_upload_result_markdown(self._single_result(), ["/tmp/a.jpg"])
        assert "Single photo" in result

    def test_carousel_says_carousel(self):
        paths = ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg"]
        result = format_upload_result_markdown(self._carousel_result(), paths)
        assert "Carousel" in result
        assert "3 images" in result

    def test_caption_shown(self):
        result = format_upload_result_markdown(self._single_result(), ["/tmp/a.jpg"])
        assert "Hello world" in result

    def test_no_caption(self):
        r = dict(self._single_result())
        r["caption"] = ""
        result = format_upload_result_markdown(r, ["/tmp/a.jpg"])
        assert "none" in result.lower()

    def test_file_names_listed(self):
        result = format_upload_result_markdown(self._single_result(), ["/home/user/photo.jpg"])
        assert "photo.jpg" in result

    def test_media_id_shown(self):
        result = format_upload_result_markdown(self._single_result(), ["/tmp/a.jpg"])
        assert "3456789012345678901" in result

    def test_published_header(self):
        result = format_upload_result_markdown(self._single_result(), ["/tmp/a.jpg"])
        assert "Published" in result


# ─────────────────────────────────────────────────────────────────────────────
# InstagramClient.upload_photo — auth check + delegation
# ─────────────────────────────────────────────────────────────────────────────

def _make_client(authenticated: bool = True) -> InstagramClient:
    import instagram_mcp.client as cli_mod
    cli_mod.CURL_CFFI_AVAILABLE = True
    from instagram_mcp.config import MCPConfig

    cfg = MagicMock(spec=MCPConfig)
    cfg.ig_user_agent = "Mozilla/5.0 test"
    cfg.ig_app_id = "936619743392459"
    cfg.cache_profile_ttl = 300
    cfg.max_retries = 3
    cfg.retry_base_delay = 0.0
    cfg.async_max_clients = 10

    cache = MagicMock()
    cache.get_or_fetch = AsyncMock()
    pm = MagicMock()
    pm.get_best_proxy = AsyncMock(return_value=None)
    pm.report_failure = AsyncMock()
    pm.report_success = AsyncMock()
    rl = MagicMock()
    rl.acquire = AsyncMock()
    rl.on_rate_limited = AsyncMock()
    rl.on_success = AsyncMock()

    if authenticated:
        cm = MagicMock()
        cm.is_authenticated = True
        cm.cookies = {
            "sessionid": "sess123",
            "csrftoken": "csrf456",
            "ds_user_id": "789",
        }
    else:
        cm = None

    return InstagramClient(cfg, pm, rl, cache, cookie_manager=cm)


class TestClientUploadPhoto:
    def _make_client(self, authenticated: bool = True):
        return _make_client(authenticated)

    @pytest.mark.asyncio
    async def test_requires_auth(self):
        client = self._make_client(authenticated=False)
        with pytest.raises(FetchError, match="authentication"):
            await client.upload_photo(["/tmp/a.jpg"])

    @pytest.mark.asyncio
    async def test_empty_list_raises(self):
        client = self._make_client()
        with pytest.raises(FetchError, match="At least one"):
            await client.upload_photo([])

    @pytest.mark.asyncio
    async def test_too_many_raises(self):
        client = self._make_client()
        with pytest.raises(FetchError, match="Maximum 10"):
            await client.upload_photo([f"/tmp/img{i}.jpg" for i in range(11)])

    @pytest.mark.asyncio
    async def test_single_delegates_to_configure_single(self):
        client = self._make_client()
        session_mock = AsyncMock()
        client._get_auth_session = AsyncMock(return_value=session_mock)
        expected = {
            "ok": True, "post_type": "single",
            "shortcode": "ABC", "url": "https://www.instagram.com/p/ABC/",
            "media_id": "111", "caption": "hi", "images_uploaded": 1,
        }
        client._upload_single_image = AsyncMock(return_value=("upload_id_1", 1080, 1080))
        client._configure_single = AsyncMock(return_value=expected)

        result = await client.upload_photo(["/tmp/a.jpg"], caption="hi")
        assert result == expected
        client._configure_single.assert_awaited_once()
        args = client._configure_single.call_args[0]
        assert args[2] == "upload_id_1"

    @pytest.mark.asyncio
    async def test_carousel_delegates_to_configure_carousel(self):
        client = self._make_client()
        session_mock = AsyncMock()
        client._get_auth_session = AsyncMock(return_value=session_mock)
        expected = {
            "ok": True, "post_type": "carousel",
            "shortcode": "XYZ", "url": "https://www.instagram.com/p/XYZ/",
            "media_id": "222", "caption": "multi", "images_uploaded": 3,
        }
        client._upload_single_image = AsyncMock(side_effect=[
            ("uid1", 1080, 1080), ("uid2", 1080, 1080), ("uid3", 1080, 1080)
        ])
        client._configure_carousel = AsyncMock(return_value=expected)

        result = await client.upload_photo(
            ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg"], caption="multi"
        )
        assert result == expected
        client._configure_carousel.assert_awaited_once()
        _, _, upload_ids, *_ = client._configure_carousel.call_args[0]
        assert upload_ids == [("uid1", 1080, 1080), ("uid2", 1080, 1080), ("uid3", 1080, 1080)]


# ─────────────────────────────────────────────────────────────────────────────
# _upload_single_image — error paths
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadSingleImage:
    def _make_client(self):
        return _make_client(authenticated=True)

    @pytest.mark.asyncio
    async def test_missing_file_raises(self):
        client = self._make_client()
        session_mock = AsyncMock()
        with pytest.raises(FetchError, match="not found"):
            await client._upload_single_image(
                session_mock, "csrf", "cookie_hdr",
                "/nonexistent/path/image.jpg",
            )

    @pytest.mark.asyncio
    async def test_401_raises_session_expired(self):
        client = self._make_client()
        session_mock = AsyncMock()

        jpeg = _make_jpeg()
        resp_mock = MagicMock()
        resp_mock.status_code = 401
        session_mock.post = AsyncMock(return_value=resp_mock)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(jpeg)
            path = f.name

        try:
            with patch("os.path.isfile", return_value=True), \
                 patch("aiofiles.open") as mock_af:
                mock_af.return_value.__aenter__ = AsyncMock(
                    return_value=AsyncMock(read=AsyncMock(return_value=jpeg))
                )
                mock_af.return_value.__aexit__ = AsyncMock(return_value=False)
                with pytest.raises(FetchError, match="session expired"):
                    await client._upload_single_image(
                        session_mock, "csrf", "cookie_hdr", path
                    )
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_missing_upload_id_in_response_raises(self):
        client = self._make_client()
        session_mock = AsyncMock()

        jpeg = _make_jpeg()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = {}  # no upload_id
        session_mock.post = AsyncMock(return_value=resp_mock)

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=jpeg)))
        mock_file.__exit__ = MagicMock(return_value=False)
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=mock_file), \
             patch.object(client, "_prepare_image", return_value=(jpeg, 1080, 1080)):
            with pytest.raises(FetchError, match="missing upload_id"):
                await client._upload_single_image(
                    session_mock, "csrf", "cookie_hdr", "/fake/a.jpg"
                )

    @pytest.mark.asyncio
    async def test_success_returns_upload_id(self):
        client = self._make_client()
        session_mock = AsyncMock()

        jpeg = _make_jpeg()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = {"upload_id": "99887766"}
        session_mock.post = AsyncMock(return_value=resp_mock)

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=jpeg)))
        mock_file.__exit__ = MagicMock(return_value=False)
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=mock_file), \
             patch.object(client, "_prepare_image", return_value=(jpeg, 1080, 1080)):
            result = await client._upload_single_image(
                session_mock, "csrf", "cookie_hdr", "/fake/a.jpg"
            )
        assert result == ("99887766", 1080, 1080)


# ─────────────────────────────────────────────────────────────────────────────
# _post_configure — error handling
# ─────────────────────────────────────────────────────────────────────────────

class TestPostConfigure:
    def _make_client(self):
        return _make_client(authenticated=True)

    @pytest.mark.asyncio
    async def test_400_raises_with_message(self):
        client = self._make_client()
        session_mock = AsyncMock()
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"message": "Spam detected"}
        resp.text = ""
        session_mock.post = AsyncMock(return_value=resp)

        with pytest.raises(FetchError, match="Spam detected"):
            await client._post_configure(
                session_mock, "csrf", "https://i.instagram.com/configure/",
                {"caption": "test"}, "single", 1,
            )

    @pytest.mark.asyncio
    async def test_success_returns_dict(self):
        client = self._make_client()
        session_mock = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "media": {"code": "DXjuqH9nDVE", "pk": "12345678901234567"}
        }
        session_mock.post = AsyncMock(return_value=resp)

        result = await client._post_configure(
            session_mock, "csrf", "https://i.instagram.com/configure/",
            {"caption": "hello"}, "single", 1,
        )
        assert result["ok"] is True
        assert result["shortcode"] == "DXjuqH9nDVE"
        assert result["post_type"] == "single"
        assert result["images_uploaded"] == 1
        assert "instagram.com/p/DXjuqH9nDVE" in result["url"]

    @pytest.mark.asyncio
    async def test_carousel_images_count(self):
        client = self._make_client()
        session_mock = AsyncMock()
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"media": {"code": "ABCcarousel", "pk": "999"}}
        session_mock.post = AsyncMock(return_value=resp)

        result = await client._post_configure(
            session_mock, "csrf", "https://i.instagram.com/configure_sidecar/",
            {"caption": "carousel"}, "carousel", 4,
        )
        assert result["post_type"] == "carousel"
        assert result["images_uploaded"] == 4
