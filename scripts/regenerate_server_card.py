#!/usr/bin/env python3
"""Regenerate ``.well-known/mcp/server-card.json`` from the runtime Tool Inventory.

Usage::

    python scripts/regenerate_server_card.py [--dry-run]

This is a thin backwards-compatible wrapper around
``scripts/generate_metadata.py`` — the single source of truth for all metadata
files (manifest.json, smithery.yaml, server-card.json). It exists so existing
callers and tests that target only the server card keep working.

To regenerate every metadata file at once, prefer::

    python scripts/generate_metadata.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make sibling module importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_metadata as gm  # noqa: E402

CARD_PATH = gm.CARD_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print, don't write.")
    args = parser.parse_args(argv)

    inventory, version = gm.load_inventory()
    card = gm.build_server_card(inventory, version)

    if args.dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0

    gm._atomic_write(CARD_PATH, gm._json_text(card))
    print(f"Wrote {len(card['tools'])} tools to {CARD_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
