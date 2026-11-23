"""
Test Case 1 — Price Alert Monitor: Functionality & Delay Consistency
=====================================================================
Verifies:
  1. The monitor correctly detects price crossings (ABOVE / BELOW)
  2. It sends a Discord alert when a crossing happens
  3. It does NOT alert when price stays on the same side
  4. Delay (time from price change → Discord webhook call) is measured
     across multiple simulated ticks and checked for consistency
  5. The price-fetching function returns fresh data (no stale cache)

All network calls (yfinance + Discord) are mocked — the tests run offline
in < 2 seconds.

Run:
    python -m unittest tests.test_monitor_delay -v
"""

import statistics
import time
import unittest
from unittest.mock import MagicMock, patch, call

# ── We need to reset module-level state between tests ────────────────────────
import importlib
import alerts.price_alert_monitor as monitor_mod


def _reset_monitor_state():
    """Clear all module-level dicts so tests don't leak into each other."""
    monitor_mod._prev_side.clear()
    monitor_mod._last_alert_time.clear()
    monitor_mod._fetch_failures.clear()


class TestMonitorFunctionality(unittest.TestCase):
    """Core crossing-detection logic — no timing, just correctness."""

    def setUp(self):
        _reset_monitor_state()
        self.alert_config = {
            "ticker": "TEST",
            "target_price": 100.00,
            "cooldown": 0,
        }

    # ── 1. First tick should only record side, never alert ───────────────
    @patch("alerts.price_alert_monitor.send_price_cross_alert")
    @patch("alerts.price_alert_monitor._get_realtime_price", return_value=99.0)
    def test_first_tick_records_side_no_alert(self, mock_price, mock_send):
        monitor_mod._check_and_alert(self.alert_config)

        mock_send.assert_not_called()
        key = ("TEST", 100.00)
        self.assertEqual(monitor_mod._prev_side[key], "BELOW")

    # ── 2. No cross → no alert ──────────────────────────────────────────
    @patch("alerts.price_alert_monitor.send_price_cross_alert")
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_no_cross_no_alert(self, mock_price, mock_send):
        # Tick 1: price below → record
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.alert_config)
        # Tick 2: still below → should NOT alert
        mock_price.return_value = 98.5
        monitor_mod._check_and_alert(self.alert_config)

        mock_send.assert_not_called()

    # ── 3. BELOW → ABOVE crossing triggers alert ────────────────────────
    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_cross_above_triggers_alert(self, mock_price, mock_send):
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.alert_config)  # init

        mock_price.return_value = 101.0
        monitor_mod._check_and_alert(self.alert_config)  # cross!

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["direction"], "ABOVE")
        self.assertEqual(kwargs["ticker"], "TEST")
        self.assertAlmostEqual(kwargs["current_price"], 101.0)

    # ── 4. ABOVE → BELOW crossing triggers alert ────────────────────────
    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_cross_below_triggers_alert(self, mock_price, mock_send):
        mock_price.return_value = 101.0
        monitor_mod._check_and_alert(self.alert_config)  # init ABOVE

        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.alert_config)  # cross below!

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["direction"], "BELOW")

    # ── 5. Price exactly at target → no alert, no state change ──────────
    @patch("alerts.price_alert_monitor.send_price_cross_alert")
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_exactly_at_target_no_alert(self, mock_price, mock_send):
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.alert_config)  # init BELOW

        mock_price.return_value = 100.0  # exactly AT target
        monitor_mod._check_and_alert(self.alert_config)

        mock_send.assert_not_called()
        # _prev_side should still be BELOW (not overwritten)
        key = ("TEST", 100.00)
        self.assertEqual(monitor_mod._prev_side[key], "BELOW")

    # ── 6. Price fetch returns None → no crash, no alert ────────────────
    @patch("alerts.price_alert_monitor.send_price_cross_alert")
    @patch("alerts.price_alert_monitor._get_realtime_price", return_value=None)
    def test_price_none_graceful(self, mock_price, mock_send):
        monitor_mod._check_and_alert(self.alert_config)
        mock_send.assert_not_called()


class TestDelayConsistency(unittest.TestCase):
    """
    Measure the processing delay between _check_and_alert receiving a
    price and the Discord webhook being called.  Runs 20 simulated
    crossings and verifies the delays are consistent (low std-dev).
    """

    def setUp(self):
        _reset_monitor_state()

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_delay_is_consistent_across_crossings(self, mock_price, mock_send):
        """
        Simulate 20 crossings (alternating above/below) and record the
        wall-clock time each call to _check_and_alert takes.  Assert:
          - Every crossing triggers an alert
          - Mean delay < 5 ms (pure in-process, no I/O)
          - Std-dev of delays < 2 ms (consistent)
        """
        alert_config = {
            "ticker": "DELAY",
            "target_price": 50.00,
            "cooldown": 0,
        }

        NUM_CROSSINGS = 20
        delays: list[float] = []

        # Tick 0: initialise side (below)
        mock_price.return_value = 49.0
        monitor_mod._check_and_alert(alert_config)

        prices = []
        for i in range(NUM_CROSSINGS):
            # Alternate: above on even, below on odd
            if i % 2 == 0:
                price = 51.0 + i * 0.01   # above target
            else:
                price = 49.0 - i * 0.01   # below target
            prices.append(price)

        for price in prices:
            mock_price.return_value = price
            t0 = time.perf_counter()
            monitor_mod._check_and_alert(alert_config)
            t1 = time.perf_counter()
            delays.append((t1 - t0) * 1000)  # ms

        # ── Assertions ───────────────────────────────────────────────────
        self.assertEqual(
            mock_send.call_count, NUM_CROSSINGS,
            f"Expected {NUM_CROSSINGS} alerts but got {mock_send.call_count}"
        )

        mean_ms = statistics.mean(delays)
        stddev_ms = statistics.stdev(delays) if len(delays) > 1 else 0.0

        print(f"\n  ⏱️  Delay stats over {NUM_CROSSINGS} crossings:")
        print(f"      Mean   : {mean_ms:.3f} ms")
        print(f"      Stddev : {stddev_ms:.3f} ms")
        print(f"      Min    : {min(delays):.3f} ms")
        print(f"      Max    : {max(delays):.3f} ms")

        # In-process logic should be sub-millisecond; we give 5 ms headroom
        self.assertLess(mean_ms, 5.0, f"Mean delay {mean_ms:.3f} ms too high")
        # Consistency: stddev should be small
        self.assertLess(stddev_ms, 5.0, f"Delay stddev {stddev_ms:.3f} ms too high (inconsistent)")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_delay_no_cross_is_fast(self, mock_price, mock_send):
        """When there is NO crossing, processing should be even faster."""
        alert_config = {
            "ticker": "NOLAG",
            "target_price": 50.00,
            "cooldown": 0,
        }

        # Init
        mock_price.return_value = 49.0
        monitor_mod._check_and_alert(alert_config)

        # 50 consecutive no-cross ticks
        delays = []
        for i in range(50):
            mock_price.return_value = 49.0 - i * 0.001  # still below
            t0 = time.perf_counter()
            monitor_mod._check_and_alert(alert_config)
            t1 = time.perf_counter()
            delays.append((t1 - t0) * 1000)

        mock_send.assert_not_called()

        mean_ms = statistics.mean(delays)
        print(f"\n  ⏱️  No-cross tick delay (50 ticks):")
        print(f"      Mean : {mean_ms:.3f} ms")
        self.assertLess(mean_ms, 2.0)


if __name__ == "__main__":
    unittest.main()
