import hashlib
import logging
import anyio
from pathlib import Path
from typing import Optional
from curl_cffi.requests import AsyncSession
from ._path_guard import ensure_path

logger = logging.getLogger("instagram_mcp.media_cache")

class MediaCache:
    """Caches Instagram media (images/videos) locally to avoid CDN URL expiration."""
    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir:
            cache_dir = ensure_path(cache_dir, name="media_cache_dir")
        self.cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / "data" / "media_cache"
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create media cache directory at %s: %s", self.cache_dir, e)

    def _get_cache_path(self, url: str) -> Path:
        # Determine file extension
        ext = ".jpg"
        if ".mp4" in url.lower():
            ext = ".mp4"
        elif ".webp" in url.lower():
            ext = ".webp"

        # Create a unique hash of the URL
        hasher = hashlib.sha256()
        hasher.update(url.encode("utf-8"))
        filename = f"{hasher.hexdigest()}{ext}"
        return self.cache_dir / filename

    async def get_or_fetch(self, url: str, session: AsyncSession) -> str:
        """Get local file URL if cached, otherwise download and cache the media."""
        if not url or not url.startswith("http"):
            return url

        # Strip URL parameters to keep cache stable if query changes but media is same
        clean_url = url.split("?")[0]
        cache_path = self._get_cache_path(clean_url)
        file_uri = cache_path.as_uri()

        # If already cached, return immediately
        if cache_path.is_file():
            logger.debug("Media cache hit: %s -> %s", url, file_uri)
            return file_uri

        # Fetch and cache
        logger.info("Media cache miss. Fetching media: %s", url)
        try:
            resp = await session.get(url, timeout=20)
            if resp.status_code == 200:
                # Write file asynchronously using anyio
                await anyio.Path(cache_path).write_bytes(resp.content)
                logger.info("Successfully cached media to %s", file_uri)
                return file_uri
            else:
                logger.warning("Failed to fetch media from CDN: HTTP %d", resp.status_code)
        except Exception as e:
            logger.error("Exception fetching media from CDN: %s", e)

        # Fallback to original CDN URL on failure
        return url
