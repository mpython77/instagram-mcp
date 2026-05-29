"""Browser fingerprint rotation for anti-detection.

This module provides utilities for integration into the request pipeline.
See middleware.py for composable usage.
"""

from __future__ import annotations

import random
import logging
from typing import Dict, Optional

logger = logging.getLogger("instagram_mcp.fingerprint")

CHROME_VERSIONS = [
    "chrome120",
    "chrome124",
    "chrome126",
    "chrome127",
    "chrome130",
    "chrome131",
    "chrome133",
    "chrome136",
    "chrome140",
    "chrome142",
]

LOCALES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
]

PLATFORMS = [
    '"Windows"',
    '"macOS"',
    '"Linux"',
]


class FingerprintRotator:
    """Rotates browser fingerprints for anti-detection.

    Each instance can be seeded for reproducibility in tests.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    def get_impersonate(self) -> str:
        """Return a random Chrome impersonation version string."""
        return self._rng.choice(CHROME_VERSIONS)

    def get_headers(self) -> Dict[str, str]:
        """Return randomized browser headers for anti-detection."""
        return {
            "Accept-Language": self._rng.choice(LOCALES),
            "Sec-CH-UA-Platform": self._rng.choice(PLATFORMS),
            "Sec-CH-UA-Mobile": "?0",
        }

    def get_fingerprint(self) -> Dict[str, object]:
        """Return a combined fingerprint with impersonate string and headers."""
        return {
            "impersonate": self.get_impersonate(),
            "headers": self.get_headers(),
        }
