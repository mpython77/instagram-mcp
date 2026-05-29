"""Tests for browser fingerprint rotation module."""

import pytest

from instagram_mcp.fingerprint import (
    CHROME_VERSIONS,
    LOCALES,
    PLATFORMS,
    FingerprintRotator,
)


class TestFingerprintRotator:
    """Tests for FingerprintRotator."""

    def test_get_impersonate_returns_valid_version(self):
        """get_impersonate should return a version from CHROME_VERSIONS."""
        rotator = FingerprintRotator(seed=42)
        version = rotator.get_impersonate()
        assert version in CHROME_VERSIONS

    def test_get_headers_has_required_keys(self):
        """get_headers should return dict with all required header keys."""
        rotator = FingerprintRotator(seed=42)
        headers = rotator.get_headers()

        assert "Accept-Language" in headers
        assert "Sec-CH-UA-Platform" in headers
        assert "Sec-CH-UA-Mobile" in headers
        assert headers["Sec-CH-UA-Mobile"] == "?0"
        assert headers["Accept-Language"] in LOCALES
        assert headers["Sec-CH-UA-Platform"] in PLATFORMS

    def test_seeded_reproducibility(self):
        """Same seed should produce identical results."""
        rotator1 = FingerprintRotator(seed=123)
        rotator2 = FingerprintRotator(seed=123)

        fp1 = rotator1.get_fingerprint()
        fp2 = rotator2.get_fingerprint()

        assert fp1 == fp2

    def test_different_seeds_differ(self):
        """Different seeds should (likely) produce different results."""
        rotator1 = FingerprintRotator(seed=1)
        rotator2 = FingerprintRotator(seed=999)

        # Run multiple times to increase chance of difference
        results1 = [rotator1.get_impersonate() for _ in range(5)]
        results2 = [rotator2.get_impersonate() for _ in range(5)]
        assert results1 != results2

    def test_get_fingerprint_structure(self):
        """get_fingerprint should return dict with impersonate and headers."""
        rotator = FingerprintRotator(seed=42)
        fp = rotator.get_fingerprint()

        assert "impersonate" in fp
        assert "headers" in fp
        assert isinstance(fp["impersonate"], str)
        assert isinstance(fp["headers"], dict)
        assert fp["impersonate"] in CHROME_VERSIONS
