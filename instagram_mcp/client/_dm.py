"""Direct Messages mixin for InstagramClient."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import json as _json

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


class DmMixin:
    """Direct message methods."""

    # ── Direct Messages ──────────────────────────────────────────────────────

    async def fetch_dm_inbox(
        self,
        limit: int = 20,
        cursor: Optional[str] = None,
        cache_ttl: int = 30,
    ) -> Dict[str, Any]:
        """Fetch DM inbox threads (requires cookies)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("DM inbox requires authentication. Set up cookies.txt.")

        cache_key = f"dm_inbox:{cursor or 'first'}:{limit}"

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            csrf = (cm.cookies.get("csrftoken", "")) or ""
            params: Dict[str, str] = {
                "visual_message_return_type": "unseen",
                "direction": "older",
                "limit": str(limit),
            }
            if cursor:
                params["cursor"] = cursor

            resp = await session.get(
                "https://www.instagram.com/api/v1/direct_v2/inbox/",
                params=params,
                headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id},
                allow_redirects=False,
            )
            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                raise FetchError("DM inbox: redirected — session may be expired or rate-limited")
            if status in (401, 403):
                raise FetchError("DM inbox: session expired. Re-export cookies.txt.")
            if status != 200:
                raise FetchError(f"DM inbox: HTTP {status}")
            try:
                body = resp.json()
            except Exception:
                raise FetchError("DM inbox: invalid JSON response")

            inbox = body.get("inbox") or {}
            threads_raw = inbox.get("threads") or []
            threads = []
            for t in threads_raw:
                users = [
                    {
                        "user_id": str(u.get("pk") or u.get("id") or ""),
                        "username": u.get("username", ""),
                        "full_name": u.get("full_name", ""),
                        "is_verified": bool(u.get("is_verified")),
                    }
                    for u in (t.get("users") or [])
                ]
                items_list = t.get("items") or []
                last_item = items_list[0] if items_list else {}
                threads.append({
                    "thread_id": t.get("thread_v2_id") or t.get("thread_id", ""),
                    "thread_title": t.get("thread_title", ""),
                    "is_group": bool(t.get("is_group")),
                    "users": users,
                    "has_unread": t.get("read_state", 0) != 0,
                    "last_activity_at": t.get("last_activity_at", 0),
                    "last_message_type": last_item.get("item_type", ""),
                    "last_message_text": (
                        last_item.get("text", "")
                        if last_item.get("item_type") == "text" else ""
                    ),
                })
            return {
                "threads": threads,
                "has_older": bool(inbox.get("has_older")),
                "oldest_cursor": inbox.get("oldest_cursor", ""),
                "count": len(threads),
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=cache_ttl)


    async def fetch_dm_thread(
        self,
        thread_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
        cache_ttl: int = 30,
    ) -> Dict[str, Any]:
        """Fetch messages in a DM thread with media content, read receipts, and pagination."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("DM thread requires authentication. Set up cookies.txt.")

        cache_key = f"dm_thread:{thread_id}:{cursor or 'first'}:{limit}"

        async def _do_fetch() -> Dict[str, Any]:
            session = await self._get_auth_session()
            csrf = (cm.cookies.get("csrftoken", "")) or ""
            my_user_id = (cm.cookies.get("ds_user_id", "")) or ""
            params: Dict[str, str] = {"limit": str(limit)}
            if cursor:
                params["cursor"] = cursor

            resp = await session.get(
                f"https://www.instagram.com/api/v1/direct_v2/threads/{thread_id}/",
                params=params,
                headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id},
                allow_redirects=False,
            )
            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                raise FetchError("DM thread: redirected — session may be expired or rate-limited")
            if status in (401, 403):
                raise FetchError("DM thread: session expired. Re-export cookies.")
            if status == 404:
                raise FetchError(f"Thread {thread_id!r} not found.")
            if status != 200:
                raise FetchError(f"DM thread: HTTP {status}")
            try:
                body = resp.json()
            except Exception:
                raise FetchError("DM thread: invalid JSON response")

            thread = body.get("thread") or {}

            # ── Read receipts: last seen item_id per user ─────────────────────
            last_seen_at: Dict[str, Any] = thread.get("last_seen_at") or {}
            # Map user_id → last seen item_id (as int for comparison)
            seen_item_ids: Dict[str, int] = {}
            for uid, info in last_seen_at.items():
                raw_iid = info.get("item_id", "")
                try:
                    seen_item_ids[str(uid)] = int(raw_iid)
                except (ValueError, TypeError):
                    pass

            # ── Build username map from participants ──────────────────────────
            users_raw = thread.get("users") or []
            uid_to_username: Dict[str, str] = {}
            for u in users_raw:
                uid = str(u.get("pk") or u.get("id") or "")
                if uid:
                    uid_to_username[uid] = u.get("username", uid)

            # ── Parse messages ────────────────────────────────────────────────
            items_raw = thread.get("items") or []
            messages = []
            for item in items_raw:
                item_id_str = item.get("item_id", "")
                user_id_str = str(item.get("user_id", ""))
                ts = item.get("timestamp", 0)
                itype = item.get("item_type", "")
                is_mine = user_id_str == my_user_id

                # ── Read status ───────────────────────────────────────────────
                # A message is "read" if at least one OTHER participant has seen
                # an item at or after this message's item_id (snowflake ordering).
                try:
                    item_id_int = int(item_id_str)
                except (ValueError, TypeError):
                    item_id_int = 0

                read_by: List[str] = []
                for uid, last_iid in seen_item_ids.items():
                    if uid != user_id_str and last_iid >= item_id_int:
                        read_by.append(uid_to_username.get(uid, uid))

                msg: Dict[str, Any] = {
                    "item_id": item_id_str,
                    "user_id": user_id_str,
                    "username": "me" if is_mine else uid_to_username.get(user_id_str, user_id_str),
                    "timestamp": ts,
                    "item_type": itype,
                    "is_mine": is_mine,
                    "read_by": read_by,
                    "is_read": bool(read_by) if is_mine else True,
                }

                # ── Content by type ───────────────────────────────────────────
                if itype == "text":
                    msg["text"] = item.get("text", "")

                elif itype == "like":
                    msg["text"] = "❤️"

                elif itype == "media_share":
                    shared = item.get("media_share") or {}
                    code = shared.get("code", "")
                    media_type = shared.get("media_type", 1)  # 1=photo,2=video,8=carousel
                    caption_data = shared.get("caption") or {}
                    caption = (
                        caption_data.get("text", "")[:120]
                        if isinstance(caption_data, dict)
                        else str(caption_data)[:120]
                    )
                    candidates = (shared.get("image_versions2") or {}).get("candidates") or []
                    thumb_url = candidates[0].get("url", "") if candidates else ""
                    video_versions = shared.get("video_versions") or []
                    video_url = video_versions[0].get("url", "") if video_versions else ""
                    media_label = {1: "photo", 2: "video", 8: "carousel"}.get(media_type, "media")
                    msg["text"] = f"[shared {media_label}]"
                    msg["media_url"] = f"https://www.instagram.com/p/{code}/" if code else ""
                    msg["thumb_url"] = thumb_url
                    msg["video_url"] = video_url
                    msg["caption"] = caption
                    msg["media_type"] = media_label

                elif itype == "raven_media":
                    vm = item.get("visual_media") or {}
                    media = vm.get("media") or {}
                    candidates = (media.get("image_versions2") or {}).get("candidates") or []
                    thumb_url = candidates[0].get("url", "") if candidates else ""
                    video_versions = media.get("video_versions") or []
                    video_url = video_versions[0].get("url", "") if video_versions else ""
                    media_label = "video" if video_url else "photo"
                    msg["text"] = f"[disappearing {media_label}]"
                    msg["thumb_url"] = thumb_url
                    msg["video_url"] = video_url
                    msg["media_type"] = f"disappearing_{media_label}"

                elif itype == "voice_media":
                    voice = (item.get("voice_media") or {}).get("media") or {}
                    duration_ms = voice.get("audio", {}).get("duration", 0)
                    msg["text"] = f"[voice message {duration_ms // 1000}s]"
                    msg["audio_url"] = (voice.get("audio") or {}).get("audio_src", "")

                elif itype == "animated_media":
                    gif = (item.get("animated_media") or {}).get("images", {})
                    fixed = (gif.get("fixed_height") or {})
                    msg["text"] = "[GIF]"
                    msg["thumb_url"] = fixed.get("url", "")

                elif itype == "story_share":
                    story = item.get("story_share") or {}
                    msg["text"] = "[story reply]"
                    msg["story_username"] = story.get("text", "")

                elif itype == "action_log":
                    log = item.get("action_log") or {}
                    msg["text"] = f"[{log.get('description', 'action')}]"

                elif itype == "placeholder":
                    # Deleted message or automated/bot message with no visible content
                    msg["text"] = "[message unavailable]"

                elif itype == "xma_media_share":
                    xma = item.get("xma_media_share") or {}
                    title = xma.get("title", "")
                    preview = xma.get("preview_url", "")
                    msg["text"] = f"[shared: {title}]" if title else "[shared content]"
                    msg["media_url"] = preview

                elif itype == "link":
                    link = item.get("link") or {}
                    msg["text"] = link.get("text", "[link]")
                    context = link.get("link_context") or {}
                    msg["media_url"] = context.get("link_url", "")

                else:
                    msg["text"] = f"[{itype}]"

                messages.append(msg)

            participants = [
                {
                    "user_id": str(u.get("pk") or u.get("id") or ""),
                    "username": u.get("username", ""),
                    "full_name": u.get("full_name", ""),
                }
                for u in users_raw
            ]

            # Pagination: prev_cursor loads OLDER messages
            prev_cursor = thread.get("prev_cursor", "")
            # MINCURSOR/MAXCURSOR are Instagram's boundary markers — treat as "no more"
            if prev_cursor in ("MINCURSOR", "MAXCURSOR", ""):
                prev_cursor = ""
            has_older = bool(thread.get("has_older")) and bool(prev_cursor)

            return {
                "thread_id": thread_id,
                "thread_title": thread.get("thread_title", ""),
                "is_group": bool(thread.get("is_group")),
                "participants": participants,
                "messages": messages,
                "message_count": len(messages),
                "has_older": has_older,
                "prev_cursor": prev_cursor,
                "oldest_cursor": thread.get("oldest_cursor", ""),
            }

        return await self._cache.get_or_fetch(cache_key, _do_fetch, ttl=cache_ttl)


    async def _fetch_fb_tokens(self) -> Tuple[str, str]:
        """Fetch fb_dtsg and lsd tokens from Instagram homepage.

        If authenticated, uses the current session to get account-linked tokens
        via CookieManager. Otherwise falls back to an anonymous (no-cookie)
        fetch with HTTP/1.1 to force the legacy page format which embeds tokens.
        """
        import re as _re

        # 1. Prefer authenticated tokens if we have a session
        if self._cookie_manager and self._cookie_manager.is_authenticated:
            try:
                session = await self._get_auth_session()
                return await self._cookie_manager.ensure_csrf_tokens(session)
            except Exception as exc:
                logger.warning(
                    "Authenticated CSRF fetch failed (session may be stale), "
                    "falling back to anonymous: %s",
                    exc,
                )

        # 2. Anonymous fallback (for public tools or if auth fetch failed)
        from curl_cffi.requests import AsyncSession as _AsyncSession
        from curl_cffi import CurlHttpVersion as _CurlHttpVersion

        # HTTP/1.1 + no impersonation forces legacy page format
        tmp = _AsyncSession(http_version=_CurlHttpVersion.V1_1)
        try:
            resp = await tmp.get(
                "https://www.instagram.com/",
                headers={
                    "User-Agent": self._config.ig_user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=20,
                allow_redirects=True,
            )
            html = resp.text
        finally:
            await tmp.close()

        m = _re.search(r'"dtsg":\{"token":"([^"]+)"', html)
        if not m:
            m = _re.search(r'"DTSG","[^"]*","([^"]+)"', html)
        if not m:
            raise FetchError(
                "Could not extract fb_dtsg from Instagram web. "
                "Session may be expired — re-export cookies."
            )
        fb_dtsg = m.group(1)
        m2 = _re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
        lsd = m2.group(1) if m2 else fb_dtsg[:16]
        return fb_dtsg, lsd


    async def resolve_dm_thread_igid(self, username: str) -> str:
        """Resolve a username to its DM thread igid (web thread_v2_id format).

        Strategy:
        1. Search existing inbox threads for a matching username.
        2. If not found, get user_id then call get_or_create via www (not mobile).
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        username_lower = username.lower()

        # Step 1: Search inbox for existing thread
        _ck = self._cookie_str()
        inbox_resp = await session.get(
            "https://www.instagram.com/api/v1/direct_v2/inbox/?limit=40",
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id, "Cookie": _ck},
            allow_redirects=False,
        )
        if inbox_resp.status_code == 200:
            try:
                inbox_data = inbox_resp.json()
                threads = (inbox_data.get("inbox") or {}).get("threads") or []
                for t in threads:
                    for u in (t.get("users") or []):
                        if (u.get("username") or "").lower() == username_lower:
                            igid = t.get("thread_v2_id") or t.get("thread_id")
                            if igid:
                                return str(igid)
            except Exception:
                pass

        # Step 2: Get user_id, then get_or_create via www
        resp = await session.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers={"x-ig-app-id": self._config.ig_app_id, "x-csrftoken": csrf, "Cookie": _ck},
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"resolve_dm_thread_igid: redirected (session rate-limited) for '{username}'")
        if resp.status_code != 200:
            raise FetchError(f"Could not fetch profile for '{username}': HTTP {resp.status_code}")
        try:
            pdata = resp.json()
        except Exception:
            raise FetchError(f"Invalid JSON from profile API for '{username}'")
        user = (pdata.get("data") or {}).get("user") or {}
        user_id = user.get("id") or user.get("pk")
        if not user_id:
            raise FetchError(f"User '{username}' not found.")

        tdata = None
        for gc_host in ["https://www.instagram.com", "https://i.instagram.com"]:
            resp2 = await session.post(
                f"{gc_host}/api/v1/direct_v2/threads/get_or_create/",
                data={
                    "recipient_users": f"[[{user_id}]]",
                    "use_unified_inbox": "true",
                },
                headers={
                    "x-csrftoken": csrf,
                    "x-ig-app-id": self._config.ig_app_id,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": _ck,
                    "referer": "https://www.instagram.com/direct/inbox/",
                },
                allow_redirects=False,
            )
            if resp2.status_code in (200, 201):
                try:
                    body_check = resp2.text
                    if not body_check.lstrip().startswith("<"):
                        tdata = resp2.json()
                        if tdata.get("thread") or tdata.get("thread_id"):
                            break
                except Exception:
                    pass
        if tdata is None:
            raise FetchError(
                f"Could not find/create DM thread for '{username}': HTTP {resp2.status_code}"
            )
        thread = tdata.get("thread") or {}
        igid = thread.get("thread_v2_id") or thread.get("thread_id")
        if not igid:
            raise FetchError(f"Could not resolve thread igid for '{username}'")
        return str(igid)


    async def send_dm_text(self, thread_id: str, text: str) -> Dict[str, Any]:
        """Send a text message via Instagram Web GraphQL (IGDirectTextSendMutation)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("Send DM requires authentication. Set up cookies.txt.")
        if not text.strip():
            raise FetchError("Message text cannot be empty.")
        if len(text) > 1000:
            raise FetchError("Message too long (max 1000 chars).")

        csrf = (cm.cookies.get("csrftoken", "")) or ""
        ds_user_id = (cm.cookies.get("ds_user_id", "0")) or "0"

        last_error = "unknown error"
        for attempt in range(3):
            if attempt > 0:
                await asyncio.sleep(attempt * 4)
                cm.invalidate_csrf_cache()
                logger.debug("send_dm_text retry %d/3", attempt + 1)

            try:
                fb_dtsg, lsd = await self._fetch_fb_tokens()
            except Exception as exc:
                last_error = str(exc)
                logger.warning("send_dm_text: token fetch failed (attempt %d): %s", attempt + 1, exc)
                continue

            offline_id = str(int(time.time() * 1000) * (2 ** 22) + random.randint(0, (2 ** 22) - 1))
            variables = {
                "ig_thread_igid": thread_id,
                "offline_threading_id": offline_id,
                "recipient_igids": None,
                "replied_to_client_context": None,
                "replied_to_item_id": None,
                "reply_to_message_id": None,
                "sampled": None,
                "text": {"sensitive_string_value": text},
                "mentions": [],
                "mentioned_user_ids": [],
                "commands": None,
                "forwarded_from_thread_id": None,
                "is_forwarded_from_own_message": None,
                "send_attribution": "igd_web_chat_tab:in_thread",
            }
            data = {
                "av": ds_user_id,
                "__d": "www",
                "__user": ds_user_id,
                "__a": "1",
                "__req": str(random.randint(10, 99)),
                "dpr": "1",
                "__ccg": "GOOD",
                "fb_dtsg": fb_dtsg,
                "jazoest": "2" + str(sum(ord(c) for c in fb_dtsg)),
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "IGDirectTextSendMutation",
                "server_timestamps": "true",
                "variables": _json.dumps(variables),
                "doc_id": "26911679871773184",
            }

            session = await self._get_auth_session()
            resp = await session.post(
                "https://www.instagram.com/api/graphql",
                data=data,
                headers={
                    "x-csrftoken": csrf,
                    "x-fb-friendly-name": "IGDirectTextSendMutation",
                    "x-fb-lsd": lsd,
                    "x-ig-app-id": self._config.ig_app_id,
                    "x-ig-www-claim": "0",
                    "x-asbd-id": "129477",
                    "x-instagram-ajax": "1",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://www.instagram.com/direct/t/{thread_id}/",
                    "Origin": "https://www.instagram.com",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                },
                allow_redirects=False,
            )

            body_text = resp.text
            if body_text.startswith("for (;;);"):
                body_text = body_text[9:]

            if resp.status_code in (301, 302, 303, 307, 308):
                last_error = "GraphQL redirected (session rate-limited)"
                cm.invalidate_csrf_cache()
                continue
            if resp.status_code in (401, 403):
                raise FetchError("Send DM: session expired. Re-export cookies.")
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue

            try:
                body = _json.loads(body_text)
            except Exception:
                last_error = f"invalid JSON: {body_text[:200]}"
                continue

            # Instagram bot-detection: session flagged — invalidate tokens and retry
            if "1357001" in body_text or '"not-logged-in"' in body_text:
                last_error = "session flagged by Instagram (error 1357001)"
                cm.invalidate_csrf_cache()
                logger.warning("send_dm_text: error 1357001 on attempt %d, retrying", attempt + 1)
                continue

            if "error" in body:
                err_val = body.get("error")
                err_code = err_val.get("code") if isinstance(err_val, dict) else None
                if err_code == 1357001:
                    last_error = "session flagged (1357001)"
                    cm.invalidate_csrf_cache()
                    continue
                raise FetchError(
                    f"Send DM failed: {body.get('errorSummary', err_val)}"
                )

            inner = ((body.get("data") or {}).get(
                "xig_direct_text_send_with_slide_messaging_response"
            ) or {})
            msg_id = inner.get("message_id", "")
            if not msg_id:
                raise FetchError(f"Send DM: no message_id in response: {body_text[:300]}")

            return {
                "status": "sent",
                "item_id": msg_id,
                "timestamp": int(inner.get("timestamp_ms", 0)),
                "thread_id": thread_id,
            }

        raise FetchError(f"Send DM failed after 3 attempts: {last_error}")


    async def send_dm_to_username(self, username: str, text: str) -> Dict[str, Any]:
        """Resolve username → thread igid, then send DM via GraphQL."""
        username = username.lstrip("@")
        thread_igid = await self.resolve_dm_thread_igid(username)
        result = await self.send_dm_text(thread_igid, text)
        result["username"] = username
        return result


    async def _gql_mutation(
        self,
        doc_id: str,
        variables: Dict[str, Any],
        friendly_name: str,
        fb_dtsg: str,
        lsd: str,
    ) -> Dict[str, Any]:
        """Generic GraphQL mutation via Instagram web API."""
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        ds_user_id = (cm.cookies.get("ds_user_id", "0") if cm else "0") or "0"
        session = await self._get_auth_session()
        data = {
            "av": ds_user_id, "__d": "www", "__user": ds_user_id, "__a": "1",
            "fb_dtsg": fb_dtsg, "lsd": lsd,
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": friendly_name,
            "server_timestamps": "true",
            "variables": _json.dumps(variables),
            "doc_id": doc_id,
        }
        resp = await session.post(
            "https://www.instagram.com/api/graphql",
            data=data,
            headers={
                "x-csrftoken": csrf, "x-fb-lsd": lsd,
                "x-fb-friendly-name": friendly_name,
                "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/direct/inbox/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(
                f"{friendly_name}: GraphQL redirected (session may be rate-limited). "
                f"Location: {resp.headers.get('location', 'unknown')}"
            )
        body = resp.text
        if body.startswith("for (;;);"):
            body = body[9:]
        if resp.status_code not in (200, 201):
            raise FetchError(f"{friendly_name}: HTTP {resp.status_code}")
        try:
            return _json.loads(body)
        except Exception:
            raise FetchError(f"{friendly_name}: invalid JSON: {body[:200]}")


    async def _gql_mutation_with_retry(
        self,
        doc_id: str,
        variables: Dict[str, Any],
        friendly_name: str,
    ) -> Dict[str, Any]:
        """Fetch CSRF tokens and run a GraphQL mutation, retrying on session-flagged errors."""
        cm = self._cookie_manager
        last_error = "unknown"
        for attempt in range(3):
            if attempt > 0:
                await asyncio.sleep(attempt * 4)
                cm.invalidate_csrf_cache()
                logger.debug("%s retry %d/3", friendly_name, attempt + 1)
            try:
                fb_dtsg, lsd = await self._fetch_fb_tokens()
            except Exception as exc:
                last_error = str(exc)
                continue
            try:
                body = await self._gql_mutation(doc_id, variables, friendly_name, fb_dtsg, lsd)
            except FetchError as exc:
                last_error = str(exc)
                if "1357001" in last_error or "redirected" in last_error:
                    cm.invalidate_csrf_cache()
                    continue
                raise
            body_str = str(body)
            if "1357001" in body_str or '"not-logged-in"' in body_str:
                last_error = "session flagged (1357001)"
                cm.invalidate_csrf_cache()
                continue
            return body
        raise FetchError(f"{friendly_name} failed after 3 attempts: {last_error}")


    async def dm_react(self, thread_id: str, item_id: str, emoji: str = "❤") -> Dict[str, Any]:
        """React to a DM message with an emoji (default: ❤)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_react requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="3672524849516997",
            variables={"thread_id": thread_id, "item_id": item_id, "reaction": emoji},
            friendly_name="IGDirectSendEmojiReactionMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_react failed: {body}")
        return {"status": "reacted", "thread_id": thread_id, "item_id": item_id, "emoji": emoji}


    async def dm_unreact(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        """Remove emoji reaction from a DM message."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_unreact requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="3672524849516997",
            variables={"thread_id": thread_id, "item_id": item_id, "reaction": ""},
            friendly_name="IGDirectSendEmojiReactionMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_unreact failed: {body}")
        return {"status": "unreacted", "thread_id": thread_id, "item_id": item_id}


    async def dm_unsend(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        """Delete/unsend a DM message."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_unsend requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="7166420300085783",
            variables={"thread_id": thread_id, "item_id": item_id},
            friendly_name="IGDirectDeleteItemMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_unsend failed: {body}")
        return {"status": "deleted", "thread_id": thread_id, "item_id": item_id}


    async def dm_mark_seen(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        """Mark a DM thread as seen up to the given item_id."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_mark_seen requires authentication.")
        body = await self._gql_mutation_with_retry(
            doc_id="5994298984009617",
            variables={"thread_id": thread_id, "last_seen_at": item_id},
            friendly_name="IGDirectMarkThreadSeenMutation",
        )
        if body.get("error"):
            raise FetchError(f"dm_mark_seen failed: {body}")
        return {"status": "seen", "thread_id": thread_id, "item_id": item_id}

    # ── P1: New DM methods ────────────────────────────────────────────────────


    def _auth_headers(self, csrf: str, content_type: str = "application/x-www-form-urlencoded") -> Dict[str, str]:
        """Standard authenticated request headers."""
        return {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "content-type": content_type,
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }


    async def _auth_post(self, url: str, data: Any, csrf: str, session: Any, method_name: str) -> Dict[str, Any]:
        """POST to an authenticated Instagram API endpoint and return parsed JSON."""
        resp = await session.post(url, data=data, headers=self._auth_headers(csrf), allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"{method_name}: redirected to login (session rate-limited or expired)")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"{method_name}: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"{method_name}: got HTML (session blocked): {body_text[:150]}")
        try:
            return _json.loads(body_text)
        except Exception:
            raise FetchError(f"{method_name}: invalid JSON: {body_text[:200]}")


    async def _auth_get(self, url: str, params: Dict[str, str], csrf: str, session: Any, method_name: str) -> Dict[str, Any]:
        """GET from an authenticated Instagram API endpoint and return parsed JSON."""
        resp = await session.get(url, params=params, headers=self._auth_headers(csrf), allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError(f"{method_name}: redirected to login (session rate-limited or expired)")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"{method_name}: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"{method_name}: got HTML (session blocked): {body_text[:150]}")
        try:
            return _json.loads(body_text)
        except Exception:
            raise FetchError(f"{method_name}: invalid JSON: {body_text[:200]}")


    async def _require_auth(self, method_name: str):
        """Raise FetchError if not authenticated. Returns (cm, session, csrf)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError(f"{method_name} requires authentication. Set up cookies.txt.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        return cm, session, csrf


    async def dm_send_photo(
        self,
        photo_path: str,
        thread_id: Optional[str] = None,
        username: Optional[str] = None,
        caption: str = "",
    ) -> Dict[str, Any]:
        """Send a photo as a Direct Message — upload via rupload then broadcast."""
        cm, session, csrf = await self._require_auth("dm_send_photo")
        cookie_header = self._cookie_str()

        if not thread_id and username:
            thread_id = await self.resolve_dm_thread_igid(username.lstrip("@"))
        if not thread_id:
            raise FetchError("dm_send_photo: provide thread_id or username")

        # Step 1: upload image via rupload (same as story/feed upload)
        upload_id, _w, _h = await self._upload_single_image(
            session, csrf, cookie_header, photo_path, is_sidecar=False
        )

        # Step 2: broadcast the uploaded photo to the DM thread
        uid = (cm.cookies.get("ds_user_id", "") if cm else "") or ""
        device_id = (cm.cookies.get("ig_did", "") if cm else "") or ""
        offline_id = str(int(time.time() * 1000)) + str(random.randint(100, 999))

        data: Dict[str, str] = {
            "upload_id": upload_id,
            "thread_ids": _json.dumps([thread_id]),
            "recipient_users": "[]",
            "offline_threading_id": offline_id,
        }
        if uid:
            data["_uid"] = uid
        if device_id:
            data["_uuid"] = device_id
        if caption:
            data["caption"] = caption

        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/direct/inbox/",
        }

        resp = await session.post(
            "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/photo/",
            data=data,
            headers=headers,
            allow_redirects=False,
        )
        body_text = resp.text
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("dm_send_photo: redirected (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"dm_send_photo: HTTP {resp.status_code}: {body_text[:300]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"dm_send_photo: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"dm_send_photo: API error: {body.get('message', 'unknown')}")
        payload = (body.get("payload") or {})
        item_id = payload.get("item_id", "")
        return {"status": "sent", "item_id": item_id, "thread_id": thread_id, "upload_id": upload_id}


    async def dm_send_video(
        self,
        video_path: str,
        thread_id: Optional[str] = None,
        username: Optional[str] = None,
        thumbnail_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a video as a Direct Message (upload then broadcast)."""
        import os as _os
        cm, session, csrf = await self._require_auth("dm_send_video")

        if not thread_id and username:
            thread_id = await self.resolve_dm_thread_igid(username.lstrip("@"))
        if not thread_id:
            raise FetchError("dm_send_video: provide thread_id or username")

        cookie_header = self._cookie_str()
        upload_id, duration = await self._upload_video(session, csrf, cookie_header, video_path, is_reel=False)

        # Optional thumbnail upload
        sampled_image_upload_id = ""
        if thumbnail_path and _os.path.isfile(thumbnail_path):
            try:
                sampled_image_upload_id, _, _ = await self._upload_single_image(
                    session, csrf, cookie_header, thumbnail_path, is_sidecar=False
                )
            except Exception:
                pass

        payload: Dict[str, Any] = {
            "upload_id": upload_id,
            "thread_ids": _json.dumps([thread_id]),
            "recipient_users": _json.dumps([]),
            "video_result": "",
        }
        if sampled_image_upload_id:
            payload["sampled_image_upload_id"] = sampled_image_upload_id

        body = await self._auth_post(
            "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/video/",
            payload,
            csrf,
            session,
            "dm_send_video",
        )
        if body.get("status") == "fail":
            raise FetchError(f"dm_send_video: API error: {body.get('message', 'unknown')}")
        payload_resp = body.get("payload") or {}
        item_id = payload_resp.get("item_id", "")
        return {"status": "sent", "item_id": item_id, "thread_id": thread_id, "upload_id": upload_id}


    async def dm_mute(self, thread_id: str, mute: bool = True) -> Dict[str, Any]:
        """Mute or unmute a DM thread."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_mute requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        endpoint = "mute" if mute else "unmute"
        resp = await session.post(
            f"https://www.instagram.com/api/v1/direct_v2/threads/{thread_id}/{endpoint}/",
            data={},
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Cookie": self._cookie_str()},
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("dm_mute: redirected (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"dm_mute: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"dm_mute: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"dm_mute: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"dm_mute: {body.get('message', 'unknown error')}")
        return {"status": "muted" if mute else "unmuted", "thread_id": thread_id}


    async def dm_share_post(
        self,
        media_id: str,
        thread_id: Optional[str] = None,
        username: Optional[str] = None,
        text: str = "",
    ) -> Dict[str, Any]:
        """Share an Instagram post to a DM thread or user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("dm_share_post requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""

        if not thread_id and username:
            thread_id = await self.resolve_dm_thread_igid(username.lstrip("@"))
        if not thread_id:
            raise FetchError("dm_share_post: provide thread_id or username")

        data: Dict[str, Any] = {
            "media_id": media_id,
            "thread_ids": _json.dumps([thread_id]),
            "recipient_users": _json.dumps([]),
        }
        if text:
            data["text"] = text

        resp = await session.post(
            "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/media_share/",
            data=data,
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Cookie": self._cookie_str()},
            allow_redirects=False,
        )
        if resp.status_code not in (200, 201):
            raise FetchError(f"dm_share_post: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except Exception:
            raise FetchError(f"dm_share_post: invalid JSON: {resp.text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"dm_share_post: {body.get('message', 'unknown error')}")
        payload = body.get("payload") or {}
        return {
            "status": "shared",
            "thread_id": thread_id,
            "media_id": media_id,
            "item_id": payload.get("item_id", ""),
        }


    async def dm_media_messages(self, thread_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """List all media messages (photos, videos, shared posts) in a DM thread."""
        data = await self.fetch_dm_thread(thread_id, limit=limit)
        media_types = {"media", "reel_share", "clip", "felix_share", "xma_media_share", "animated_media", "voice_media"}
        messages = data.get("messages", [])
        return [m for m in messages if m.get("type") in media_types]

    # ── P2: Comment methods ───────────────────────────────────────────────────

