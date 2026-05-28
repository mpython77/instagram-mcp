"""DM toolset — Instagram Direct messages.

All eight tools registered here are 🔐 auth-required; the registrar therefore
short-circuits to an empty inventory when ``MCPConfig.hide_auth_when_no_cookies``
is set and ``client.cookie_manager.is_authenticated`` is ``False``. Bodies are
ported verbatim from the legacy ``instagram_mcp/tools.py`` (lines 3516-3713 and
4643-4722); only annotation hints are tightened so each destructive write tool
declares ``readOnlyHint=False, destructiveHint=True, idempotentHint=False`` to
satisfy :func:`instagram_mcp.tools._audit.run_annotation_audit`.

Tools registered:

* ``instagram_dm_inbox``       (read-only)
* ``instagram_dm_thread``      (read-only)
* ``instagram_dm_send``        (destructive)
* ``instagram_dm_send_photo``  (destructive)
* ``instagram_dm_send_video``  (destructive)
* ``instagram_dm_react``       (destructive)
* ``instagram_dm_unsend``      (destructive)
* ``instagram_dm_mark_seen``   (destructive)

Validates: Requirements 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3,
8.1, 8.3, 17.2.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context

from ..formatter import (
    format_dm_inbox_markdown,
    format_dm_send_markdown,
    format_dm_thread_markdown,
)
from ..models import (
    DMInboxInput,
    DMMarkSeenInput,
    DMReactInput,
    DMSendInput,
    DMSendPhotoInput,
    DMSendVideoInput,
    DMThreadInput,
    DMUnsendInput,
)
from ._helpers import ToolDescriptor, _exception_to_tool_error, _tool_error

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ..client import InstagramClient
    from ..config import MCPConfig
    from ..exporter import JsonExporter

logger = logging.getLogger(__name__)

TOOLSET_NAME = "dm"

__all__ = ["TOOLSET_NAME", "register_dm"]


# Annotation presets — reused per tool below to keep registration concise and
# to make the read-only vs destructive contract obvious at a glance.
_READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_DESTRUCTIVE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


def _first_doc_line(fn) -> str:
    """Return the first non-empty stripped line of a function's docstring."""
    doc = (fn.__doc__ or "").strip()
    for line in doc.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def register_dm(
    mcp: "FastMCP",
    client: "InstagramClient",
    config: "MCPConfig",
    exporter: "JsonExporter",
) -> list[ToolDescriptor]:
    """Register the DM toolset with ``mcp`` and return its descriptors.

    Every tool here is 🔐 auth-required, so when
    ``config.hide_auth_when_no_cookies`` is enabled and the cookie manager has
    no authenticated session the registrar returns an empty list without
    declaring any ``@mcp.tool``.
    """
    descriptors: list[ToolDescriptor] = []

    cookie_manager = getattr(client, "cookie_manager", None)
    is_authed = bool(getattr(cookie_manager, "is_authenticated", False))
    if config.hide_auth_when_no_cookies and not is_authed:
        return descriptors

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_inbox  (read-only)
    # ─────────────────────────────────────────────────────────────────────
    inbox_annotations = {"title": "Instagram DM Inbox", **_READ_ONLY_ANNOTATIONS}

    @mcp.tool(name="instagram_dm_inbox", annotations=inbox_annotations)
    async def instagram_dm_inbox(params: DMInboxInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — List DM inbox threads.

        Returns your most recent direct message conversations:
        thread title, participants, unread status, last message preview.
        Use thread_id from results to fetch full messages via instagram_dm_thread.

        Args:
            params: limit (1-50, default 20), cursor (pagination)
        """
        await ctx.info(f"instagram_dm_inbox: limit={params.limit}")
        try:
            data = await client.fetch_dm_inbox(
                limit=params.limit,
                cursor=params.cursor or None,
            )
            return format_dm_inbox_markdown(data)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_inbox",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=inbox_annotations,
            input_model=DMInboxInput,
            description_first_line=_first_doc_line(instagram_dm_inbox),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_thread  (read-only)
    # ─────────────────────────────────────────────────────────────────────
    thread_annotations = {"title": "Instagram DM Thread", **_READ_ONLY_ANNOTATIONS}

    @mcp.tool(name="instagram_dm_thread", annotations=thread_annotations)
    async def instagram_dm_thread(params: DMThreadInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Fetch messages in a DM thread.

        Returns conversation messages in chronological order.
        Supports pagination via cursor for older messages.

        Args:
            params: thread_id (from dm_inbox), limit (1-50), cursor
        """
        await ctx.info(f"instagram_dm_thread: {params.thread_id}")
        try:
            data = await client.fetch_dm_thread(
                thread_id=params.thread_id,
                limit=params.limit,
                cursor=params.cursor or None,
            )
            return format_dm_thread_markdown(data)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_thread",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=thread_annotations,
            input_model=DMThreadInput,
            description_first_line=_first_doc_line(instagram_dm_thread),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_send  (destructive)
    # ─────────────────────────────────────────────────────────────────────
    send_annotations = {"title": "Instagram DM Send", **_DESTRUCTIVE_ANNOTATIONS}

    @mcp.tool(name="instagram_dm_send", annotations=send_annotations)
    async def instagram_dm_send(params: DMSendInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Send a text DM via Instagram Web GraphQL.

        Provide either:
        - username: Instagram handle (e.g. 'cristiano') — resolves thread automatically
        - thread_id: igid from instagram_dm_inbox — sends to existing thread

        Args:
            params: username OR thread_id, plus text (max 1000 chars)
        """
        target = params.username or params.thread_id
        await ctx.info(f"instagram_dm_send: target={target}, len={len(params.text)}")
        try:
            if params.username:
                data = await client.send_dm_to_username(
                    username=params.username,
                    text=params.text,
                )
            else:
                data = await client.send_dm_text(
                    thread_id=params.thread_id,
                    text=params.text,
                )
            return format_dm_send_markdown(data)
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_send",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=send_annotations,
            input_model=DMSendInput,
            description_first_line=_first_doc_line(instagram_dm_send),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_send_photo  (destructive)
    # ─────────────────────────────────────────────────────────────────────
    send_photo_annotations = {
        "title": "Instagram DM Send Photo",
        **_DESTRUCTIVE_ANNOTATIONS,
    }

    @mcp.tool(name="instagram_dm_send_photo", annotations=send_photo_annotations)
    async def instagram_dm_send_photo(params: DMSendPhotoInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Send a photo as a Direct Message.

        Provide either username or thread_id. The photo is uploaded and sent
        to the DM thread immediately.

        Args:
            params: photo_path (local file), username or thread_id, optional caption
        """
        await ctx.info(
            f"instagram_dm_send_photo: target={params.username or params.thread_id}"
        )
        if not params.username and not params.thread_id:
            raise _tool_error("Provide either username or thread_id", "validation_error")
        try:
            data = await client.dm_send_photo(
                params.photo_path,
                thread_id=params.thread_id,
                username=params.username,
                caption=params.caption,
            )
            target = params.username or params.thread_id
            return f"✅ Photo sent to {target}\nItem ID: {data.get('item_id', '')}"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_send_photo",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=send_photo_annotations,
            input_model=DMSendPhotoInput,
            description_first_line=_first_doc_line(instagram_dm_send_photo),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_send_video  (destructive)
    # ─────────────────────────────────────────────────────────────────────
    send_video_annotations = {
        "title": "Instagram DM Send Video",
        **_DESTRUCTIVE_ANNOTATIONS,
    }

    @mcp.tool(name="instagram_dm_send_video", annotations=send_video_annotations)
    async def instagram_dm_send_video(params: DMSendVideoInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Send a video file as a Direct Message.

        Uploads the video and sends it to the specified DM thread or user.

        Args:
            params: video_path (local MP4), username or thread_id, optional thumbnail_path
        """
        await ctx.info(
            f"instagram_dm_send_video: target={params.username or params.thread_id}"
        )
        if not params.username and not params.thread_id:
            raise _tool_error("Provide either username or thread_id", "validation_error")
        try:
            data = await client.dm_send_video(
                params.video_path,
                thread_id=params.thread_id,
                username=params.username,
                thumbnail_path=params.thumbnail_path,
            )
            target = params.username or params.thread_id
            return f"✅ Video sent to {target}\nItem ID: {data.get('item_id', '')}"
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_send_video",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=send_video_annotations,
            input_model=DMSendVideoInput,
            description_first_line=_first_doc_line(instagram_dm_send_video),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_react  (destructive)
    # ─────────────────────────────────────────────────────────────────────
    react_annotations = {"title": "Instagram DM Reaction", **_DESTRUCTIVE_ANNOTATIONS}

    @mcp.tool(name="instagram_dm_react", annotations=react_annotations)
    async def instagram_dm_react(params: DMReactInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Add or remove an emoji reaction to a DM message.

        Args:
            params: thread_id, item_id, emoji (default ❤), action (react/unreact)
        """
        await ctx.info(
            f"instagram_dm_react: thread={params.thread_id} item={params.item_id} "
            f"action={params.action}"
        )
        try:
            if params.action == "unreact":
                data = await client.dm_unreact(params.thread_id, params.item_id)
            else:
                data = await client.dm_react(
                    params.thread_id, params.item_id, params.emoji
                )
            return (
                f"✅ {data['status'].capitalize()}: {data.get('emoji', '')} "
                f"on message {data['item_id'][:20]}..."
            )
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_react",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=react_annotations,
            input_model=DMReactInput,
            description_first_line=_first_doc_line(instagram_dm_react),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_unsend  (destructive)
    # ─────────────────────────────────────────────────────────────────────
    unsend_annotations = {"title": "Instagram DM Unsend", **_DESTRUCTIVE_ANNOTATIONS}

    @mcp.tool(name="instagram_dm_unsend", annotations=unsend_annotations)
    async def instagram_dm_unsend(params: DMUnsendInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Delete/unsend a DM message (removes it for everyone).

        Args:
            params: thread_id, item_id
        """
        await ctx.info(
            f"instagram_dm_unsend: thread={params.thread_id} item={params.item_id}"
        )
        try:
            data = await client.dm_unsend(params.thread_id, params.item_id)
            return f"✅ Message deleted: {data['item_id'][:30]}..."
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_unsend",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=unsend_annotations,
            input_model=DMUnsendInput,
            description_first_line=_first_doc_line(instagram_dm_unsend),
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # TOOL: instagram_dm_mark_seen  (destructive)
    # ─────────────────────────────────────────────────────────────────────
    mark_seen_annotations = {
        "title": "Instagram DM Mark Seen",
        **_DESTRUCTIVE_ANNOTATIONS,
    }

    @mcp.tool(name="instagram_dm_mark_seen", annotations=mark_seen_annotations)
    async def instagram_dm_mark_seen(params: DMMarkSeenInput, ctx: Context) -> str:
        """🔐 AUTH REQUIRED — Mark a DM thread as seen up to a given message.

        Args:
            params: thread_id, item_id (last message to mark as read)
        """
        await ctx.info(f"instagram_dm_mark_seen: thread={params.thread_id}")
        try:
            data = await client.dm_mark_seen(params.thread_id, params.item_id)
            return f"✅ Thread marked as seen up to message {data['item_id'][:30]}..."
        except Exception as e:
            raise _exception_to_tool_error(e)

    descriptors.append(
        ToolDescriptor(
            name="instagram_dm_mark_seen",
            toolset=TOOLSET_NAME,
            auth_tier="auth",
            annotations=mark_seen_annotations,
            input_model=DMMarkSeenInput,
            description_first_line=_first_doc_line(instagram_dm_mark_seen),
        )
    )

    return descriptors
