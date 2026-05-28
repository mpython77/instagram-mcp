#!/usr/bin/env python3
"""Regenerate .well-known/mcp/server-card.json from the runtime Tool_Inventory.

Usage:
    python scripts/regenerate_server_card.py [--dry-run]

The script:
  1. Constructs an MCP server via ``create_mcp_server`` (which runs the full
     registration + audit + instructions pipeline).
  2. Reads ``mcp._instagram_tool_inventory`` to get the live tool list.
  3. Loads the existing ``.well-known/mcp/server-card.json``.
  4. Replaces ONLY the tool list section; every other field is preserved
     verbatim (Requirement 21.4).
  5. Writes the file atomically (.tmp then rename).

Without ``--dry-run`` the file is rewritten. With ``--dry-run`` the script
prints what would change but does not write.

The inventory entries written into the card use this shape::

    {
      "name": "<tool_name>",
      "title": "<tool_annotations.title>",
      "auth_tier": "anon" | "auth" | "auto",
      "toolset": "<canonical_toolset>",
      "annotations": {
        "readOnlyHint": bool,
        "idempotentHint": bool,
        "destructiveHint": bool,
        "openWorldHint": bool
      }
    }

If the existing card already has a different shape for tools, adapt to it as
follows: keep the existing top-level keys (e.g. ``name``, ``version``,
``description``, ``vendor``, ``capabilities``, ``protocol_version``,
``transports``), and replace only ``capabilities.tools`` (or whatever path
holds the tool list). If no tools section exists, add it under the obvious
key ``tools`` at the top level.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Suppress lifespan-only warnings during the import-only run
logging.basicConfig(level=logging.WARNING)


CARD_PATH = (
    Path(__file__).resolve().parent.parent / ".well-known" / "mcp" / "server-card.json"
)


def _build_inventory_payload() -> list[dict]:
    """Construct the MCP server and return the inventory payload."""
    # Avoid loading cookies for this script — it just inspects shape, not
    # behaviour. Honour any value the caller has already exported, but default
    # to "0" so auth-tier tools remain registered even when no cookies are
    # present (the server card is meant to advertise the full surface).
    os.environ.setdefault("INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES", "0")
    from instagram_mcp import create_mcp_server  # noqa: E402

    mcp = create_mcp_server()
    inv = mcp._instagram_tool_inventory  # type: ignore[attr-defined]

    payload: list[dict] = []
    for desc in inv:
        payload.append(
            {
                "name": desc.name,
                "title": desc.annotations.get("title", desc.name),
                "auth_tier": desc.auth_tier,
                "toolset": desc.toolset,
                "annotations": {
                    "readOnlyHint": bool(desc.annotations.get("readOnlyHint", False)),
                    "idempotentHint": bool(
                        desc.annotations.get("idempotentHint", False)
                    ),
                    "destructiveHint": bool(
                        desc.annotations.get("destructiveHint", False)
                    ),
                    "openWorldHint": bool(
                        desc.annotations.get("openWorldHint", False)
                    ),
                },
            }
        )
    return payload


def _load_card() -> dict:
    if not CARD_PATH.is_file():
        # Fresh card scaffolding if the file is absent.
        return {
            "name": "instagram-mcp",
            "description": (
                "Instagram MCP server — scraping, DMs, scheduling, uploads, "
                "social actions"
            ),
            "tools": [],
        }
    return json.loads(CARD_PATH.read_text(encoding="utf-8"))


def _write_card(card: dict) -> None:
    """Atomic write: serialise → .tmp → rename."""
    CARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(card, ensure_ascii=False, indent=2) + "\n"
    tmp = CARD_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(CARD_PATH)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print, don't write."
    )
    args = parser.parse_args(argv)

    card = _load_card()
    inventory = _build_inventory_payload()

    # Decide where the tool list lives. Prefer the existing key path if
    # present so we never touch unrelated top-level keys.
    if (
        "capabilities" in card
        and isinstance(card.get("capabilities"), dict)
        and "tools" in card["capabilities"]
    ):
        target_path: tuple[str, ...] = ("capabilities", "tools")
    elif "tools" in card:
        target_path = ("tools",)
    else:
        # Default: top-level "tools" key.
        target_path = ("tools",)
        card["tools"] = []

    # Apply the replacement, walking down the path (which may be nested).
    cursor: dict = card
    for key in target_path[:-1]:
        cursor = cursor[key]
    cursor[target_path[-1]] = inventory

    if args.dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0

    _write_card(card)
    print(f"Wrote {len(inventory)} tools to {CARD_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
