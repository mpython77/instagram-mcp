"""Tests for shadow-ban detection module."""

import pytest

from instagram_mcp.shadow_ban import ShadowBanDetector


class TestShadowBanDetector:
    """Tests for ShadowBanDetector."""

    def test_no_shadow_ban_on_valid_data(self):
        """Non-empty responses should not trigger shadow-ban detection."""
        detector = ShadowBanDetector(threshold=3)
        proxy = "http://proxy1:8080"

        # Valid dict with data
        assert detector.check_response(proxy, {"users": [{"id": 1}]}) is False
        # Valid list with items
        assert detector.check_response(proxy, [{"post": "abc"}]) is False
        # Non-empty string
        assert detector.check_response(proxy, "some data") is False

    def test_shadow_ban_detected_after_threshold(self):
        """Shadow-ban should be detected after threshold consecutive empty responses."""
        detector = ShadowBanDetector(threshold=3)
        proxy = "http://proxy1:8080"

        # First empty - not yet
        assert detector.check_response(proxy, None) is False
        # Second empty - not yet
        assert detector.check_response(proxy, {}) is False
        # Third empty - threshold reached
        assert detector.check_response(proxy, []) is True

    def test_counter_resets_on_valid_response(self):
        """A valid response should reset the consecutive empty counter."""
        detector = ShadowBanDetector(threshold=3)
        proxy = "http://proxy1:8080"

        # Two empty responses
        assert detector.check_response(proxy, None) is False
        assert detector.check_response(proxy, {}) is False

        # One valid response resets
        assert detector.check_response(proxy, {"user": "data"}) is False

        # Now need 3 more empty to trigger
        assert detector.check_response(proxy, None) is False
        assert detector.check_response(proxy, None) is False
        assert detector.check_response(proxy, None) is True

    def test_quarantine_and_stats(self):
        """Quarantine and stats methods should work correctly."""
        detector = ShadowBanDetector(threshold=2)
        proxy = "http://proxy1:8080"

        # Build up counter
        detector.check_response(proxy, None)
        detector.check_response(proxy, None)

        # Quarantine
        detector.quarantine_proxy(proxy)
        assert detector.is_quarantined(proxy) is True

        stats = detector.stats()
        assert proxy in stats["quarantined"]
        assert stats["counters"][proxy] == 2
        assert stats["threshold"] == 2

        # Reset clears everything
        detector.reset(proxy)
        assert detector.is_quarantined(proxy) is False
        assert proxy not in detector.stats()["counters"]

    def test_empty_data_key_detection(self):
        """Dicts with 'data' key that is None or empty should count as empty."""
        detector = ShadowBanDetector(threshold=2)
        proxy = "http://proxy2:8080"

        assert detector.check_response(proxy, {"data": None}) is False  # count=1
        assert detector.check_response(proxy, {"data": []}) is True  # count=2, threshold

    def test_multiple_proxies_independent(self):
        """Each proxy should have its own independent counter."""
        detector = ShadowBanDetector(threshold=2)
        proxy_a = "http://proxyA:8080"
        proxy_b = "http://proxyB:8080"

        detector.check_response(proxy_a, None)
        detector.check_response(proxy_b, None)
        detector.check_response(proxy_a, None)

        # proxy_a hit threshold, proxy_b did not
        assert detector.stats()["counters"][proxy_a] == 2
        assert detector.stats()["counters"][proxy_b] == 1
