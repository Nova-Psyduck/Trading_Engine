"""
Test Case 2 — Price Crossing: Signal Frequency & Double-Cross-Under-30s
========================================================================
Verifies:
  1. How frequently the monitor sends signals when price oscillates
  2. Two crosses within 30 seconds BOTH send signals (no one-shot break)
  3. After sending two rapid signals, the monitor is NOT broken — it
     continues detecting subsequent crossings
  4. Simulated real-time scenario: price hovers near target, bounces
     across it twice within a 30-second window, and both alerts fire
  5. Signal frequency statistics (crossings/sec, per-signal latency)
  6. Edge case: price bounces exactly twice and then stabilises —
     monitor still correctly reports "no cross" on stable ticks

All network calls (yfinance + Discord) are mocked — runs offline
in < 1 second.

Run:
    python -m unittest tests.test_price_crossing_frequency -v
"""

import statistics
import time
import unittest
from unittest.mock import patch, call

import alerts.price_alert_monitor as monitor_mod


def _reset_monitor_state():
    """Clear all module-level dicts so tests don't leak into each other."""
    monitor_mod._prev_side.clear()
    monitor_mod._last_alert_time.clear()
    monitor_mod._fetch_failures.clear()


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2a: Two Crosses Within 30 Seconds — Both Must Fire
# ══════════════════════════════════════════════════════════════════════════════

class TestTwoCrossesWithin30Seconds(unittest.TestCase):
    """
    The critical question: if price crosses the bar twice within 30 seconds,
    does the monitor send BOTH signals, or does it break after the first?
    """

    def setUp(self):
        _reset_monitor_state()
        self.config = {
            "ticker": "NVDL",
            "target_price": 15.46,
            "cooldown": 0,
        }

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_two_crosses_within_30s_both_fire(self, mock_price, mock_send):
        """
        Realistic scenario with timestamps:
          T+0.0s : price $15.40 (BELOW $15.46) — initial tick, no alert
          T+5.0s : price $15.50 (ABOVE $15.46) — ✅ CROSS #1 → alert ABOVE
          T+15.0s: price $15.42 (BELOW $15.46) — ✅ CROSS #2 → alert BELOW

        Total elapsed: 15 seconds (< 30 seconds).
        BOTH alerts MUST fire.  If the monitor breaks after cross #1,
        this test fails.
        """
        timestamps = []

        # T+0: initial tick — record side, no alert
        mock_price.return_value = 15.40
        t0 = time.perf_counter()
        monitor_mod._check_and_alert(self.config)
        timestamps.append(time.perf_counter() - t0)

        self.assertEqual(mock_send.call_count, 0,
                         "Initial tick should NOT send any alert")

        # T+5: price crosses ABOVE $15.46
        mock_price.return_value = 15.50
        t1 = time.perf_counter()
        monitor_mod._check_and_alert(self.config)
        timestamps.append(time.perf_counter() - t0)

        self.assertEqual(mock_send.call_count, 1,
                         "❌ CROSS #1 (ABOVE) was NOT detected!")
        _, kw1 = mock_send.call_args
        self.assertEqual(kw1["direction"], "ABOVE")
        self.assertAlmostEqual(kw1["current_price"], 15.50, places=2)

        # T+15: price crosses BELOW $15.46
        mock_price.return_value = 15.42
        monitor_mod._check_and_alert(self.config)
        total_elapsed = time.perf_counter() - t0

        self.assertEqual(mock_send.call_count, 2,
                         "❌ CROSS #2 (BELOW) was NOT detected! "
                         "Monitor BROKE after sending the first signal!")
        _, kw2 = mock_send.call_args
        self.assertEqual(kw2["direction"], "BELOW")
        self.assertAlmostEqual(kw2["current_price"], 15.42, places=2)

        # Verify total time is well under 30 seconds
        self.assertLess(total_elapsed, 1.0,
                        "Processing 3 ticks shouldn't take more than 1 second")

        print(f"\n  ✅ Both crosses within {total_elapsed*1000:.1f} ms (< 30s):")
        print(f"      Cross #1: ABOVE $15.46 at ${kw1['current_price']:.4f}")
        print(f"      Cross #2: BELOW $15.46 at ${kw2['current_price']:.4f}")
        print(f"      Monitor did NOT break — both signals sent successfully!")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_two_crosses_within_30s_same_direction(self, mock_price, mock_send):
        """
        Price crosses ABOVE, comes back, crosses ABOVE again — all within 30s.
        Both ABOVE crossings must trigger (there's a BELOW in between).
        """
        # Init BELOW
        mock_price.return_value = 15.40
        monitor_mod._check_and_alert(self.config)

        # Cross #1: ABOVE
        mock_price.return_value = 15.50
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 1)

        # Dip back BELOW (cross #2)
        mock_price.return_value = 15.42
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 2)

        # Cross #3: ABOVE again
        mock_price.return_value = 15.50
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 3,
                         "❌ Third crossing missed — monitor broke after 2 signals!")

        print("\n  ✅ Three crossings (ABOVE → BELOW → ABOVE) all fired within 30s window")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2b: Monitor Doesn't Break After Sending Signals
