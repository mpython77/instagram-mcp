"""Upload mixin for InstagramClient."""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import json as _json

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


class UploadMixin:
    """Media upload methods."""

    # ── Upload ───────────────────────────────────────────────────────────────

    async def upload_photo(
        self,
        image_paths: List[str],
        caption: str = "",
        disable_comments: bool = False,
        hide_like_count: bool = False,
        location_id: str = "",
    ) -> Dict[str, Any]:
        """
        Upload 1–10 images as an Instagram post (single or carousel). Auth required.

        Flow:
          1. Read + validate each image (JPEG natively; PNG → JPEG via Pillow)
          2. POST each image to www.instagram.com/rupload_igphoto/ to get upload_id
          3. POST to /api/v1/media/configure/ (single) or configure_sidecar/ (carousel)

        Returns:
            dict with: ok, post_type, shortcode, url, media_id, caption, images_uploaded
        Raises:
            FetchError: not authenticated, file missing, upload or configure failed
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            raise FetchError(
                "Photo upload requires authentication. "
                "Set up cookies.txt and restart the server."
            )
        if not image_paths:
            raise FetchError("At least one image path is required.")
        if len(image_paths) > 10:
            raise FetchError("Maximum 10 images per post.")

        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        cookie_header = "; ".join(f"{k}={v}" for k, v in (cm.cookies if cm else {}).items())

        is_carousel = len(image_paths) > 1
        uploads: List[tuple] = []
        for path in image_paths:
            item = await self._upload_single_image(session, csrf, cookie_header, path, is_sidecar=is_carousel)
            uploads.append(item)

        if len(uploads) == 1:
            return await self._configure_single(
                session, csrf, uploads[0][0],
                caption, disable_comments, hide_like_count, location_id,
                cookie_header=cookie_header,
            )
        return await self._configure_carousel(
            session, csrf, uploads,
            caption, disable_comments, hide_like_count,
            cookie_header=cookie_header,
        )


    async def _upload_single_image(
        self,
        session: Any,
        csrf: str,
        cookie_header: str,
        path: str,
        is_sidecar: bool = False,
    ) -> tuple:
        """Upload one image file, return (upload_id, width, height)."""
        import os as _os

        if not _os.path.isfile(path):
            raise FetchError(f"Image file not found: {path!r}")

        with open(path, "rb") as fh:
            raw_bytes = fh.read()

        if not raw_bytes:
            raise FetchError(f"Image file is empty: {path!r}")

        jpeg_bytes, width, height = self._prepare_image(raw_bytes, path)

        upload_id = str(int(time.time() * 1000)) + str(random.randint(100, 999))
        content_len = len(jpeg_bytes)

        rupload_params_dict: Dict[str, Any] = {
            "upload_id":           upload_id,
            "media_type":          "1",
            "upload_media_height": str(height),
            "upload_media_width":  str(width),
            "xsharing_user_ids":   "[]",
            "image_compression":   _json.dumps({
                "lib_name":    "moz",
                "lib_version": "3.1.m",
                "quality":     "87",
            }),
        }
        rupload_params = _json.dumps(rupload_params_dict)

        # Use the web-compatible rupload endpoint
        url = f"https://www.instagram.com/rupload_igphoto/{upload_id}"
        headers = {
            "User-Agent":                  self._config.ig_user_agent,
            "X-Instagram-Rupload-Params":  rupload_params,
            "Content-Type":                "image/jpeg",
            "Content-Length":              str(content_len),
            "X-Entity-Type":               "image/jpeg",
            "X-Entity-Name":               f"instagram_photo_{upload_id}",
            "X-Entity-Length":             str(content_len),
            "Offset":                      "0",
            "Accept-Encoding":             "gzip",
            "x-ig-app-id":                 self._config.ig_app_id,
            "Cookie":                      cookie_header,
            "x-csrftoken":                 csrf,
            "Origin":                      "https://www.instagram.com",
            "Referer":                     "https://www.instagram.com/",
        }

        try:
            resp = await session.post(url, data=jpeg_bytes, headers=headers, timeout=90)
        except Exception as exc:
            raise FetchError(f"rupload request failed for {path!r}: {exc}") from exc

        if resp.status_code == 401:
            raise FetchError("rupload 401 — session expired. Re-export cookies.txt.")
        if resp.status_code == 429:
            raise FetchError("rupload 429 — rate limited. Wait a moment and retry.")
        if resp.status_code not in (200, 201):
            raise FetchError(
                f"rupload HTTP {resp.status_code} for {path!r}: {resp.text[:300]}"
            )

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"rupload returned non-JSON: {resp.text[:200]}")

        uid = str(body.get("upload_id") or "")
        if not uid:
            raise FetchError(f"rupload response missing upload_id: {body}")

        return uid, width, height


    async def _configure_single(
        self,
        session: Any,
        csrf: str,
        upload_id: str,
        caption: str,
        disable_comments: bool,
        hide_like_count: bool,
        location_id: str,
        cookie_header: str = "",
    ) -> Dict[str, Any]:
        """POST /api/v1/media/configure/ to publish a single-image post."""
        cm = self._cookie_manager
        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        payload: Dict[str, Any] = {
            "upload_id":                     upload_id,
            "caption":                       caption,
            "source_type":                   "4",
            "disable_comments":              "1" if disable_comments else "0",
            "like_and_view_counts_disabled": "1" if hide_like_count else "0",
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id
        if location_id:
            payload["location"] = _json.dumps({
                "name":               "",
                "facebook_places_id": location_id,
            })

        url = "https://www.instagram.com/api/v1/media/configure/"
        return await self._post_configure(session, csrf, url, payload, "single", 1, cookie_header=cookie_header)


    async def _configure_carousel(
        self,
        session: Any,
        csrf: str,
        uploads: List[tuple],
        caption: str,
        disable_comments: bool,
        hide_like_count: bool,
        cookie_header: str = "",
    ) -> Dict[str, Any]:
        """POST /api/v1/media/configure_sidecar/ to publish a carousel post."""
        sidecar_id = str(int(time.time() * 1000))
        client_sidecar_id = str(uuid.uuid4())

        cm = self._cookie_manager
        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        children = [
            {
                "upload_id":          upload_id,
                "source_type":        "4",
                "timezone_offset":    "0",
            }
            for upload_id, w, h in uploads
        ]
        payload: Dict[str, Any] = {
            "upload_id":                     sidecar_id,
            "client_sidecar_id":             client_sidecar_id,
            "caption":                       caption,
            "source_type":                   "4",
            "children_metadata":             children,
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id

        url = "https://www.instagram.com/api/v1/media/configure_sidecar/"
        return await self._post_configure(session, csrf, url, payload, "carousel", len(uploads), cookie_header=cookie_header, as_json=True)


    async def publish_story(
        self,
        image_path: str,
        close_friends_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Publish a photo story. Auth required.

        Flow:
          1. Upload image via /rupload_igphoto/ (same as post upload)
          2. POST to /api/v1/media/configure_to_story/ with configure_mode=1

        Returns:
            dict with: ok, media_id, story_url (if code available)
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            raise FetchError("publish_story requires authentication.")

        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        cookie_header = self._cookie_str()

        upload_id, w, h = await self._upload_single_image(
            session, csrf, cookie_header, image_path, is_sidecar=False
        )

        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        payload: Dict[str, Any] = {
            "upload_id":                upload_id,
            "source_type":              "4",
            "configure_mode":           "1",
            "post_to_close_friends_only": "1" if close_friends_only else "0",
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id

        url = "https://www.instagram.com/api/v1/media/configure_to_story/"
        headers = {
            "User-Agent":       self._config.ig_user_agent,
            "Accept":           "*/*",
            "Accept-Language":  "en-US,en;q=0.9",
            "Origin":           "https://www.instagram.com",
            "Referer":          "https://www.instagram.com/",
            "x-ig-app-id":      self._config.ig_app_id,
            "x-csrftoken":      csrf,
            "Content-Type":     "application/x-www-form-urlencoded",
            "Cookie":           cookie_header,
        }
        try:
            resp = await session.post(url, data=payload, headers=headers, timeout=30)
        except Exception as exc:
            raise FetchError(f"configure_to_story failed: {exc}") from exc

        if resp.status_code == 400:
            try:
                msg = resp.json().get("message") or resp.text[:300]
            except Exception:
                msg = resp.text[:300]
            raise FetchError(f"configure_to_story 400: {msg}")
        if resp.status_code == 401:
            raise FetchError("configure_to_story 401 — session expired. Re-export cookies.")
        if resp.status_code not in (200, 201):
            raise FetchError(f"configure_to_story HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"configure_to_story non-JSON: {resp.text[:200]}")

        if body.get("status") == "fail":
            raise FetchError(f"configure_to_story API error: {body.get('message', 'unknown')}")

        media = body.get("media") or {}
        media_id = str(media.get("pk") or media.get("id") or "")

        return {
            "ok":       True,
            "media_id": media_id,
            "story_url": f"https://www.instagram.com/stories/{uid}/{media_id}/" if media_id else "",
        }


    async def _upload_video(
        self,
        session: Any,
        csrf: str,
        cookie_header: str,
        path: str,
        is_reel: bool = True,
    ) -> Tuple[str, float]:
        """
        Upload a video file via rupload_igvideo.

        Returns (upload_id, duration_seconds).
        duration_seconds is extracted from the file metadata if ffprobe is available,
        otherwise estimated from file size.
        """
        import os as _os

        if not _os.path.isfile(path):
            raise FetchError(f"Video file not found: {path!r}")

        with open(path, "rb") as fh:
            video_bytes = fh.read()

        if not video_bytes:
            raise FetchError(f"Video file is empty: {path!r}")

        # Determine MIME type
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else "mp4"
        mime = "video/mp4" if ext in ("mp4", "m4v") else f"video/{ext}"

        # Try to get duration via ffprobe
        duration = 0.0
        try:
            import subprocess as _sp
            res = _sp.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", path],
                capture_output=True, text=True, timeout=10
            )
            if res.returncode == 0:
                fmt = _json.loads(res.stdout).get("format", {})
                duration = float(fmt.get("duration", 0.0))
        except Exception:
            pass

        upload_id = str(int(time.time() * 1000)) + str(random.randint(100, 999))
        content_len = len(video_bytes)
        media_type = "2"  # video

        rupload_params = _json.dumps({
            "upload_id":    upload_id,
            "media_type":   media_type,
            "xsharing_user_ids": "[]",
            "upload_media_duration_ms": str(int(duration * 1000)) if duration else "0",
            "is_igtv_video": "0",
            "is_clips_video": "1" if is_reel else "0",
        })

        url = f"https://www.instagram.com/rupload_igvideo/{upload_id}"
        headers = {
            "User-Agent":                  self._config.ig_user_agent,
            "X-Instagram-Rupload-Params":  rupload_params,
            "Content-Type":                mime,
            "Content-Length":              str(content_len),
            "X-Entity-Type":               mime,
            "X-Entity-Name":               f"instagram_video_{upload_id}",
            "X-Entity-Length":             str(content_len),
            "Offset":                      "0",
            "Accept-Encoding":             "gzip",
            "x-ig-app-id":                self._config.ig_app_id,
            "Cookie":                      cookie_header,
            "x-csrftoken":                 csrf,
            "Origin":                      "https://www.instagram.com",
            "Referer":                     "https://www.instagram.com/",
        }

        try:
            resp = await session.post(url, data=video_bytes, headers=headers, timeout=300)
        except Exception as exc:
            raise FetchError(f"video rupload request failed for {path!r}: {exc}") from exc

        if resp.status_code == 401:
            raise FetchError("video rupload 401 — session expired. Re-export cookies.")
        if resp.status_code == 429:
            raise FetchError("video rupload 429 — rate limited. Wait and retry.")
        if resp.status_code not in (200, 201):
            raise FetchError(f"video rupload HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"video rupload returned non-JSON: {resp.text[:200]}")

        uid = str(body.get("upload_id") or "")
        if not uid:
            raise FetchError(f"video rupload response missing upload_id: {body}")

        return uid, duration


    async def upload_reel(
        self,
        video_path: str,
        caption: str = "",
        cover_path: Optional[str] = None,
        disable_comments: bool = False,
        hide_like_count: bool = False,
        share_to_feed: bool = True,
    ) -> Dict[str, Any]:
        """
        Upload a video as an Instagram Reel. Auth required.

        Flow:
          1. Upload video bytes via /rupload_igvideo/
          2. Optionally upload cover thumbnail via /rupload_igphoto/
          3. POST to /api/v1/media/configure_to_reel/ with clip metadata

        Returns:
            dict with ok, shortcode, url, media_id, caption.
        """
        cm = self._cookie_manager
        if cm is None or not getattr(cm, "is_authenticated", False):
            raise FetchError("upload_reel requires authentication.")

        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        cookie_header = self._cookie_str()

        # 1. Upload video
        upload_id, duration = await self._upload_video(
            session, csrf, cookie_header, video_path, is_reel=True
        )

        # 2. Optionally upload cover image
        cover_upload_id = ""
        if cover_path:
            try:
                cover_upload_id, _, _ = await self._upload_single_image(
                    session, csrf, cookie_header, cover_path, is_sidecar=False
                )
            except Exception:
                pass  # cover is optional

        # 3. Configure as Reel
        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        clip_info: Dict[str, Any] = {
            "is_clips_video": True,
            "caption":        caption,
        }
        if duration:
            clip_info["video_length"] = round(duration, 3)

        payload: Dict[str, Any] = {
            "upload_id":                     upload_id,
            "source_type":                   "3",
            "caption":                       caption,
            "clips":                         [clip_info],
            "extra":                         {"source_type": 3},
            "audio_muted":                   False,
            "poster_frame_index":            0,
            "share_to_feed":                 "1" if share_to_feed else "0",
            "disable_comments":              "1" if disable_comments else "0",
            "like_and_view_counts_disabled": "1" if hide_like_count else "0",
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
            payload["device_id"] = device_id
        if cover_upload_id:
            payload["cover_upload_id"] = cover_upload_id

        url = "https://www.instagram.com/api/v1/media/configure_to_reel/"
        return await self._post_configure(
            session, csrf, url, payload, "reel", 1,
            cookie_header=cookie_header, as_json=True
        )


    async def _post_configure(
        self,
        session: Any,
        csrf: str,
        url: str,
        payload: Dict[str, Any],
        post_type: str,
        images_count: int,
        cookie_header: str = "",
        as_json: bool = False,
    ) -> Dict[str, Any]:
        """Common POST helper for configure endpoints."""
        headers = {
            "User-Agent":       self._config.ig_user_agent,
            "Accept":           "*/*",
            "Accept-Language":  "en-US,en;q=0.9",
            "Origin":           "https://www.instagram.com",
            "Referer":          "https://www.instagram.com/",
            "x-ig-app-id":      self._config.ig_app_id,
            "x-csrftoken":      csrf,
            "X-Requested-With": "XMLHttpRequest",
            "X-Instagram-AJAX": "1",
        }
        if as_json:
            headers["Content-Type"] = "application/json"
            data = _json.dumps(payload)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = payload

        if cookie_header:
            headers["Cookie"] = cookie_header

        try:
            resp = await session.post(url, data=data, headers=headers, timeout=30)
        except Exception as exc:
            raise FetchError(f"configure request failed: {exc}") from exc

        if resp.status_code == 400:
            try:
                body = resp.json()
                msg = body.get("message") or body.get("error_title") or resp.text[:300]
            except Exception:
                msg = resp.text[:300]
            raise FetchError(f"configure 400 — {msg}")
        if resp.status_code == 401:
            raise FetchError("configure 401 — session expired. Re-export cookies.txt.")
        if resp.status_code == 429:
            raise FetchError("configure 429 — rate limited. Wait a moment and retry.")
        if resp.status_code not in (200, 201):
            raise FetchError(f"configure HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"configure returned non-JSON: {resp.text[:200]}")

        media = body.get("media") or {}
        code = str(media.get("code") or "")
        media_id = str(media.get("pk") or media.get("id") or "")

        return {
            "ok":             True,
            "post_type":      post_type,
            "shortcode":      code,
            "url":            f"https://www.instagram.com/p/{code}/" if code else "",
            "media_id":       media_id,
            "caption":        payload.get("caption", ""),
            "images_uploaded": images_count,
        }


    @staticmethod
    def _prepare_image(raw_bytes: bytes, path: str) -> tuple:
        """
        Validate and normalise image bytes to JPEG.

        Returns (jpeg_bytes, width, height).
        Accepts JPEG directly. Converts PNG (and other formats) via Pillow.
        """
        import struct as _struct

        # JPEG: FF D8 FF
        if raw_bytes[:3] == b"\xff\xd8\xff":
            width, height = UploadMixin._jpeg_dimensions(raw_bytes)
            return raw_bytes, width, height

        # PNG: 89 50 4E 47  — read dimensions from IHDR chunk at bytes 16-24
        is_png = raw_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        if is_png and len(raw_bytes) >= 24:
            width  = _struct.unpack(">I", raw_bytes[16:20])[0]
            height = _struct.unpack(">I", raw_bytes[20:24])[0]
        else:
            width = height = 0

        # Convert to JPEG via Pillow
        try:
            from PIL import Image as _PILImage
            import io as _io
            img = _PILImage.open(_io.BytesIO(raw_bytes))
            if width == 0:
                width, height = img.size
            img = img.convert("RGB")
            out = _io.BytesIO()
            img.save(out, format="JPEG", quality=87, optimize=True)
            return out.getvalue(), width, height
        except ImportError:
            raise FetchError(
                f"Image {path!r} is not a JPEG. "
                "Install Pillow to support PNG and other formats: pip install Pillow"
            )
        except Exception as exc:
            raise FetchError(f"Failed to convert image {path!r} to JPEG: {exc}") from exc


    @staticmethod
    def _jpeg_dimensions(data: bytes) -> tuple:
        """Extract width and height from JPEG SOF markers."""
        import struct as _struct
        i = 2  # skip FF D8
        while i + 8 < len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            ):
                height = _struct.unpack(">H", data[i + 5:i + 7])[0]
                width  = _struct.unpack(">H", data[i + 7:i + 9])[0]
                return width, height
            seg_len = _struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
        return 1080, 1080  # safe fallback


    async def upload_video_feed(
        self,
        video_path: str,
        caption: str = "",
        cover_path: Optional[str] = None,
        disable_comments: bool = False,
        hide_like_count: bool = False,
    ) -> Dict[str, Any]:
        """Upload a video as a regular Instagram feed post (not a Reel)."""
        import os as _os
        cm, session, csrf = await self._require_auth("upload_video_feed")
        cookie_header = self._cookie_str()

        upload_id, duration = await self._upload_video(session, csrf, cookie_header, video_path, is_reel=False)

        cover_upload_id = ""
        if cover_path and _os.path.isfile(cover_path):
            try:
                cover_upload_id, _, _ = await self._upload_single_image(session, csrf, cookie_header, cover_path, is_sidecar=False)
            except Exception:
                pass

        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""

        payload: Dict[str, Any] = {
            "upload_id": upload_id,
            "source_type": "3",
            "caption": caption,
            "media_type": "2",
            "poster_frame_index": 0,
            "audio_muted": False,
            "disable_comments": "1" if disable_comments else "0",
            "like_and_view_counts_disabled": "1" if hide_like_count else "0",
        }
        if uid:
            payload["_uid"] = uid
        if device_id:
            payload["_uuid"] = device_id
        if duration:
            payload["length"] = round(duration, 3)
        if cover_upload_id:
            payload["cover_upload_id"] = cover_upload_id

        return await self._post_configure(
            session, csrf, "https://www.instagram.com/api/v1/media/upload_finish/",
            payload, "video", 1, cookie_header=cookie_header, as_json=True,
        )

    # ── P4: Account & Feed methods ────────────────────────────────────────────

