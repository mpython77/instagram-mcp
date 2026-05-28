"""Annotation audit for the registered MCP tool inventory.

This module implements the contract described in **design Section 6**
("Annotation audit") of the ``mcp-architecture-hardening`` spec. It exposes
:data:`DESTRUCTIVE_TOOLS`, the :class:`AnnotationAuditError` exception, and the
:func:`run_annotation_audit` entry point that the server bootstrap calls
immediately after :func:`register_tools` has populated
``mcp._instagram_tool_inventory``.

The audit enforces five rules against every :class:`ToolDescriptor`:

(i)   ``annotations["title"]`` is a non-empty ``str``.
(ii)  ``annotations["readOnlyHint"]``, ``annotations["idempotentHint"]``,
      ``annotations["destructiveHint"]``, ``annotations["openWorldHint"]``
      are all ``bool`` values (no implicit truthiness, no ``None``).
(iii) ``description_first_line`` (after stripping leading whitespace) starts
      with one of the auth-tier markers ``🌐``, ``🔐``, ``🌐/🔐`` *and* the
      marker matches the descriptor's ``auth_tier``:

      * ``"anon"`` → ``🌐``
      * ``"auth"`` → ``🔐``
      * ``"auto"`` → ``🌐/🔐``

(iv)  IF the tool ``name`` is in :data:`DESTRUCTIVE_TOOLS` THEN
      ``annotations["readOnlyHint"]`` MUST be ``False`` AND
      (``annotations["destructiveHint"]`` is ``True`` OR
      ``annotations["idempotentHint"]`` is ``False``).
(v)   IF the tool ``name`` is **not** in :data:`DESTRUCTIVE_TOOLS` THEN the
      tool is treated as purely read-only:
      ``annotations["readOnlyHint"]`` MUST be ``True`` AND
      ``annotations["destructiveHint"]`` MUST be ``False``.

All violations are accumulated; if any are found,
:class:`AnnotationAuditError` is raised with a single multi-line message,
one violation per line, each line tagged with the offending tool's ``name``
and the violated rule number. This matches Requirement 17.4 ("error message
naming the offending tool and the violated rule").

Validates: Requirements 8.1, 8.2, 8.3, 17.1, 17.2, 17.3, 17.4.
"""

from __future__ import annotations

from typing import Iterable

from ._helpers import ToolDescriptor

__all__ = [
    "DESTRUCTIVE_TOOLS",
    "AnnotationAuditError",
    "run_annotation_audit",
]


# ---------------------------------------------------------------------------
# Destructive tool registry (mirrors design Section 6 verbatim)
# ---------------------------------------------------------------------------

DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    # Direct messages — every send/react/unsend/mark-seen mutates Instagram state.
    "instagram_dm_send",
    "instagram_dm_send_photo",
    "instagram_dm_send_video",
    "instagram_dm_react",
    "instagram_dm_unsend",
    "instagram_dm_mark_seen",
    # Engagement actions.
    "instagram_post_like",
    "instagram_post_save",
    "instagram_follow_user",
    "instagram_block_user",
    "instagram_post_comment",
    "instagram_delete_comment",
    "instagram_comment_reply",
    "instagram_comment_like",
    "instagram_comment_hide",
    # Stories.
    "instagram_publish_story",
    "instagram_story_mark_seen",
    "instagram_story_reply",
    # Profile / broadcast / uploads / scheduling.
    "instagram_edit_profile",
    "instagram_broadcast_channel",
    "instagram_upload_photo",
    "instagram_upload_reel",
    "instagram_upload_video",
    "instagram_post_delete",
    "instagram_toggle_comments",
    "instagram_account_privacy",
    "instagram_submit_verification_code",
    "instagram_schedule",  # write when action != list/get
    # OAuth / sessions / monitor — token rotation and persistent state mutation
    # (webhook setup is state-mutating, even if the tool itself is auth='auth').
    "instagram_oauth",
    "instagram_sessions",
    "instagram_monitor",
})


# ---------------------------------------------------------------------------
# Exception type
# ---------------------------------------------------------------------------


class AnnotationAuditError(RuntimeError):
    """Raised by :func:`run_annotation_audit` when one or more descriptors
    violate the rules described in this module's docstring.

    The ``args[0]`` payload is a single multi-line string with one violation
    per line. Each line is formatted as
    ``"<tool_name>: rule (<roman>) — <human-readable detail>"`` so callers
    (and CI logs) can identify the offending tool and the violated rule
    without parsing structured data.
    """


# ---------------------------------------------------------------------------
# Auth-tier marker map (used by rule iii)
# ---------------------------------------------------------------------------

_TIER_TO_MARKER: dict[str, str] = {
    "anon": "🌐",
    "auth": "🔐",
    "auto": "🌐/🔐",
}

# All accepted markers, ordered so that the longest prefix is checked first.
# This matters because ``"🌐/🔐"`` shares its first character with ``"🌐"``;
# we need to match the most specific marker before the shorter ones.
_ALL_MARKERS: tuple[str, ...] = ("🌐/🔐", "🌐", "🔐")