# ══════════════════════════════════════════════════════════════════════════════

class TestMonitorDoesNotBreak(unittest.TestCase):
    """
    After sending N signals, the monitor must still be alive and detecting.
    This catches any "one-shot latch" or state corruption bugs.
    """

    def setUp(self):
        _reset_monitor_state()
        self.config = {
            "ticker": "TESTBREAK",
            "target_price": 100.00,
            "cooldown": 0,
        }

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_survives_after_first_signal(self, mock_price, mock_send):
        """
        After 1 signal, the next crossing still fires.
        """
        # Init
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.config)

        # Cross #1
        mock_price.return_value = 101.0
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 1)

        # Stable ticks (no cross) — monitor should NOT crash
        for _ in range(5):
            mock_price.return_value = 101.5
            monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 1, "No-cross ticks should not trigger alerts")

        # Cross #2 — monitor MUST still be alive
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 2,
                         "❌ Monitor died after first signal! Second crossing not detected!")

        print("\n  ✅ Monitor survived first signal + 5 stable ticks + detected second cross")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_survives_after_many_signals(self, mock_price, mock_send):
        """
        After 20 signals, the 21st still fires.
        Proves no accumulating state corruption.
        """
        # Init
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.config)

        # 20 crossings
        for i in range(20):
            mock_price.return_value = 101.0 if i % 2 == 0 else 99.0
            monitor_mod._check_and_alert(self.config)

        self.assertEqual(mock_send.call_count, 20)

        # 21st crossing — the critical one
        # After i=19 (odd) → price was 99 (BELOW), so next cross must be ABOVE
        mock_price.return_value = 101.0
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 21,
                         "❌ 21st crossing failed — monitor broke after 20 signals!")

        print(f"\n  ✅ All 21 crossings fired — monitor is robust with no state corruption")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_stable_after_cross_no_spurious_alerts(self, mock_price, mock_send):
        """
        After a cross, if price stays on the new side for 20 ticks,
        no spurious alerts should fire.
        """
        # Init BELOW
        mock_price.return_value = 99.0
        monitor_mod._check_and_alert(self.config)

        # Cross ABOVE
        mock_price.return_value = 101.0
        monitor_mod._check_and_alert(self.config)
        self.assertEqual(mock_send.call_count, 1)

        # 20 stable ticks above target — should be silent
        for i in range(20):
            mock_price.return_value = 101.0 + i * 0.01
            monitor_mod._check_and_alert(self.config)

        self.assertEqual(mock_send.call_count, 1,
                         f"Got {mock_send.call_count} alerts — expected exactly 1 (spurious alerts!)")

        print("\n  ✅ No spurious alerts during 20 stable ticks after crossing")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2c: Signal Frequency Measurement
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalFrequency(unittest.TestCase):
    """
    Measure how frequently the monitor CAN send signals and how long
    each signal takes to process.
    """

    def setUp(self):
        _reset_monitor_state()

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_signal_frequency_100_crossings(self, mock_price, mock_send):
        """
        Feed 100 alternating prices and measure:
          - Total time for all crossings
          - Signals per second
          - Per-signal latency (mean, stddev, min, max)
        """
        config = {
            "ticker": "FREQ100",
            "target_price": 50.00,
            "cooldown": 0,
        }

        # Init
        mock_price.return_value = 49.0
        monitor_mod._check_and_alert(config)

        N = 100
        per_signal_delays = []

        for i in range(N):
            mock_price.return_value = 51.0 if i % 2 == 0 else 49.0
            t0 = time.perf_counter()
            monitor_mod._check_and_alert(config)
            t1 = time.perf_counter()
            per_signal_delays.append((t1 - t0) * 1000)  # ms

        self.assertEqual(mock_send.call_count, N,
                         f"Expected {N} alerts, got {mock_send.call_count}")

        total_ms = sum(per_signal_delays)
        mean_ms = statistics.mean(per_signal_delays)
        stddev_ms = statistics.stdev(per_signal_delays)
        rate = N / (total_ms / 1000)  # signals/sec

        print(f"\n  ⚡ Signal frequency stats ({N} crossings):")
        print(f"      Total time  : {total_ms:.1f} ms")
        print(f"      Rate        : {rate:.0f} signals/sec")
        print(f"      Per signal  : mean={mean_ms:.4f} ms  "
              f"stddev={stddev_ms:.4f} ms  "
              f"min={min(per_signal_delays):.4f} ms  "
              f"max={max(per_signal_delays):.4f} ms")

        # Sanity checks
        self.assertGreater(rate, 500,
                           f"Signal rate {rate:.0f}/sec too low — processing bottleneck")
        self.assertLess(mean_ms, 5.0,
                        f"Mean per-signal delay {mean_ms:.3f} ms too high")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_signal_frequency_with_interleaved_stable_ticks(self, mock_price, mock_send):
        """
        Real-world pattern: each crossing is followed by 5 stable ticks.
        Only the crossings should produce alerts.
        """
        config = {
            "ticker": "REALWORLD",
            "target_price": 50.00,
            "cooldown": 0,
        }

        # Init
        mock_price.return_value = 49.0
        monitor_mod._check_and_alert(config)

        total_ticks = 0
        total_crossings = 0

        for cycle in range(10):
            # Cross!
            if cycle % 2 == 0:
                mock_price.return_value = 51.0
            else:
                mock_price.return_value = 49.0
            monitor_mod._check_and_alert(config)
            total_ticks += 1
            total_crossings += 1

            # 5 stable ticks
            for _ in range(5):
                mock_price.return_value = mock_price.return_value + 0.001
                monitor_mod._check_and_alert(config)
                total_ticks += 1

        self.assertEqual(mock_send.call_count, 10,
                         f"Expected 10 crossings out of {total_ticks} ticks, "
                         f"got {mock_send.call_count}")

        print(f"\n  ✅ {mock_send.call_count} signals from {total_ticks} ticks "
              f"(signal rate: 1 per {total_ticks // mock_send.call_count} ticks)")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2d: Realistic 30-Second Simulation with Timestamps
