"""
CookieManager — Instagram session cookie loader and CSRF token resolver.

Supports two cookie file formats (auto-detected):

  1. Netscape cookies.txt — exported via "Get cookies.txt LOCALLY" extension
     Tab-separated: domain  flag  path  secure  expiry  name  value

  2. JSON array — exported via "EditThisCookie" or "Cookie-Editor" extension
     Each entry: {"name": "...", "value": "...", "domain": "...", ...}
     Save the exported JSON as cookies.json (or cookies.txt — auto-detected).

Flow:
  1. User exports cookies from browser (must be logged in to Instagram)
  2. CookieManager.load() parses the file (format auto-detected)
  3. CookieManager.ensure_csrf_tokens(session) fetches fb_dtsg + lsd from
     instagram.com HTML if needed
  4. Authenticated tools use these tokens in POST bodies

Cookie file locations searched (first found wins):
  - INSTAGRAM_MCP_COOKIES env var  → explicit path
  - ./cookies.json                 → JSON format, cwd
  - ./cookies.txt                  → Netscape format, cwd
  - ../cookies.json                → parent directory
  - ../cookies.txt                 → parent directory
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("instagram_mcp.cookies")

# Regex patterns for CSRF token extraction from Instagram HTML
_FB_DTSG_RE = re.compile(r'"fb_dtsg"\s*:\s*\{"token"\s*:\s*"([^"]+)"')
_LSD_RE = re.compile(r'"lsd"\s*:\s*\{"token"\s*:\s*"([^"]+)"')
# Fallback patterns
_FB_DTSG_ALT_RE = re.compile(r'"DTSGInitData"\s*,\s*\[\]\s*,\s*\{"token"\s*:\s*"([^"]+)"')
_LSD_ALT_RE = re.compile(r'"LSD"\s*,\s*\[\]\s*,\s*\{"token"\s*:\s*"([^"]+)"')

# How long cached CSRF tokens stay valid (seconds) — they rotate periodically
_CSRF_CACHE_TTL = 1800  # 30 minutes


class CookieManager:
    """
    Manages Instagram session cookies and CSRF tokens for authenticated requests.

    Thread-safe: all state is read-only after load(); CSRF tokens are
    refreshed under an asyncio lock.
    """

    def __init__(self, cookies_path: Optional[str] = None) -> None:
        self._path: Optional[Path] = Path(cookies_path) if cookies_path else None
        self._cookies: Dict[str, str] = {}
        self._fb_dtsg: Optional[str] = None
        self._lsd: Optional[str] = None
        self._csrf_fetched_at: float = 0.0
        self._csrf_cache: Optional[Tuple[str, str]] = None  # (fb_dtsg, lsd)
        self._loaded: bool = False

        # asyncio lock — prevents concurrent CSRF refreshes
        import asyncio
        self._csrf_lock = asyncio.Lock()

    # ── Initialisation ───────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        Load cookies from file. Returns True if a valid sessionid was found.

        Searches self._path, then standard fallback paths.
        """
        path = self._resolve_path()
        if path is None:
            logger.debug("No cookies.txt file found — authenticated tools unavailable")
            self._loaded = True
            return False

        try:
            self._cookies = _parse_cookies_file(path)
        except Exception as exc:
            logger.warning("Could not parse cookies file at %s: %s", path, exc)
            self._loaded = True
            return False

        if not self._cookies.get("sessionid"):
            logger.warning(
                "Cookies file loaded from %s but no 'sessionid' found — "
                "are you logged in to Instagram?",
                path,
            )
            self._loaded = True
            return False

        logger.info(
            "Cookies loaded from %s — sessionid present, %d cookies total",
            path,
            len(self._cookies),
        )
        self._loaded = True
        return True

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def is_authenticated(self) -> bool:
        """True if a valid sessionid cookie is loaded."""
        return bool(self._cookies.get("sessionid"))

    @property
    def cookies(self) -> Dict[str, str]:
        """Dict of all loaded cookies (safe copy)."""
        return dict(self._cookies)

    @property
    def fb_dtsg(self) -> Optional[str]:
        return self._fb_dtsg

    @property
    def lsd(self) -> Optional[str]:
        return self._lsd

    def auth_required_error(self) -> str:
        """Friendly error message shown when cookies are missing."""
        return (
            "🔐 **Authentication required** — this tool needs you to be logged in.\n\n"
            "**Setup (one-time) — choose one of these methods:**\n\n"
            "**Method A — JSON format** (EditThisCookie / Cookie-Editor extension):\n"
            "1. Install *EditThisCookie* or *Cookie-Editor* in Chrome/Firefox\n"
            "2. Open https://www.instagram.com and log in\n"
            "3. Click the extension → Export → copies JSON to clipboard\n"
            "4. Paste into a file and save as `cookies.json`\n"
            "5. Place `cookies.json` next to your MCP server\n\n"
            "**Method B — Netscape .txt format** (Get cookies.txt LOCALLY extension):\n"
            "1. Install *Get cookies.txt LOCALLY* in Chrome/Firefox\n"
            "2. Open https://www.instagram.com and log in\n"
            "3. Click the extension → Export → save as `cookies.txt`\n"
            "4. Place `cookies.txt` next to your MCP server\n\n"
            "Or set env var: `INSTAGRAM_MCP_COOKIES=/full/path/to/cookies.json`\n"
            "Then restart the MCP server.\n\n"
            "Anonymous tools (instagram_profile, instagram_feed_deep, etc.) "
            "continue to work without cookies."
        )

    async def ensure_csrf_tokens(self, session) -> Tuple[str, str]:
        """
        Return (fb_dtsg, lsd), refreshing from Instagram if stale.

        *session* is a curl_cffi.requests.AsyncSession with cookies already set.
        """
        async with self._csrf_lock:
            now = time.monotonic()
            age = now - self._csrf_fetched_at

            # If cache is valid and not stale, return it
            if self._csrf_cache and age < _CSRF_CACHE_TTL:
                return self._csrf_cache

            # Refresh needed
            fb_dtsg, lsd = await _fetch_csrf_tokens(session, self._cookies)
            if fb_dtsg and lsd:
                self._fb_dtsg = fb_dtsg
                self._lsd = lsd
                self._csrf_fetched_at = now
                self._csrf_cache = (fb_dtsg, lsd)
                logger.debug("CSRF tokens refreshed (fb_dtsg=%s…)", fb_dtsg[:12])
            else:
                # If we have old tokens and refresh failed, we might want to retry
                # or raise. Here we raise as per existing logic.
                raise RuntimeError(
                    "Could not extract fb_dtsg/lsd from instagram.com — "
                    "session may be expired. Please re-export cookies.txt."
                )

            return self._csrf_cache

    # ── Path resolution ──────────────────────────────────────────────────────

    def _resolve_path(self) -> Optional[Path]:
        """Find the first existing cookies file (JSON or Netscape .txt)."""
        candidates: list[Path] = []
        if self._path:
            candidates.append(self._path)
        # Check both .json and .txt in each directory (JSON first — it's the common export)
        for base in [Path.cwd(), Path.cwd().parent, Path(__file__).parent.parent]:
            candidates.append(base / "cookies.json")
            candidates.append(base / "cookies.txt")
        for p in candidates:
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return p
            except Exception:
                continue
        return None


