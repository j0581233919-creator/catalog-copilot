"""Tests for thread-safe sliding-window RateLimiter in CatalogCopilot."""

from __future__ import annotations
import unittest
from catalog_copilot.rate_limiter import RateLimiter


class TestRateLimiter(unittest.TestCase):
    """Verifies sliding window rate limit enforcement, replenishing, and key isolation."""

    def setUp(self):
        self.simulated_time = 1000.0
        self.limiter = RateLimiter(
            max_requests=5,
            window_seconds=10.0,
            time_fn=lambda: self.simulated_time,
        )

    def test_basic_consumption_within_limit(self):
        key = "sender_1"
        for i in range(5):
            allowed, info = self.limiter.check_and_consume(key)
            self.assertTrue(allowed)
            self.assertEqual(info["remaining"], 4 - i)

        # 6th request must be rejected
        allowed, info = self.limiter.check_and_consume(key)
        self.assertFalse(allowed)
        self.assertEqual(info["remaining"], 0)
        self.assertGreater(info["retry_after_seconds"], 0.0)

    def test_window_expiration_replenishes_tokens(self):
        key = "sender_2"
        # Consume all 5
        for _ in range(5):
            self.assertTrue(self.limiter.check_and_consume(key)[0])

        self.assertFalse(self.limiter.is_allowed(key))

        # Advance time by 5 seconds (still within 10s window)
        self.simulated_time += 5.0
        self.assertFalse(self.limiter.is_allowed(key))

        # Advance time past 10 seconds from start (1000.0 -> 1011.0)
        self.simulated_time += 6.0
        # All 5 requests made at t=1000.0 are now expired
        self.assertTrue(self.limiter.is_allowed(key))
        allowed, info = self.limiter.check_and_consume(key)
        self.assertTrue(allowed)
        self.assertEqual(info["remaining"], 4)

    def test_per_key_isolation(self):
        key_a = "conversation_101"
        key_b = "conversation_102"

        # Exhaust key A
        for _ in range(5):
            self.assertTrue(self.limiter.check_and_consume(key_a)[0])

        self.assertFalse(self.limiter.is_allowed(key_a))
        # Key B must still have full capacity
        self.assertTrue(self.limiter.is_allowed(key_b))
        allowed, info = self.limiter.check_and_consume(key_b)
        self.assertTrue(allowed)
        self.assertEqual(info["remaining"], 4)

    def test_reset_and_cleanup(self):
        self.limiter.check_and_consume("k1")
        self.limiter.check_and_consume("k2")

        self.limiter.reset("k1")
        allowed, info = self.limiter.check_and_consume("k1")
        self.assertEqual(info["remaining"], 4)

        # Global reset
        self.limiter.reset()
        self.assertEqual(len(self.limiter._timestamps), 0)

        # Test cleanup_expired
        self.limiter.check_and_consume("k3")
        self.simulated_time += 15.0  # Expire k3
        cleaned = self.limiter.cleanup_expired()
        self.assertEqual(cleaned, 1)
        self.assertNotIn("k3", self.limiter._timestamps)

    def test_invalid_parameters_raise_error(self):
        with self.assertRaises(ValueError):
            RateLimiter(max_requests=0)
        with self.assertRaises(ValueError):
            RateLimiter(window_seconds=-1.0)


if __name__ == "__main__":
    unittest.main()