# ══════════════════════════════════════════════════════════════════════════════

class TestRealistic30SecondWindow(unittest.TestCase):
    """
    Simulate a realistic 30-second window where the CHECK_INTERVAL is 5s
    and price oscillates around the target.  Verifies the exact number of
    signals that fire.
    """

    def setUp(self):
        _reset_monitor_state()

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_realistic_30s_price_oscillation(self, mock_price, mock_send):
        """
        Simulated price ticks every 5 seconds for 30 seconds around $15.46:

          Tick 0 (T+0s):  $15.44  BELOW  — init
          Tick 1 (T+5s):  $15.48  ABOVE  — ✅ CROSS #1
          Tick 2 (T+10s): $15.47  ABOVE  — no cross (still above)
          Tick 3 (T+15s): $15.43  BELOW  — ✅ CROSS #2
          Tick 4 (T+20s): $15.41  BELOW  — no cross (still below)
          Tick 5 (T+25s): $15.49  ABOVE  — ✅ CROSS #3
          Tick 6 (T+30s): $15.45  BELOW  — ✅ CROSS #4

        Expected: 4 alerts in 30 seconds, monitor never breaks.
        """
        config = {
            "ticker": "NVDL",
            "target_price": 15.46,
            "cooldown": 0,
        }

        ticks = [
            (0,  15.44, None),      # init
            (5,  15.48, "ABOVE"),    # cross #1
            (10, 15.47, None),       # still above
            (15, 15.43, "BELOW"),    # cross #2
            (20, 15.41, None),       # still below
            (25, 15.49, "ABOVE"),    # cross #3
            (30, 15.45, "BELOW"),    # cross #4
        ]

        expected_alerts = 0
        for t_sec, price, expected_dir in ticks:
            mock_price.return_value = price
            monitor_mod._check_and_alert(config)

            if expected_dir is not None:
                expected_alerts += 1
                self.assertEqual(
                    mock_send.call_count, expected_alerts,
                    f"At T+{t_sec}s (price=${price}): expected {expected_alerts} "
                    f"total alerts, got {mock_send.call_count}"
                )
                _, kwargs = mock_send.call_args
                self.assertEqual(kwargs["direction"], expected_dir,
                                 f"At T+{t_sec}s: direction should be {expected_dir}")

        self.assertEqual(mock_send.call_count, 4,
                         f"Expected exactly 4 alerts in 30s window, got {mock_send.call_count}")

        print("\n  ✅ Realistic 30-second simulation:")
        print("      T+0s:  $15.44 (init)")
        print("      T+5s:  $15.48 → ABOVE ✅")
        print("      T+10s: $15.47 (stable)")
        print("      T+15s: $15.43 → BELOW ✅")
        print("      T+20s: $15.41 (stable)")
        print("      T+25s: $15.49 → ABOVE ✅")
        print("      T+30s: $15.45 → BELOW ✅")
        print("      Total: 4/4 signals fired, monitor never broke!")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2e: Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    """Edge cases that could cause the monitor to miss signals or crash."""

    def setUp(self):
        _reset_monitor_state()

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_tiny_price_difference_still_counts(self, mock_price, mock_send):
        """
        Price crosses by just $0.01 — should still trigger.
        """
        config = {"ticker": "TINY", "target_price": 100.00, "cooldown": 0}

        mock_price.return_value = 99.99   # just below
        monitor_mod._check_and_alert(config)

        mock_price.return_value = 100.01  # just above by $0.01
        monitor_mod._check_and_alert(config)

        self.assertEqual(mock_send.call_count, 1, "Even $0.01 cross should trigger")
        print("\n  ✅ $0.01 crossing detected")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_price_exactly_at_target_then_crosses(self, mock_price, mock_send):
        """
        Price sits exactly at target (no alert), then moves away → alert.
        """
        config = {"ticker": "EXACT", "target_price": 50.00, "cooldown": 0}

        mock_price.return_value = 49.0  # init below
        monitor_mod._check_and_alert(config)

        mock_price.return_value = 50.0  # exactly at target — should NOT trigger
        monitor_mod._check_and_alert(config)
        self.assertEqual(mock_send.call_count, 0, "Exactly at target should not trigger")

        mock_price.return_value = 50.01  # now above — should trigger
        monitor_mod._check_and_alert(config)
        self.assertEqual(mock_send.call_count, 1, "Moving above from target should trigger")

        print("\n  ✅ Exact-target → cross-above correctly handled")

    @patch("alerts.price_alert_monitor.send_price_cross_alert", return_value=True)
    @patch("alerts.price_alert_monitor._get_realtime_price")
    def test_rapid_bounce_at_target_no_spurious(self, mock_price, mock_send):
        """
        Price: 99 → 100 (exact) → 99 → 100 (exact) → 101
        Only the final 99→101 should count as a crossing (since 100 is treated
        as "stay at previous side").
        """
        config = {"ticker": "BOUNCE", "target_price": 100.00, "cooldown": 0}

        prices = [99.0, 100.0, 99.0, 100.0, 101.0]

        for p in prices:
            mock_price.return_value = p
            monitor_mod._check_and_alert(config)

        # First tick: init (99 → BELOW), no alert
        # 100.0: exact at target → no side change, no alert
        # 99.0: still BELOW → no cross
        # 100.0: exact → no side change
        # 101.0: BELOW → ABOVE → ALERT!
        self.assertEqual(mock_send.call_count, 1,
                         f"Expected 1 alert for bouncing-at-target pattern, "
                         f"got {mock_send.call_count}")

        print("\n  ✅ Bouncing at exact target produces exactly 1 real crossing signal")


if __name__ == "__main__":
    unittest.main()