# ── Module-level helpers ─────────────────────────────────────────────────────

def _parse_cookies_file(path: Path) -> Dict[str, str]:
    """
    Auto-detect cookie file format and parse it.

    Supports:
      - JSON array  — exported by EditThisCookie / Cookie-Editor
      - Netscape    — exported by "Get cookies.txt LOCALLY"
    """
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if raw.startswith("[") or raw.startswith("{"):
        return _parse_json_cookies(raw)
    return _parse_netscape_cookies(raw)


def _parse_json_cookies(raw: str) -> Dict[str, str]:
    """
    Parse JSON cookie export (EditThisCookie / Cookie-Editor format).

    Accepts both an array of cookie objects and a single object.
    Filters to instagram.com domain cookies only.
    """
    data = json.loads(raw)
    if isinstance(data, dict):
        # Single cookie object — wrap in list
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of cookie objects")

    result: Dict[str, str] = {}
    for entry in data:
        try:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "").strip()
            value = entry.get("value", "")
            domain = entry.get("domain", "")
            # Only include instagram.com cookies
            if not name:
                continue
            if domain and "instagram.com" not in domain:
                continue
            result[name] = str(value)
        except Exception as e:
            logger.debug("Skipping malformed cookie entry: %s", e)
            continue

    return result


def _parse_netscape_cookies(raw: str) -> Dict[str, str]:
    """
    Parse a Netscape-format cookies.txt string.

    Format per line (tab-separated):
      domain  flag  path  secure  expiry  name  value
    """
    result: Dict[str, str] = {}
    for line in raw.splitlines():
        try:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            name = parts[5].strip()
            value = parts[6].strip()
            if name:
                result[name] = value
        except Exception as e:
            logger.warning("Skipping malformed cookie line: %s", e)
            continue
    return result


async def _fetch_csrf_tokens(session, cookies: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch instagram.com homepage and extract fb_dtsg + lsd from the HTML.
    Returns (fb_dtsg, lsd) — either may be None on parse failure.
    """
    try:
        resp = await session.get(
            "https://www.instagram.com/",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/142.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15,
        )
        html = resp.text
    except Exception as exc:
        logger.warning("Failed to fetch instagram.com for CSRF: %s", exc)
        return None, None

    fb_dtsg = None
    lsd = None

    for pattern in (_FB_DTSG_RE, _FB_DTSG_ALT_RE):
        m = pattern.search(html)
        if m:
            fb_dtsg = m.group(1)
            break

    for pattern in (_LSD_RE, _LSD_ALT_RE):
        m = pattern.search(html)
        if m:
            lsd = m.group(1)
            break

    if not fb_dtsg or not lsd:
        logger.debug(
            "CSRF extraction: fb_dtsg=%s lsd=%s (HTML len=%d)",
            bool(fb_dtsg), bool(lsd), len(html),
        )

    return fb_dtsg, lsd