# ---------------------------------------------------------------------------
# Audit entry point
# ---------------------------------------------------------------------------


def run_annotation_audit(inventory: Iterable[ToolDescriptor]) -> None:
    """Validate every :class:`ToolDescriptor` against rules (i)–(v).

    Parameters
    ----------
    inventory:
        Iterable of :class:`ToolDescriptor` objects, typically the value
        stored on ``mcp._instagram_tool_inventory`` immediately after
        :func:`register_tools` returns.

    Raises
    ------
    AnnotationAuditError
        If any descriptor violates one or more rules. The error message is a
        newline-joined list of ``"<tool>: rule (<n>) — <detail>"`` entries,
        one per violation; multiple violations on the same descriptor produce
        multiple lines.
    """
    violations: list[str] = []

    for descriptor in inventory:
        annotations = descriptor.annotations or {}

        # ------------------------------------------------------------------
        # Rule (i): non-empty string title.
        # ------------------------------------------------------------------
        title = annotations.get("title")
        if not isinstance(title, str) or not title.strip():
            violations.append(
                f"{descriptor.name}: rule (i) — annotations['title'] must be a "
                f"non-empty str (got {type(title).__name__})"
            )

        # ------------------------------------------------------------------
        # Rule (ii): four bool hints.
        # ------------------------------------------------------------------
        for key in ("readOnlyHint", "idempotentHint", "destructiveHint", "openWorldHint"):
            value = annotations.get(key)
            if not isinstance(value, bool):
                violations.append(
                    f"{descriptor.name}: rule (ii) — annotations[{key!r}] must "
                    f"be bool (got {type(value).__name__})"
                )

        # ------------------------------------------------------------------
        # Rule (iii): docstring marker present and matching auth_tier.
        # ------------------------------------------------------------------
        first = (descriptor.description_first_line or "").lstrip()
        # Match the longest marker first so "🌐/🔐" wins over "🌐".
        actual_marker: str | None = None
        for marker in _ALL_MARKERS:
            if first.startswith(marker):
                actual_marker = marker
                break

        if actual_marker is None:
            violations.append(
                f"{descriptor.name}: rule (iii) — description_first_line must "
                f"start with one of '🌐', '🔐', '🌐/🔐' (got {first[:16]!r})"
            )
        else:
            expected_marker = _TIER_TO_MARKER.get(descriptor.auth_tier)
            if expected_marker is None:
                violations.append(
                    f"{descriptor.name}: rule (iii) — invalid auth_tier "
                    f"{descriptor.auth_tier!r} (expected 'anon', 'auth', or 'auto')"
                )
            elif actual_marker != expected_marker:
                violations.append(
                    f"{descriptor.name}: rule (iii) — docstring marker "
                    f"{actual_marker!r} does not match auth_tier="
                    f"{descriptor.auth_tier!r} (expected {expected_marker!r})"
                )

        # ------------------------------------------------------------------
        # Rule (iv): destructive tool invariants.
        # Rule (v): purely read-only invariants.
        #
        # We deliberately skip these structural checks when rule (ii) already
        # flagged a non-bool hint, because reasoning about ``True``/``False``
        # against a missing or wrongly-typed value would produce confusing
        # double-reporting. Rule (ii) is sufficient to fail the audit in that
        # case; the maintainer fixes the type, then reruns and sees rule
        # (iv)/(v) outcomes cleanly.
        # ------------------------------------------------------------------
        hints_typed = all(
            isinstance(annotations.get(k), bool)
            for k in ("readOnlyHint", "idempotentHint", "destructiveHint")
        )
        if hints_typed:
            read_only_hint = annotations["readOnlyHint"]
            destructive_hint = annotations["destructiveHint"]
            idempotent_hint = annotations["idempotentHint"]

            if descriptor.name in DESTRUCTIVE_TOOLS:
                # Rule (iv).
                if read_only_hint is not False:
                    violations.append(
                        f"{descriptor.name}: rule (iv) — destructive tool must "
                        f"declare readOnlyHint=False"
                    )
                if not (destructive_hint is True or idempotent_hint is False):
                    violations.append(
                        f"{descriptor.name}: rule (iv) — destructive tool must "
                        f"declare destructiveHint=True or idempotentHint=False"
                    )
            else:
                # Rule (v) — conservative interpretation: any tool not on the
                # destructive list is treated as purely read-only.
                if read_only_hint is not True:
                    violations.append(
                        f"{descriptor.name}: rule (v) — non-destructive tool "
                        f"must declare readOnlyHint=True"
                    )
                if destructive_hint is not False:
                    violations.append(
                        f"{descriptor.name}: rule (v) — non-destructive tool "
                        f"must declare destructiveHint=False"
                    )

    if violations:
        raise AnnotationAuditError("\n".join(violations))
