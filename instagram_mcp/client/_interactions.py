"""Interactions mixin for InstagramClient (likes, follows, comments, etc.)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import json as _json

from ..exceptions import FetchError

logger = logging.getLogger("instagram_mcp.client")


class InteractionsMixin:
    """User interaction methods (likes, follows, comments, etc.)."""

    # ── Interactions ─────────────────────────────────────────────────────────

    async def like_post(self, media_id: str, action: str = "like") -> Dict[str, Any]:
        """Like or unlike an Instagram post via /api/v1/web/likes/."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("like_post requires authentication.")
        action = action.lower().strip()
        if action not in ("like", "unlike"):
            raise FetchError(f"like_post: action must be 'like' or 'unlike', got '{action}'")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/likes/{media_id}/{action}/",
            data={},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("like_post: redirected (session rate-limited or expired)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"like_post: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"like_post: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"like_post: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"like_post: API error: {body.get('message', 'unknown')}")
        return {"status": action + "d", "media_id": media_id}


    async def follow_user(self, user_id: str, action: str = "follow") -> Dict[str, Any]:
        """Follow or unfollow an Instagram user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("follow_user requires authentication.")
        action = action.lower().strip()
        if action not in ("follow", "unfollow"):
            raise FetchError(f"follow_user: action must be 'follow' or 'unfollow', got '{action}'")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        endpoint = "create" if action == "follow" else "destroy"
        headers = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
            "content-type": "application/x-www-form-urlencoded",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        resp = await session.post(
            f"https://www.instagram.com/api/v1/friendships/{endpoint}/{user_id}/",
            data={"user_id": user_id},
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("follow_user: redirected (session rate-limited or expired)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"follow_user: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"follow_user: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"follow_user: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"follow_user: API error: {body.get('message', 'unknown')}")
        fs = body.get("friendship_status") or {}
        return {
            "status": action + "ed",
            "user_id": user_id,
            "following": bool(fs.get("following")),
            "is_private": bool(fs.get("is_private")),
            "outgoing_request": bool(fs.get("outgoing_request")),
        }

    # ── Broadcast Channels ────────────────────────────────────────────────────


    async def block_user(self, user_id: str) -> Dict[str, Any]:
        """Block an Instagram user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("block_user requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/friendships/{user_id}/block/",
            data={"user_id": user_id},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("block_user: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"block_user: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"block_user: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"block_user: invalid JSON: {body_text[:200]}")
        fs = body.get("friendship_status") or {}
        return {
            "status": "blocked",
            "user_id": user_id,
            "blocking": bool(fs.get("blocking")),
        }


    async def unblock_user(self, user_id: str) -> Dict[str, Any]:
        """Unblock an Instagram user."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("unblock_user requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/friendships/{user_id}/unblock/",
            data={"user_id": user_id},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("unblock_user: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"unblock_user: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"unblock_user: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"unblock_user: invalid JSON: {body_text[:200]}")
        fs = body.get("friendship_status") or {}
        return {
            "status": "unblocked",
            "user_id": user_id,
            "blocking": bool(fs.get("blocking")),
        }


    async def post_comment(self, media_id: str, text: str) -> Dict[str, Any]:
        """Post a comment on an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("post_comment requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        _base_hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        # Try www endpoint first
        resp = await session.post(
            f"https://www.instagram.com/api/v1/media/{media_id}/comment/",
            data={
                "comment_text": text,
                "idempotence_token": str(int(time.time() * 1000)),
            },
            headers=_base_hdrs,
            allow_redirects=False,
        )
        body_text = resp.text
        if resp.status_code in (301, 302, 303, 307, 308) or resp.status_code not in (200, 201) or body_text.startswith("<!"):
            # Fall back to i.instagram.com
            resp = await session.post(
                f"https://i.instagram.com/api/v1/media/{media_id}/comment/",
                data={
                    "comment_text": text,
                    "idempotence_token": str(int(time.time() * 1000)),
                },
                headers=_base_hdrs,
                allow_redirects=False,
            )
            body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"post_comment: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"post_comment: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"post_comment: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"post_comment: API error: {body.get('message', 'unknown')}")
        comment = body.get("comment") or {}
        comment_id = str(comment.get("pk") or comment.get("id") or "")
        return {
            "status": "commented",
            "comment_id": comment_id,
            "text": text,
            "media_id": media_id,
        }


    async def delete_comment(self, media_id: str, comment_id: str) -> Dict[str, Any]:
        """Delete a comment on an Instagram post (own comment or on own post)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("delete_comment requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        headers = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/media/{media_id}/comment/{comment_id}/delete/",
                data={"comment_or_caption": "0"},
                headers=headers,
                allow_redirects=False,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                continue
            body_text = resp.text
            if resp.status_code not in (200, 201):
                raise FetchError(f"delete_comment: HTTP {resp.status_code}: {body_text[:200]}")
            if body_text.lstrip().startswith("<"):
                raise FetchError(f"delete_comment: got HTML (session blocked): {body_text[:150]}")
            try:
                body = _json.loads(body_text)
            except Exception:
                raise FetchError(f"delete_comment: invalid JSON: {body_text[:200]}")
            if body.get("status") == "fail":
                raise FetchError(f"delete_comment: API error: {body.get('message', 'unknown')}")
            return {"status": "deleted", "comment_id": comment_id, "media_id": media_id}
        raise FetchError("delete_comment: all hosts redirected (session rate-limited)")


    async def comment_reply(self, media_id: str, comment_id: str, text: str) -> Dict[str, Any]:
        """Reply to a specific comment on an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("comment_reply requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        post_data = {
            "comment_text": text,
            "replied_to_comment_id": comment_id,
            "idempotence_token": str(int(time.time() * 1000)),
        }
        body_text = ""
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/media/{media_id}/comment/",
                data=post_data, headers=hdrs, allow_redirects=False,
            )
            body_text = resp.text
            if resp.status_code in (301, 302, 303, 307, 308) or body_text.lstrip().startswith("<"):
                continue
            break
        if resp.status_code not in (200, 201):
            raise FetchError(f"comment_reply: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("comment_reply: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"comment_reply: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"comment_reply: {body.get('message', 'unknown error')}")
        comment = body.get("comment") or {}
        return {
            "status": "replied",
            "comment_id": str(comment.get("pk") or comment.get("id") or ""),
            "replied_to": comment_id,
            "text": text,
            "media_id": media_id,
        }


    async def comment_like(self, comment_id: str, action: str = "like") -> Dict[str, Any]:
        """Like or unlike a comment on an Instagram post."""
        action = action.lower().strip()
        if action not in ("like", "unlike"):
            raise FetchError(f"comment_like: action must be 'like' or 'unlike', got '{action}'")
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("comment_like requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        endpoint = "comment_like" if action == "like" else "comment_unlike"
        hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        body_text = ""
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/media/{comment_id}/{endpoint}/",
                data={}, headers=hdrs, allow_redirects=False,
            )
            body_text = resp.text
            if resp.status_code in (301, 302, 303, 307, 308) or body_text.lstrip().startswith("<"):
                continue
            break
        if resp.status_code not in (200, 201):
            raise FetchError(f"comment_like: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("comment_like: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"comment_like: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"comment_like: {body.get('message', 'unknown error')}")
        return {"status": action + "d", "comment_id": comment_id}


    async def comment_hide(self, comment_id: str, hide: bool = True) -> Dict[str, Any]:
        """Hide or unhide a comment on your post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("comment_hide requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        endpoint = "flag_comment" if hide else "unflag_comment"
        hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        body_text = ""
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/media/{comment_id}/{endpoint}/",
                data={}, headers=hdrs, allow_redirects=False,
            )
            body_text = resp.text
            if resp.status_code in (301, 302, 303, 307, 308) or body_text.lstrip().startswith("<"):
                continue
            break
        if resp.status_code not in (200, 201):
            raise FetchError(f"comment_hide: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("comment_hide: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"comment_hide: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"comment_hide: {body.get('message', 'unknown error')}")
        return {"status": "hidden" if hide else "visible", "comment_id": comment_id}

    # ── P3: Post management methods ───────────────────────────────────────────


    async def post_delete(self, media_id: str) -> Dict[str, Any]:
        """Permanently delete one of your own Instagram posts."""
        _, session, csrf = await self._require_auth("post_delete")
        # Probe media info before deletion (best-effort; result is not required).
        try:
            await self._auth_get(
                f"https://www.instagram.com/api/v1/media/{media_id}/info/",
                {}, csrf, session, "post_delete_info",
            )
        except Exception:
            pass

        body = await self._auth_post(
            f"https://www.instagram.com/api/v1/media/{media_id}/delete/",
            {"media_id": media_id},
            csrf, session, "post_delete",
        )
        if body.get("status") == "fail":
            raise FetchError(f"post_delete: {body.get('message', 'unknown error')}")
        return {"status": "deleted", "media_id": media_id}


    async def toggle_comments(self, media_id: str, enabled: bool = True) -> Dict[str, Any]:
        """Enable or disable comments on one of your Instagram posts."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("toggle_comments requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        endpoint = "enable_comments" if enabled else "disable_comments"
        hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        body_text = ""
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/media/{media_id}/{endpoint}/",
                data={}, headers=hdrs, allow_redirects=False,
            )
            body_text = resp.text
            if resp.status_code in (301, 302, 303, 307, 308) or body_text.lstrip().startswith("<"):
                continue
            break
        if resp.status_code not in (200, 201):
            raise FetchError(f"toggle_comments: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("toggle_comments: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"toggle_comments: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"toggle_comments: {body.get('message', 'unknown error')}")
        return {"status": "enabled" if enabled else "disabled", "media_id": media_id}


    async def media_insights(self, media_id: str) -> Dict[str, Any]:
        """Get performance insights for one of your Instagram posts."""
        cm, session, csrf = await self._require_auth("media_insights")
        hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "Cookie": self._cookie_str(),
        }
        body_text = ""
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.get(
                f"{host}/api/v1/insights/media_organic_insights/{media_id}/",
                params={"ig_app_id": self._config.ig_app_id},
                headers=hdrs, allow_redirects=False,
            )
            body_text = resp.text
            if resp.status_code in (301, 302, 303, 307, 308) or body_text.lstrip().startswith("<"):
                continue
            break
        if resp.status_code not in (200, 201):
            raise FetchError(f"media_insights: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("media_insights: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"media_insights: invalid JSON: {body_text[:200]}")
        # Parse organic_insights node
        insights_node = (
            body.get("media_organic_insights")
            or body.get("inline_insights_node")
            or body
        )
        metrics = {}
        for key in ("reach", "impressions", "saved", "likes", "comments", "shares", "profile_visits", "plays"):
            val = insights_node.get(key) or insights_node.get(f"total_{key}")
            if val is not None:
                metrics[key] = val
        # Also try inline_insights_node for business accounts
        inline = body.get("inline_insights_node") or {}
        metrics_arr = inline.get("metrics") or []
        for m in metrics_arr:
            name = m.get("name", "").lower()
            value = m.get("value")
            if name and value is not None:
                metrics[name] = value
        return {"media_id": media_id, "insights": metrics, "raw": insights_node}


    async def post_save(self, media_id: str) -> Dict[str, Any]:
        """Save/bookmark an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("post_save requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/save/{media_id}/save/",
            data={},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("post_save: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"post_save: HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.text
        if body.lstrip().startswith("<"):
            raise FetchError(f"post_save: got HTML (session may be blocked): {body[:150]}")
        return {"status": "saved", "media_id": media_id}


    async def post_unsave(self, media_id: str) -> Dict[str, Any]:
        """Unsave/unbookmark an Instagram post."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("post_unsave requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        resp = await session.post(
            f"https://www.instagram.com/api/v1/web/save/{media_id}/unsave/",
            data={},
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("post_unsave: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"post_unsave: HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.text
        if body.lstrip().startswith("<"):
            raise FetchError(f"post_unsave: got HTML (session may be blocked): {body[:150]}")
        return {"status": "unsaved", "media_id": media_id}


    async def account_privacy(self, is_private: bool) -> Dict[str, Any]:
        """Toggle account between private and public mode."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("account_privacy requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        uid = (cm.cookies.get("ds_user_id", "")) or ""
        endpoint = "set_private" if is_private else "set_public"
        hdrs = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json, */*",
            "referer": "https://www.instagram.com/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        body_text = ""
        for host in ("https://www.instagram.com", "https://i.instagram.com"):
            resp = await session.post(
                f"{host}/api/v1/accounts/{endpoint}/",
                data={"_csrftoken": csrf, "_uid": uid, "_uuid": (cm.cookies.get("ig_did") or "")},
                headers=hdrs, allow_redirects=False,
            )
            body_text = resp.text
            if resp.status_code in (301, 302, 303, 307, 308) or body_text.lstrip().startswith("<"):
                continue
            break
        if resp.status_code not in (200, 201):
            raise FetchError(f"account_privacy: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError("account_privacy: got HTML (session blocked)")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"account_privacy: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"account_privacy: {body.get('message', 'unknown error')}")
        return {"status": "private" if is_private else "public"}


    async def story_mark_seen(
        self,
        reel_media_ids: List[str],
        reel_media_owner_ids: List[str],
        reel_media_taken_at: List[int],
    ) -> Dict[str, Any]:
        """Mark stories as seen."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("story_mark_seen requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""
        reels_seen: Dict[str, Any] = {}
        for mid, oid, ts in zip(reel_media_ids, reel_media_owner_ids, reel_media_taken_at):
            reels_seen[f"{oid}_{mid}"] = {
                "media_id": mid, "owner_id": oid, "taken_at": ts,
                "seen_at": int(time.time()), "source": "feed",
            }
        resp = await session.post(
            "https://i.instagram.com/api/v1/media/seen/",
            data={
                "reels": _json.dumps(reels_seen),
                "live_vods_skipped": "{}",
                "nuxes_skipped": "{}",
            },
            headers={
                "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
                "content-type": "application/x-www-form-urlencoded",
                "referer": "https://www.instagram.com/",
                "origin": "https://www.instagram.com",
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
        )
        body = resp.text
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("story_mark_seen: redirected (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"story_mark_seen: HTTP {resp.status_code}: {body[:200]}")
        if body.lstrip().startswith("<"):
            raise FetchError(f"story_mark_seen: got HTML (session blocked): {body[:150]}")
        return {"status": "seen", "count": len(reel_media_ids)}


    async def story_reply(self, story_owner_username: str, text: str) -> Dict[str, Any]:
        """Reply to a story by sending a DM to the story owner."""
        return await self.send_dm_to_username(story_owner_username, text)


    async def edit_profile(
        self,
        biography: Optional[str] = None,
        full_name: Optional[str] = None,
        external_url: Optional[str] = None,
        email: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit the authenticated user's profile (bio, name, URL)."""
        cm = self._cookie_manager
        if not cm or not getattr(cm, "is_authenticated", False):
            raise FetchError("edit_profile requires authentication.")
        session = await self._get_auth_session()
        csrf = (cm.cookies.get("csrftoken", "")) or ""

        # Get current profile first — abort if fetch fails to avoid wiping existing fields
        my_id = (cm.cookies.get("ds_user_id", "")) or ""
        info_resp = await session.get(
            f"https://www.instagram.com/api/v1/users/{my_id}/info/",
            headers={"x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile, "Cookie": self._cookie_str()},
            allow_redirects=False,
        )
        current: Dict[str, Any] = {}
        if info_resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("edit_profile: session redirected — cannot fetch current profile to preserve fields")
        if info_resp.status_code == 200 and not info_resp.text.lstrip().startswith("<"):
            try:
                current = info_resp.json().get("user") or {}
            except Exception:
                pass

        data: Dict[str, str] = {
            "biography": biography if biography is not None else (current.get("biography") or ""),
            "full_name": full_name if full_name is not None else (current.get("full_name") or ""),
            "external_url": external_url if external_url is not None else (current.get("external_url") or ""),
            "email": email if email is not None else (current.get("email") or ""),
            "phone_number": phone_number if phone_number is not None else (current.get("phone_number") or ""),
            "username": current.get("username", ""),
            "first_name": _fn.split()[0] if (_fn := (full_name or current.get("full_name") or "")).strip() else "",
        }

        _ep_headers = {
            "x-csrftoken": csrf, "x-ig-app-id": self._config.ig_app_id_mobile,
            "content-type": "application/x-www-form-urlencoded",
            "referer": "https://www.instagram.com/accounts/edit/",
            "origin": "https://www.instagram.com",
            "Cookie": self._cookie_str(),
        }
        # Try web-specific endpoint first, then mobile fallback
        for _ep_url in [
            "https://www.instagram.com/api/v1/web/accounts/edit/",
            "https://i.instagram.com/api/v1/accounts/edit/",
            "https://www.instagram.com/api/v1/accounts/edit/",
        ]:
            resp = await session.post(_ep_url, data=data, headers=_ep_headers, allow_redirects=False)
            if resp.status_code in (200, 201) and not resp.text.lstrip().startswith("<"):
                break
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("edit_profile: redirected to login (session rate-limited)")
        if resp.status_code not in (200, 201):
            raise FetchError(f"edit_profile: HTTP {resp.status_code}: {resp.text[:200]}")
        body_text = resp.text
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"edit_profile: got HTML (session blocked): {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"edit_profile: invalid JSON: {body_text[:200]}")
        user = body.get("user") or {}
        return {
            "status": "updated",
            "username": user.get("username", ""),
            "full_name": user.get("full_name", ""),
            "biography": user.get("biography", ""),
            "external_url": user.get("external_url", ""),
        }


    async def broadcast_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """
        Get info about a broadcast channel (subscribers, title, description).

        Args:
            channel_id: The broadcast channel ID (from channel URL or DM).

        Returns:
            dict with channel_id, title, description, subscriber_count, is_pinned.
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "accept": "application/json, */*",
            "Cookie": self._cookie_str(),
        }
        resp = await session.get(
            f"https://i.instagram.com/api/v1/broadcasts/{channel_id}/info/",
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("broadcast_channel_info: redirected — not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"broadcast_channel_info: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"broadcast_channel_info: got HTML: {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"broadcast_channel_info: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"broadcast_channel_info: {body.get('message', 'unknown')}")
        ch = body.get("broadcast_channel") or body
        return {
            "channel_id": channel_id,
            "title": ch.get("title", ""),
            "description": ch.get("description", ""),
            "subscriber_count": ch.get("subscriber_count", 0),
            "is_pinned": ch.get("is_pinned", False),
            "broadcast_status": ch.get("broadcast_status", ""),
        }


    async def broadcast_channel_posts(
        self, channel_id: str, max_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get posts from a broadcast channel.

        Returns:
            dict with posts (list), next_max_id (for pagination), has_more.
        """
        session = await self._get_auth_session()
        cm = self._cookie_manager
        csrf = (cm.cookies.get("csrftoken", "") if cm else "") or ""
        headers = {
            "x-csrftoken": csrf,
            "x-ig-app-id": self._config.ig_app_id,
            "accept": "application/json, */*",
            "Cookie": self._cookie_str(),
        }
        params: Dict[str, str] = {}
        if max_id:
            params["max_id"] = max_id
        resp = await session.get(
            f"https://i.instagram.com/api/v1/broadcasts/{channel_id}/posts/",
            params=params,
            headers=headers,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise FetchError("broadcast_channel_posts: redirected — not logged in")
        body_text = resp.text
        if resp.status_code not in (200, 201):
            raise FetchError(f"broadcast_channel_posts: HTTP {resp.status_code}: {body_text[:200]}")
        if body_text.lstrip().startswith("<"):
            raise FetchError(f"broadcast_channel_posts: got HTML: {body_text[:150]}")
        try:
            body = _json.loads(body_text)
        except Exception:
            raise FetchError(f"broadcast_channel_posts: invalid JSON: {body_text[:200]}")
        if body.get("status") == "fail":
            raise FetchError(f"broadcast_channel_posts: {body.get('message', 'unknown')}")
        items = body.get("broadcast_posts") or body.get("items") or []
        posts = []
        for item in items:
            posts.append({
                "post_id": str(item.get("pk", item.get("id", ""))),
                "text": item.get("text", ""),
                "created_at": item.get("created_at") or item.get("taken_at"),
                "like_count": item.get("like_count", 0),
            })
        return {
            "posts": posts,
            "next_max_id": body.get("next_max_id"),
            "has_more": bool(body.get("more_available") or body.get("next_max_id")),
        }

    # ── Threads ───────────────────────────────────────────────────────────────

    _THREADS_APP_ID = "238260118697367"  # Threads web app ID

