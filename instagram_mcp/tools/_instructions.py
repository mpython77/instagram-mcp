"""Server instructions builder for the Instagram MCP server.

Implements ``build_server_instructions`` per *design.md* Section 5
("Server instructions builder"). The builder consumes the runtime
``Tool_Inventory`` (a list of :class:`ToolDescriptor` produced by the
per-toolset registrars) and emits a deterministic, multi-section text
block suitable for ``FastMCP.instructions``.

Design contract (Section 5):

* The output begins with a one-line header naming the auth mode
  (the literal ``"authenticated"`` or ``"anonymous (no cookies.txt)"``).
* An ``AUTH TIERS`` block reports the three per-tier counts derived from
  the inventory (🌐 anon, 🔐 auth, 🌐/🔐 auto).
* A ``TOOLS`` block lists every registered tool grouped by toolset in
  :data:`CANONICAL_ORDER`. Within each toolset the entries are sorted
  alphabetically by tool name and prefixed with the matching tier badge
  from :data:`TIER_BADGE`.
* Toolsets with zero registered tools are omitted from the output for
  readability (option (a) in the task spec).
* When the inventory is empty the function still returns a non-empty
  string with zero counts and an empty ``TOOLS`` list — it never raises.

The shape of this string is deterministic and intentionally easy to
diff: every newline, badge and ordering decision is fixed by the
contract above.

Validates Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from ._helpers import ToolDescriptor


# Canonical toolset ordering. Sections in the rendered output appear in
# exactly this order (Requirement 6.2). Toolsets not present in the
# inventory are simply skipped — see the ``# omit empty toolsets`` note
# inside :func:`build_server_instructions`.
CANONICAL_ORDER: tuple[str, ...] = (
    "profile",
    "analysis",
    "content",
    "social_graph",
    "dm",
    "upload",
    "automation",
    "server",
)

# Auth-tier → badge mapping. Keys must match the ``AuthTier`` literal in
# ``_helpers.py`` (Requirement 6.3).
TIER_BADGE: dict[str, str] = {
    "anon": "🌐",
    "auth": "🔐",
    "auto": "🌐/🔐",
}


def build_server_instructions(
    inventory: Iterable[ToolDescriptor],
    auth_status: str,
) -> str:
    """Render the deterministic server-instructions string.

    Args:
        inventory: The runtime :class:`ToolDescriptor` list produced by
            ``register_tools``. Any iterable is accepted; it is consumed
            once into a local list.
        auth_status: The literal string ``"authenticated"`` or
            ``"anonymous (no cookies.txt)"`` describing the current auth
            mode (Requirement 6.7). The value is interpolated verbatim
            into the header.

    Returns:
        A non-empty multi-section string. When ``inventory`` is empty
        the result still contains the header, an ``AUTH TIERS`` block
        with zero counters, and an empty ``TOOLS`` section — no
        exception is raised (Requirement 6.6).
    """
    descriptors: list[ToolDescriptor] = list(inventory)

    # Per-tier counters drive the AUTH TIERS block (Requirement 6.5).
    tier_counts: Counter[str] = Counter(d.auth_tier for d in descriptors)
    n_anon = tier_counts.get("anon", 0)
    n_auth = tier_counts.get("auth", 0)
    n_auto = tier_counts.get("auto", 0)
    total = len(descriptors)

    # Bucket descriptors by toolset so we can emit sections in
    # CANONICAL_ORDER (Requirement 6.2).
    by_toolset: dict[str, list[ToolDescriptor]] = {}
    for desc in descriptors:
        by_toolset.setdefault(desc.toolset, []).append(desc)

    lines: list[str] = []
    lines.append(f"Instagram data server — {auth_status}.")
    lines.append("")
    lines.append("AUTH TIERS:")
    lines.append(
        f"• 🌐 Anonymous: {n_anon} tools — no credentials needed."
    )
    lines.append(
        f"• 🔐 Authenticated: {n_auth} tools — require cookies.json/cookies.txt."
    )
    lines.append(
        f"• 🌐/🔐 Auto-mode: {n_auto} tools — anonymous, upgrade with cookies."
    )
    lines.append("")
    lines.append(f"TOOLS (total: {total}):")

    for toolset in CANONICAL_ORDER:
        items = by_toolset.get(toolset, [])
        if not items:
            # Option (a): omit empty toolsets for cleanliness.
            continue
        lines.append("")
        lines.append(f"[{toolset} — {len(items)} tools]")
        for desc in sorted(items, key=lambda d: d.name):
            badge = TIER_BADGE[desc.auth_tier]
            description = desc.description_first_line or ""
            lines.append(f"• {badge} {desc.name} — {description}")

    return "\n".join(lines)


__all__ = [
    "CANONICAL_ORDER",
    "TIER_BADGE",
    "build_server_instructions",
]
