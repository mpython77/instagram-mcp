"""Upload toolset — photo / reel uploads and media download.

Tools: instagram_upload_photo, instagram_upload_reel, instagram_download.
All require an authenticated session (🔐).

Validates: Requirements 1.2, 2.1–2.5, 4.5, 4.6, 5.1–5.3, 8.1, 8.3, 17.2.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context, FastMCP

from ..exceptions import FetchError
from ..formatter import format_upload_result_markdown
from ..models import DownloadInput, UploadPhotoInput, UploadReelInput
from ._helpers import (
    ToolDescriptor,
    _exception_to_tool_error,
    _tool_error,
)

if TYPE_CHECKING:
    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger("instagram_mcp.tools.upload")

TOOLSET_NAME = "upload"


# ---------------------------------------------------------------------------
# Annotation constants (kept module-local so the registrar and the
# ``ToolDescriptor`` payload stay in lock-step — the annotation audit relies
# on them being identical).
# ---------------------------------------------------------------------------

_UPLOAD_PHOTO_ANNOTATIONS: dict = {
    "title": "Instagram Upload Photo",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

_UPLOAD_REEL_ANNOTATIONS: dict = {
    "title": "Instagram Upload Reel",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

_DOWNLOAD_ANNOTATIONS: dict = {
    "title": "Instagram Download Media",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

_UPLOAD_PHOTO_DESCRIPTION = (
    "🔐 Upload 1–10 images to Instagram as a post (single photo or carousel). "
    "Requires authenticated session (cookies.txt). "
    "Supports JPEG natively; PNG requires Pillow (pip install Pillow). "
    "Returns the post URL and shortcode immediately after publishing."
)

_DOWNLOAD_DESCRIPTION = (
    "🔐 Download all media from an Instagram post to a local directory. "
    "Supports single images, videos/reels, and carousels (all slides). "
    "Requires authenticated session (cookies.txt). "
    "Returns the list of saved file paths and media info."
)


def register_upload(
    mcp: FastMCP,
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register the upload toolset.

    All three tools require auth, so the entire toolset is hidden when
    ``MCPConfig.hide_auth_when_no_cookies`` is set and no session cookies
    are loaded — mirroring the legacy ``_enabled("upload", requires_auth=True)``
    gate in ``tools.py``.
    """
    descriptors: list[ToolDescriptor] = []

    is_authed = bool(
        getattr(getattr(client, "cookie_manager", None), "is_authenticated", False)
    )
    if config.hide_auth_when_no_cookies and not is_authed:
        # Every tool in this submodule is auth-required; skip the lot.
        return descriptors

    # ------------------------------------------------------------------
    # TOOL: instagram_upload_photo
    # ------------------------------------------------------------------
    @mcp.tool(
        name="instagram_upload_photo",
        description=_UPLOAD_PHOTO_DESCRIPTION,
        annotations=_UPLOAD_PHOTO_ANNOTATIONS,
    )
    async def instagram_upload_photo(params: UploadPhotoInput, ctx: Context) -> str:
        _t0 = time.perf_counter()
        n = len(params.images)
        post_kind = "carousel" if n > 1 else "single photo"
        await ctx.info(f"upload_photo — {n} image(s) → {post_kind}")
        await ctx.report_progress(0.0, 1.0, message=f"Preparing {n} image(s)...")

        try:
            result = await client.upload_photo(
                image_paths=params.images,
                caption=params.caption,
                disable_comments=params.disable_comments,
                hide_like_count=params.hide_like_count,
                location_id=params.location_id,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        elapsed = time.perf_counter() - _t0
        shortcode = result.get("shortcode", "")
        post_url  = result.get("url", "")

        await ctx.report_progress(1.0, 1.0, message="Published!")
        await ctx.info(
            f"upload_photo ✓ — {post_kind}, shortcode={shortcode!r}, {elapsed:.2f}s"
        )
        await exporter.save(
            "upload_photo",
            shortcode or "unknown",
            result,
            elapsed,
        )
        return format_upload_result_markdown(result, params.images)

    descriptors.append(
        ToolDescriptor(
            name="instagram_upload_photo",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=dict(_UPLOAD_PHOTO_ANNOTATIONS),
            input_model=UploadPhotoInput,
            description_first_line=(
                "🔐 Upload 1–10 images to Instagram as a post "
                "(single photo or carousel)."
            ),
        )
    )

    # ------------------------------------------------------------------
    # TOOL: instagram_upload_reel
    # ------------------------------------------------------------------
    @mcp.tool(
        name="instagram_upload_reel",
        annotations=_UPLOAD_REEL_ANNOTATIONS,
    )
    async def instagram_upload_reel(params: UploadReelInput, ctx: Context) -> str:
        """
        🔐 AUTH REQUIRED — Upload a video as an Instagram Reel.

        Uploads an MP4 video and publishes it as a Reel.
        Optionally specify a cover image, caption, and share-to-feed.

        Args:
            video_path: Absolute local path to an MP4 video file
            caption: Optional caption (max 2200 chars)
            cover_path: Optional local path to a JPEG/PNG cover image
            disable_comments: Disable comments on the Reel
            hide_like_count: Hide like count from viewers
            share_to_feed: Also share to main feed (default: True)

        Returns:
            Reel URL and media_id on success.
        """
        await ctx.info(f"instagram_upload_reel: {params.video_path!r}")
        await ctx.report_progress(0.0, 1.0, message="Uploading video...")
        try:
            result = await client.upload_reel(
                video_path=params.video_path,
                caption=params.caption,
                cover_path=params.cover_path,
                disable_comments=params.disable_comments,
                hide_like_count=params.hide_like_count,
                share_to_feed=params.share_to_feed,
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

        await ctx.report_progress(1.0, 1.0, message="Reel published!")
        shortcode = result.get("shortcode", "")
        url = result.get("url", "")
        media_id = result.get("media_id", "")
        lines = ["**Reel published successfully!**"]
        if url:
            lines.append(f"URL: {url}")
        if media_id:
            lines.append(f"media_id: {media_id}")
        if params.caption:
            lines.append(f"Caption: {params.caption[:80]}{'...' if len(params.caption) > 80 else ''}")
        return "\n".join(lines)

    descriptors.append(
        ToolDescriptor(
            name="instagram_upload_reel",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=dict(_UPLOAD_REEL_ANNOTATIONS),
            input_model=UploadReelInput,
            description_first_line=(
                "🔐 AUTH REQUIRED — Upload a video as an Instagram Reel."
            ),
        )
    )

    # ------------------------------------------------------------------
    # TOOL: instagram_download
    # ------------------------------------------------------------------
    @mcp.tool(
        name="instagram_download",
        description=_DOWNLOAD_DESCRIPTION,
        annotations=_DOWNLOAD_ANNOTATIONS,
    )
    async def instagram_download(params: DownloadInput, ctx: Context) -> str:
        """
        Download all media files from an Instagram post.

        🔐 Requires cookies.txt with a valid Instagram session.

        Fetches full media info via /api/v1/media/{id}/info/ then downloads
        each file (image/video) from Instagram's CDN to save_dir.

        Supports:
          - Single image posts → saves 1 .jpg
          - Video / Reel posts → saves 1 .mp4
          - Carousel posts     → saves N .jpg/.mp4 files (one per slide)

        Args:
            params: post (shortcode or URL), save_dir (output directory)

        Returns:
            Markdown summary with file paths, sizes, and media info.
        """
        import os
        import mimetypes
        from curl_cffi.requests import AsyncSession as _CurlSession

        _t0 = time.perf_counter()
        shortcode = params.post
        save_dir = params.save_dir.rstrip("/")

        await ctx.info(f"instagram_download: {shortcode} → {save_dir}")

        if not os.path.isdir(save_dir):
            raise _tool_error(
                f"Directory does not exist: {save_dir!r}",
                "validation_error",
                "Provide an existing absolute directory path for save_dir.",
            )

        # ── 1. Fetch media info ──────────────────────────────────────────
        await ctx.report_progress(0.1, 1.0, message="Fetching media info…")
        try:
            item = await client.fetch_media_info(shortcode)
        except FetchError as exc:
            raise _tool_error(str(exc), "fetch_error", "Check the shortcode and your session cookies.")

        media_type = item.get("media_type", 0)  # 1=image, 2=video, 8=carousel

        # ── 2. Collect (ext, url) pairs ─────────────────────────────────
        def _best_image(node: dict) -> str:
            iv2 = node.get("image_versions2") or {}
            cands = iv2.get("candidates") or []
            return cands[0]["url"] if cands else ""

        def _best_video(node: dict) -> str:
            # Try video_url first (older posts), then video_versions (reels/clips)
            vurl = node.get("video_url", "")
            if vurl:
                return vurl
            versions = node.get("video_versions") or []
            if versions:
                # versions are sorted by bandwidth desc; take highest quality
                return versions[0].get("url", "")
            return ""

        media_pairs: list = []  # [(ext, url), ...]
        if media_type == 1:
            url = _best_image(item)
            if url:
                media_pairs.append(("jpg", url))
        elif media_type == 2:
            vurl = _best_video(item)
            if vurl:
                media_pairs.append(("mp4", vurl))
            else:
                url = _best_image(item)
                if url:
                    media_pairs.append(("jpg", url))
        elif media_type == 8:
            for slide in item.get("carousel_media") or []:
                stype = slide.get("media_type", 1)
                if stype == 2:
                    vurl = _best_video(slide)
                    if vurl:
                        media_pairs.append(("mp4", vurl))
                        continue
                url = _best_image(slide)
                if url:
                    media_pairs.append(("jpg", url))

        if not media_pairs:
            raise _tool_error(
                f"No downloadable media found in post {shortcode!r}",
                "fetch_error",
                "Post may be private, or media URLs were not returned by Instagram.",
            )

        # ── 3. Download each file ────────────────────────────────────────
        saved_files: list = []
        total = len(media_pairs)
        await ctx.report_progress(0.2, 1.0, message=f"Downloading {total} file(s)…")

        async with _CurlSession(impersonate=config.ig_impersonate) as dl_session:
            for idx, (ext, url) in enumerate(media_pairs, 1):
                fname = f"{shortcode}_{idx}.{ext}"
                fpath = os.path.join(save_dir, fname)
                try:
                    resp = await dl_session.get(
                        url,
                        headers={"Referer": "https://www.instagram.com/"},
                    )
                    if resp.status_code != 200:
                        saved_files.append({"file": fname, "ok": False, "error": f"HTTP {resp.status_code}"})
                        continue
                    with open(fpath, "wb") as f:
                        f.write(resp.content)
                    size_kb = len(resp.content) // 1024
                    saved_files.append({"file": fname, "path": fpath, "size_kb": size_kb, "type": ext, "ok": True})
                    await ctx.report_progress(0.2 + 0.8 * idx / total, 1.0, message=f"Saved {fname} ({size_kb} KB)")
                except Exception as exc:
                    saved_files.append({"file": fname, "ok": False, "error": str(exc)})

        elapsed = time.perf_counter() - _t0

        # ── 4. Format output ─────────────────────────────────────────────
        type_label = {1: "image", 2: "video", 8: "carousel"}.get(media_type, "unknown")
        ok_files = [f for f in saved_files if f.get("ok")]
        fail_files = [f for f in saved_files if not f.get("ok")]

        lines = [
            f"## Download complete — `{shortcode}`",
            f"- **Type**: {type_label}",
            f"- **Files**: {len(ok_files)}/{total} saved in `{save_dir}`",
            f"- **Time**: {elapsed:.2f}s",
            "",
            "### Saved files",
        ]
        for f in ok_files:
            lines.append(f"- `{f['path']}` ({f['size_kb']} KB, {f['type']})")
        if fail_files:
            lines.append("\n### Errors")
            for f in fail_files:
                lines.append(f"- `{f['file']}`: {f.get('error', 'unknown error')}")

        await ctx.info(f"instagram_download ✓ — {shortcode}, {len(ok_files)} files, {elapsed:.2f}s")
        return "\n".join(lines)

    descriptors.append(
        ToolDescriptor(
            name="instagram_download",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=dict(_DOWNLOAD_ANNOTATIONS),
            input_model=DownloadInput,
            description_first_line=(
                "🔐 Download all media from an Instagram post to a local directory."
            ),
        )
    )

    return descriptors


__all__ = ["TOOLSET_NAME", "register_upload"]
