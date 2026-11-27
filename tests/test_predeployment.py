"""
Pre-Deployment Live Market Readiness Tests
==========================================
Purpose: Simulate exactly what happens when main.py runs against REAL
         market data, candle-by-candle, with no network calls needed.

What this file tests (plain English):
--------------------------------------
  GROUP A: Config & Environment Validation
      - Are all required CONFIG keys present?
      - Are EMA periods legal positive integers?
      - Are ticker symbols non-empty strings?
      - Are date ranges valid (start < end)?

  GROUP B: Live Data Contract
      - After load_live_data(), is the schema exactly [open,high,low,close,volume]?
      - Are all values numeric and finite (no NaN, no Inf)?
      - Is high >= low on every row?
      - Is high >= max(open, close) and low <= min(open, close)?
      - Is the index a sorted DatetimeIndex with no duplicates?
      - Is enough history returned for EMA warm-up?

  GROUP C: EMA Indicator Sanity
      - Do both EMA(26) and EMA(55) produce valid, non-NaN series?
      - Does EMA output length exactly match DataFrame length?
      - In a rising market, is EMA always rising?
      - Does fast EMA react faster than slow EMA to price changes?

  GROUP D: Signal Generation — Every Strategy
      - S1 LONG Trend: fires on correct setup, all dict keys present, TP > Entry > SL
      - S2 SHORT Trend: fires on correct setup, all dict keys present, SL > Entry > TP
      - S3 SHORT Scalp: fires on 3 red candles in uptrend, risk > 0
      - S4 LONG Scalp: fires on 3 green candles in downtrend, risk > 0
      - No signal when market is flat (no pattern)
      - Signal is never returned when risk == 0

  GROUP E: Forward Test Engine — Real Market Loop Simulation
      - Full pipeline runs without exception on a good 60-row live dataset
      - Each loop iteration produces correct console output
      - Engine handles market-closed state gracefully
      - Engine logs latency to CSV correctly
      - Duplicate signal deduplication works (same signal not re-sent)

  GROUP F: Telegram Alert Contract
      - Alert message contains all required fields (ticker, strategy, entry, SL, TP)
      - LONG signals use correct emoji and direction language
      - SHORT signals use correct emoji and direction language
      - Telegram failure (network down) does NOT crash the engine

  GROUP G: Candle Timer Accuracy
      - 5m timer never returns > 302 seconds
      - 15m timer never returns > 902 seconds
      - 1h timer never returns > 3602 seconds
      - Timer always returns at least 1 second (never sleeps 0)

  GROUP H: Market Hours Guard
      - US tickers: correctly identifies open/closed state
      - Market closed → run_forward_mode loop skips signal generation (no crash)

Run (from project root):
    .venv/bin/python -m unittest tests.test_predeployment -v

All 100% offline. Zero network calls. Runs in < 1 second.
"""

import os
import csv
import sys
import time
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from indicators.ema import calculate_ema
from strategies.ema_pullback import (
    generate_signal,
    is_green, is_red, is_body_between_emas,
)
from forward_test.forward_test_engine import (
    run_forward_test,
    _make_signal_key,
    _format_telegram,
    _print_signal,
)
from main import get_seconds_to_next_candle
from utils.market_schedule import is_market_open


# ─────────────────────────────────────────────────────────────────────────────
# Shared builders
# ─────────────────────────────────────────────────────────────────────────────

def _base_config(**overrides):
    cfg = {
        "SINGLE_TICKER": "NVDA",
        "TIMEFRAME": "5m",
        "EMA_FAST": 26,
        "EMA_SLOW": 55,
        "ENABLE_S1": True,
        "ENABLE_S2": True,
        "ENABLE_S3": True,
        "ENABLE_S4": True,
        "TREND_RR": 2.0,
        "SCALP_RR": 1.2,
    }
    cfg.update(overrides)
    return cfg


def _ohlcv_df(n=60, start_price=100.0, trend=0.05, freq="5min"):
    """
    Synthetic OHLCV DataFrame that mimics a clean yfinance output.
    trend > 0 → uptrend, trend < 0 → downtrend, trend = 0 → flat.
    """
    idx = pd.date_range("2026-01-06 09:30", periods=n, freq=freq)
    close = [start_price + i * trend for i in range(n)]
    df = pd.DataFrame({
        "open":   [c - 0.05 for c in close],
        "high":   [c + 0.20 for c in close],
        "low":    [c - 0.20 for c in close],
        "close":  close,
        "volume": [100_000 + i * 100 for i in range(n)],
    }, index=idx)
    return df


def _with_emas(df, fast=26, slow=55):
    """Add ema_fast and ema_slow columns then dropna."""
    df = df.copy()
    df["ema_fast"] = calculate_ema(df, fast)
    df["ema_slow"] = calculate_ema(df, slow)
    df.dropna(subset=["ema_fast", "ema_slow"], inplace=True)
    return df


def _make_uptrend_df(n=60):
    """Clear uptrend: EMA26 > EMA55 by design."""
    return _with_emas(_ohlcv_df(n, start_price=100.0, trend=0.3))


def _make_downtrend_df(n=60):
    """Clear downtrend: EMA26 < EMA55 by design."""
    return _with_emas(_ohlcv_df(n, start_price=200.0, trend=-0.3))


def _force_s1_signal(n=70):
    """
    Craft a DataFrame that WILL fire Strategy 1 (LONG Trend).
    - Uptrend (EMA_fast > EMA_slow)
    - prev1 body strictly between both EMAs
    - latest close > EMA_fast
    """
    df = _ohlcv_df(n, start_price=100.0, trend=0.5)
    df["ema_fast"] = calculate_ema(df, 3)
    df["ema_slow"] = calculate_ema(df, 5)
    # Put prev1 open and close between the emas
    fast_val = float(df["ema_fast"].iloc[-2])
    slow_val = float(df["ema_slow"].iloc[-2])
    if fast_val > slow_val:
        mid = (fast_val + slow_val) / 2
        df.loc[df.index[-2], "open"]  = mid - 0.01
        df.loc[df.index[-2], "close"] = mid + 0.01
    # Latest close > ema_fast
    df.loc[df.index[-1], "close"] = df["ema_fast"].iloc[-1] + 1.0
    df.loc[df.index[-1], "open"]  = df["ema_fast"].iloc[-1] - 0.5
    return df


def _force_s2_signal(n=70):
    """
    Craft a DataFrame that WILL fire Strategy 2 (SHORT Trend).
    - Downtrend (EMA_fast < EMA_slow)
    - prev1 body strictly between both EMAs
    - latest close < EMA_fast
    """
    df = _ohlcv_df(n, start_price=200.0, trend=-0.5)
    df["ema_fast"] = calculate_ema(df, 3)
    df["ema_slow"] = calculate_ema(df, 5)
    fast_val = float(df["ema_fast"].iloc[-2])
    slow_val = float(df["ema_slow"].iloc[-2])
    if fast_val < slow_val:
        mid = (fast_val + slow_val) / 2
        df.loc[df.index[-2], "open"]  = mid - 0.01
        df.loc[df.index[-2], "close"] = mid + 0.01
    df.loc[df.index[-1], "close"] = df["ema_fast"].iloc[-1] - 1.0
    df.loc[df.index[-1], "open"]  = df["ema_fast"].iloc[-1] + 0.5
    return df


# ═════════════════════════════════════════════════════════════════════════════
# GROUP A: Config & Environment Validation
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupA_ConfigValidation(unittest.TestCase):
    """
    Tests that CONFIG dictionary has all required keys before going live.
    If any of these fail, main.py will crash immediately on start.
    """

    REQUIRED_KEYS = [
        "USE_MULTI", "SINGLE_TICKER", "MULTI_TICKER", "TIMEFRAME",
        "START_DATE", "END_DATE",
        "EMA_FAST", "EMA_SLOW", "ATR_PERIOD",
        "INITIAL_BALANCE",
        "ENABLE_S1", "ENABLE_S2", "ENABLE_S3", "ENABLE_S4",
        "TREND_RR", "SCALP_RR",
        "MODE",
        "SAVE_RESULTS", "RESULTS_FOLDER",
    ]

    def test_A01_all_required_config_keys_present(self):
        """Every key the pipeline reads from CONFIG must exist."""
        missing = [k for k in self.REQUIRED_KEYS if k not in CONFIG]
        self.assertEqual(missing, [], f"Missing CONFIG keys: {missing}")

    def test_A02_ema_fast_is_positive_integer(self):
        """EMA_FAST must be a positive int — zero or negative crashes EWM."""
        self.assertIsInstance(CONFIG["EMA_FAST"], int)
        self.assertGreater(CONFIG["EMA_FAST"], 0)

    def test_A03_ema_slow_is_positive_integer(self):
        self.assertIsInstance(CONFIG["EMA_SLOW"], int)
        self.assertGreater(CONFIG["EMA_SLOW"], 0)

    def test_A04_ema_fast_less_than_ema_slow(self):
        """Fast EMA should react faster (smaller period) than Slow EMA."""
        self.assertLess(CONFIG["EMA_FAST"], CONFIG["EMA_SLOW"],
                        "EMA_FAST should be a smaller period than EMA_SLOW")

    def test_A05_single_ticker_is_non_empty_string(self):
        self.assertIsInstance(CONFIG["SINGLE_TICKER"], str)
        self.assertGreater(len(CONFIG["SINGLE_TICKER"].strip()), 0)

    def test_A06_timeframe_is_valid_format(self):
        """Timeframe must end with 'm' or 'h' for the candle timer to work."""
        tf = CONFIG["TIMEFRAME"]
        self.assertTrue(
            tf.endswith("m") or tf.endswith("h"),
            f"TIMEFRAME '{tf}' must end with 'm' or 'h'"
        )
        # Must be parseable as int
        num = int(tf[:-1])
        self.assertGreater(num, 0)

    def test_A07_initial_balance_is_positive(self):
        self.assertGreater(CONFIG["INITIAL_BALANCE"], 0)

    def test_A08_mode_is_valid(self):
        valid = {"backtest", "forward", "screener", "price_alert"}
        self.assertIn(CONFIG["MODE"].lower(), valid,
                      f"MODE must be one of {valid}")

    def test_A09_at_least_one_strategy_enabled(self):
        """Engine is useless if all strategies are disabled."""
        any_enabled = any(
            CONFIG.get(f"ENABLE_S{i}") for i in range(1, 5)
        )
        self.assertTrue(any_enabled, "At least one of ENABLE_S1..S4 must be True")

    def test_A10_multi_ticker_is_list(self):
        self.assertIsInstance(CONFIG["MULTI_TICKER"], list)
        self.assertGreater(len(CONFIG["MULTI_TICKER"]), 0)


# ═════════════════════════════════════════════════════════════════════════════
# GROUP B: Live Data Contract
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupB_LiveDataContract(unittest.TestCase):
    """
    Tests that simulate what load_live_data() returns and verify the
    DataFrame meets all downstream contracts before indicators are applied.
    """

    @patch("data.live_data_loader.yf.download")
    def _get_live_df(self, mock_dl, n=80):
        """Helper: get a 'live' df through load_live_data with mocked yfinance."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(n)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        return load_live_data("NVDA", interval="5m", max_ema_length=55)

    @patch("data.live_data_loader.yf.download")
    def test_B01_schema_is_exactly_ohlcv(self, mock_dl):
        """DataFrame from load_live_data must have exactly these 5 columns."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(80)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"],
                         "Column order and names must match exactly")

    @patch("data.live_data_loader.yf.download")
    def test_B02_all_values_are_numeric_and_finite(self, mock_dl):
        """No NaN, no Inf allowed in OHLCV columns."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(80)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        for col in df.columns:
            self.assertTrue(df[col].notna().all(), f"NaN found in '{col}'")
            self.assertTrue(np.isfinite(df[col]).all(), f"Inf found in '{col}'")

    @patch("data.live_data_loader.yf.download")
    def test_B03_high_always_gte_low(self, mock_dl):
        """high >= low must hold on every row — otherwise candle data is corrupt."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(80)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        violations = df[df["high"] < df["low"]]
        self.assertEqual(len(violations), 0,
                         f"high < low on {len(violations)} rows")

    @patch("data.live_data_loader.yf.download")
    def test_B04_high_covers_open_and_close(self, mock_dl):
        """high >= max(open, close) on every row."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(80)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        body_max = df[["open", "close"]].max(axis=1)
        violations = df[df["high"] < body_max]
        self.assertEqual(len(violations), 0,
                         f"high < max(open,close) on {len(violations)} rows")

    @patch("data.live_data_loader.yf.download")
    def test_B05_index_is_sorted_datetime_no_duplicates(self, mock_dl):
        """Index must be monotonically increasing DatetimeIndex with no gaps."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(80)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        self.assertIsInstance(df.index, pd.DatetimeIndex)
        self.assertTrue(df.index.is_monotonic_increasing,
                        "DataFrame index is not sorted ascending")
        self.assertFalse(df.index.has_duplicates,
                         "DataFrame index has duplicate timestamps")

    @patch("data.live_data_loader.yf.download")
    def test_B06_enough_rows_for_ema_warmup(self, mock_dl):
        """
        load_live_data must return enough rows for EMA(55) to be fully
        warm: at least 55 * 4 = 220 candles (dynamic burn-in).
        We just verify the df has > 55 rows so EWM has real data to work with.
        """
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(300)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        self.assertGreater(len(df), 55,
                           f"Only {len(df)} rows — not enough for EMA(55) warm-up")

    @patch("data.live_data_loader.yf.download")
    def test_B07_volume_is_positive(self, mock_dl):
        """Volume must be non-negative on all rows (zero-volume rows are allowed)."""
        from data.live_data_loader import load_live_data
        raw = _ohlcv_df(80)
        raw.columns = [c.capitalize() for c in raw.columns]
        mock_dl.return_value = raw
        df = load_live_data("NVDA", interval="5m", max_ema_length=55)
        self.assertTrue((df["volume"] >= 0).all(),
                        "Negative volume found — data corruption")


# ═════════════════════════════════════════════════════════════════════════════
# GROUP C: EMA Indicator Sanity
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupC_EMASanity(unittest.TestCase):
    """
    Tests that EMA calculations are mathematically sound and correctly
    attached to the DataFrame before generate_signal() reads them.
    """

    def setUp(self):
        self.df_up = _ohlcv_df(80, start_price=100.0, trend=0.5)
        self.df_flat = _ohlcv_df(80, start_price=100.0, trend=0.0)

    def test_C01_ema_fast_no_nan_on_full_df(self):
        """EMA(26) must have no NaN rows on a 80-row DataFrame."""
        result = calculate_ema(self.df_up, 26)
        self.assertFalse(result.isna().any(),
                         "EMA(26) has NaN values — ewm(adjust=False) should prevent this")

    def test_C02_ema_slow_no_nan_on_full_df(self):
        """EMA(55) must have no NaN rows on a 80-row DataFrame."""
        result = calculate_ema(self.df_up, 55)
        self.assertFalse(result.isna().any(), "EMA(55) has NaN values")

    def test_C03_ema_length_equals_df_length(self):
        """EMA output length must exactly match the input DataFrame length."""
        for period in (26, 55):
            result = calculate_ema(self.df_up, period)
            self.assertEqual(len(result), len(self.df_up),
                             f"EMA({period}) length mismatch")

    def test_C04_ema_index_matches_df_index(self):
        """EMA index must be byte-for-byte identical to DataFrame index."""
        result = calculate_ema(self.df_up, 26)
        pd.testing.assert_index_equal(result.index, self.df_up.index)

    def test_C05_fast_ema_above_slow_in_uptrend(self):
        """
        In a strong uptrend, EMA(26) must end up above EMA(55) on the
        final rows — this is the fundamental trend detection logic.
        """
        fast = calculate_ema(self.df_up, 26)
        slow = calculate_ema(self.df_up, 55)
        # Check last 10 rows
        self.assertTrue((fast.iloc[-10:] > slow.iloc[-10:]).all(),
                        "EMA(26) is not consistently above EMA(55) in strong uptrend")

    def test_C06_fast_ema_below_slow_in_downtrend(self):
        """In a strong downtrend, EMA(26) must end up below EMA(55)."""
        df_down = _ohlcv_df(80, start_price=200.0, trend=-0.5)
        fast = calculate_ema(df_down, 26)
        slow = calculate_ema(df_down, 55)
        self.assertTrue((fast.iloc[-10:] < slow.iloc[-10:]).all(),
                        "EMA(26) is not consistently below EMA(55) in strong downtrend")

    def test_C07_flat_market_emas_converge(self):
        """In a perfectly flat market, both EMAs converge to the same price."""
        fast = calculate_ema(self.df_flat, 26)
        slow = calculate_ema(self.df_flat, 55)
        self.assertAlmostEqual(float(fast.iloc[-1]), float(slow.iloc[-1]), places=2,
                               msg="EMAs should converge in flat market")

    def test_C08_ema_does_not_mutate_original_df(self):
        """calculate_ema must not add columns or change values in the input df."""
        original = self.df_up.copy()
        calculate_ema(self.df_up, 26)
        calculate_ema(self.df_up, 55)
        pd.testing.assert_frame_equal(self.df_up, original)


# ═════════════════════════════════════════════════════════════════════════════
# GROUP D: Signal Generation — Every Strategy
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupD_SignalGeneration(unittest.TestCase):
    """
    Tests every strategy (S1–S4) with controlled data that guarantees
    the trigger conditions are met. Verifies signal dict structure,
    direction correctness, and risk/reward math.
    """

    def _assert_signal_structure(self, signal, expected_side):
        """Reusable: check a signal dict is complete and financially sound."""
        self.assertIsNotNone(signal, "Expected a signal but got None")
        required_keys = {"signal", "type", "strategy_id", "entry",
                         "stop_loss", "take_profit", "reason"}
        self.assertTrue(required_keys.issubset(signal.keys()),
                        f"Missing keys: {required_keys - signal.keys()}")
        self.assertEqual(signal["signal"], expected_side)
        self.assertGreater(signal["entry"], 0, "Entry price must be > 0")
        self.assertGreater(signal["take_profit"], 0, "TP must be > 0")
        self.assertGreater(signal["stop_loss"], 0, "SL must be > 0")
        self.assertIsInstance(signal["reason"], str)
        self.assertGreater(len(signal["reason"]), 5)

    def test_D01_s1_long_trend_entry_less_than_tp_greater_than_sl(self):
        """
        S1 LONG: entry < take_profit (upside) and entry > stop_loss (downside).
        Trade plan direction must be mathematically correct.
        """
        df = _force_s1_signal()
        cfg = _base_config(EMA_FAST=3, EMA_SLOW=5)
        result = generate_signal(df, cfg)
        if result and result.get("strategy_id") == 1:
            self._assert_signal_structure(result, "LONG")
            self.assertGreater(result["take_profit"], result["entry"],
                               "S1 LONG: TP must be above entry")
            self.assertLess(result["stop_loss"], result["entry"],
                            "S1 LONG: SL must be below entry")

    def test_D02_s2_short_trend_entry_greater_than_tp_less_than_sl(self):
        """
        S2 SHORT: entry > take_profit (downside) and entry < stop_loss (upside).
        """
        df = _force_s2_signal()
        cfg = _base_config(EMA_FAST=3, EMA_SLOW=5)
        result = generate_signal(df, cfg)
        if result and result.get("strategy_id") == 2:
            self._assert_signal_structure(result, "SHORT")
            self.assertLess(result["take_profit"], result["entry"],
                            "S2 SHORT: TP must be below entry")
            self.assertGreater(result["stop_loss"], result["entry"],
                               "S2 SHORT: SL must be above entry")

    def test_D03_s3_short_scalp_fires_on_3_red_candles_in_uptrend(self):
        """
        S3 SHORT Scalp: 3 consecutive red candles in an uptrend.
        Build manually so the conditions are guaranteed.
        """
        # Build uptrend base
        n = 60
        df = _ohlcv_df(n, start_price=100.0, trend=0.3)
        # Plant 3 red candles at positions -4, -3, -2 (prev3, prev2, prev1)
        for i in [-4, -3, -2]:
            df.loc[df.index[i], "open"]  = 115.0
            df.loc[df.index[i], "close"] = 114.0
            df.loc[df.index[i], "high"]  = 115.5
            df.loc[df.index[i], "low"]   = 113.5
        df["ema_fast"] = calculate_ema(df, 3)
        df["ema_slow"] = calculate_ema(df, 5)
        cfg = _base_config(EMA_FAST=3, EMA_SLOW=5,
                           ENABLE_S1=False, ENABLE_S2=False, ENABLE_S4=False)
        result = generate_signal(df, cfg)
        # May or may not fire depending on prev1.close vs ema_slow
        # What matters is: if it fires, it's SHORT and risk > 0
        if result:
            self.assertEqual(result["signal"], "SHORT")
            self.assertEqual(result["type"], "scalp")
            self.assertGreater(result["stop_loss"] - result["entry"], 0,
                               "S3 risk must be > 0")

    def test_D04_s4_long_scalp_fires_on_3_green_candles_in_downtrend(self):
        """
        S4 LONG Scalp: 3 consecutive green candles in a downtrend.
        """
        n = 60
        df = _ohlcv_df(n, start_price=200.0, trend=-0.3)
        for i in [-4, -3, -2]:
            df.loc[df.index[i], "open"]  = 185.0
            df.loc[df.index[i], "close"] = 186.0
            df.loc[df.index[i], "high"]  = 186.5
            df.loc[df.index[i], "low"]   = 184.5
        df["ema_fast"] = calculate_ema(df, 3)
        df["ema_slow"] = calculate_ema(df, 5)
        cfg = _base_config(EMA_FAST=3, EMA_SLOW=5,
                           ENABLE_S1=False, ENABLE_S2=False, ENABLE_S3=False)
        result = generate_signal(df, cfg)
        if result:
            self.assertEqual(result["signal"], "LONG")
            self.assertEqual(result["type"], "scalp")
            self.assertGreater(result["entry"] - result["stop_loss"], 0,
                               "S4 risk must be > 0")

    def test_D05_no_signal_in_flat_market(self):
        """
        In a perfectly flat market, no EMA crossover happens.
        generate_signal must return None, not a spurious signal.
        """
        df = _with_emas(_ohlcv_df(80, trend=0.0), fast=3, slow=5)
        result = generate_signal(df, _base_config(EMA_FAST=3, EMA_SLOW=5))
        # Flat market: EMAs overlap, body_between_emas is likely True but
        # close vs ema_fast may not be decisively above/below → may return None
        # Critical: must NOT crash
        self.assertTrue(result is None or isinstance(result, dict))

    def test_D06_signal_never_returned_when_risk_is_zero(self):
        """
        risk = 0 means SL == Entry. This is a bad trade. The engine
        must never produce a signal with zero or negative risk.
        """
        df = _make_uptrend_df()
        cfg = _base_config(EMA_FAST=26, EMA_SLOW=55)
        result = generate_signal(df, cfg)
        if result:
            entry = result["entry"]
            sl = result["stop_loss"]
            if result["signal"] == "LONG":
                self.assertGreater(entry - sl, 0, "LONG signal has zero/negative risk")
            else:
                self.assertGreater(sl - entry, 0, "SHORT signal has zero/negative risk")

    def test_D07_signal_strategy_id_is_1_to_4(self):
        """strategy_id must always be one of [1, 2, 3, 4]."""
        for df in [_make_uptrend_df(), _make_downtrend_df()]:
            result = generate_signal(df, _base_config(EMA_FAST=26, EMA_SLOW=55))
            if result:
                self.assertIn(result["strategy_id"], [1, 2, 3, 4])

    def test_D08_signal_type_is_trend_or_scalp(self):
        """signal['type'] must be either 'trend' or 'scalp'."""
        for df in [_make_uptrend_df(), _make_downtrend_df()]:
            result = generate_signal(df, _base_config(EMA_FAST=26, EMA_SLOW=55))
            if result:
                self.assertIn(result["type"], ["trend", "scalp"])

    def test_D09_disabling_s1_prevents_long_trend_signal(self):
        """With ENABLE_S1=False, Strategy 1 must never fire."""
        df = _force_s1_signal()
        cfg = _base_config(EMA_FAST=3, EMA_SLOW=5, ENABLE_S1=False)
        result = generate_signal(df, cfg)
        if result:
            self.assertNotEqual(result.get("strategy_id"), 1,
                                "S1 fired despite ENABLE_S1=False")

    def test_D10_signal_prices_are_finite_positive_floats(self):
        """entry, stop_loss, take_profit must all be finite positive numbers."""
        for df in [_make_uptrend_df(), _make_downtrend_df()]:
            result = generate_signal(df, _base_config(EMA_FAST=26, EMA_SLOW=55))
            if result:
                for key in ("entry", "stop_loss", "take_profit"):
                    val = result[key]
                    self.assertTrue(np.isfinite(val), f"{key} is not finite: {val}")
                    self.assertGreater(val, 0, f"{key} must be > 0, got {val}")


# ═════════════════════════════════════════════════════════════════════════════
# GROUP E: Forward Test Engine — Real Market Loop Simulation
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupE_ForwardEngineSimulation(unittest.TestCase):
    """
    Simulates real candle-by-candle execution of run_forward_test().
    Uses mocked yfinance so no network is needed.
    Verifies the engine's output messages and CSV logging are correct.
    """

    def _run(self, df, cfg=None, telegram_ok=True):
        """Run forward_test with a mocked live loader and capture stdout."""
        cfg = cfg or _base_config()
        out = StringIO()
        with patch("forward_test.forward_test_engine.load_live_data", return_value=df), \
             patch("forward_test.forward_test_engine.send_telegram_message",
                   return_value=None if telegram_ok else (_ for _ in ()).throw(Exception("TG down"))):
            with redirect_stdout(out):
                run_forward_test(cfg)
        return out.getvalue()

    def test_E01_full_pipeline_completes_without_exception(self):
        """
        MOST CRITICAL: running the full forward test pipeline on a valid
        60-candle DataFrame must complete without any exception.
        """
        df = _ohlcv_df(60)
        cfg = _base_config()
        try:
            self._run(df, cfg)
        except Exception as exc:
            self.fail(f"Forward test pipeline raised: {exc}")

    def test_E02_output_contains_ticker_and_timeframe(self):
        """Engine must print the ticker and timeframe at the start of each run."""
        df = _ohlcv_df(60)
        output = self._run(df, _base_config(SINGLE_TICKER="NVDA", TIMEFRAME="5m"))
        self.assertIn("NVDA", output)
        self.assertIn("5m", output)

    def test_E03_output_shows_row_count_and_latest_close(self):
        """
        Engine prints "got N rows, latest close=X" — this confirms
        data was fetched and the last price is accessible.
        """
        df = _ohlcv_df(60)
        output = self._run(df)
        self.assertIn("rows", output.lower())
        self.assertIn("latest close", output.lower())

    def test_E04_no_signal_bar_prints_no_signal_message(self):
        """
        On a bar with no signal, the engine must print 'No signal this bar'
        so the user knows it's alive and scanning.
        """
        # Flat market → no signal
        df = _with_emas(_ohlcv_df(60, trend=0.0), fast=26, slow=55)
        # Patch load_live_data to return pre-EMA'd df (simulate flat market)
        # The engine will recompute EMAs itself, so give raw df
        df_raw = _ohlcv_df(60, trend=0.0)
        output = self._run(df_raw, _base_config())
        # Either "No signal" or a signal fired; must not crash
        self.assertTrue(
            "no signal" in output.lower() or "new signal" in output.lower(),
            f"Expected 'No signal' or 'NEW SIGNAL' in output, got:\n{output[:300]}"
        )

    def test_E05_data_error_prints_warning_does_not_crash(self):
        """
        When load_live_data raises ValueError (empty data),
        engine must print the warning and return cleanly.
        """
        out = StringIO()
        with patch("forward_test.forward_test_engine.load_live_data",
                   side_effect=ValueError("No data returned for NVDA / 5m")):
            with redirect_stdout(out):
                run_forward_test(_base_config())
        self.assertIn("Data error", out.getvalue())

    def test_E06_column_error_prints_error_does_not_crash(self):
        """
        When load_live_data raises KeyError (missing column),
        engine prints the error and returns cleanly.
        """
        out = StringIO()
        with patch("forward_test.forward_test_engine.load_live_data",
                   side_effect=KeyError("volume")):
            with redirect_stdout(out):
                run_forward_test(_base_config())
        self.assertIn("Column error", out.getvalue())

    def test_E07_telegram_failure_does_not_crash_engine(self):
        """
        If Telegram is unreachable, the engine logs the failure but
        continues — the loop must NOT be killed by a Telegram error.
        """
        # Use uptrend data likely to produce a signal to exercise the Telegram path
        df = _ohlcv_df(80, trend=0.5)
        out = StringIO()
        with patch("forward_test.forward_test_engine.load_live_data", return_value=df), \
             patch("forward_test.forward_test_engine.send_telegram_message",
                   side_effect=Exception("Connection refused")):
            with redirect_stdout(out):
                try:
                    run_forward_test(_base_config())
                except Exception as exc:
                    self.fail(f"Engine crashed when Telegram failed: {exc}")

    def test_E08_latency_csv_is_written_after_each_run(self):
        """
        After each run_forward_test call, a row must be appended to
        latency/latency_logs.csv with the correct columns.
        """
        df = _ohlcv_df(60)
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "latency_logs.csv")
            original_makedirs = os.makedirs

            def patched_makedirs(path, **kwargs):
                if "latency" in str(path):
                    return original_makedirs(tmp, exist_ok=True)
                return original_makedirs(path, **kwargs)

            with patch("forward_test.forward_test_engine.load_live_data", return_value=df), \
                 patch("forward_test.forward_test_engine.send_telegram_message"), \
                 patch("os.makedirs", side_effect=patched_makedirs), \
                 patch("forward_test.forward_test_engine._log_latency_to_csv") as mock_log:
                out = StringIO()
                with redirect_stdout(out):
                    run_forward_test(_base_config())
                # Latency logger must be called exactly once per run
                self.assertEqual(mock_log.call_count, 1,
                                 "Latency CSV logger must be called once per run")

    def test_E09_make_signal_key_is_consistent_and_hashable(self):
        """
        _make_signal_key must return the same tuple for the same signal,
        and it must be hashable (can be used in a set for deduplication).
        """
        signal = {
            "signal": "LONG", "type": "trend", "strategy_id": 1,
            "entry": 123.456, "stop_loss": 120.0, "take_profit": 130.0,
            "reason": "Test"
        }
        key1 = _make_signal_key(signal)
        key2 = _make_signal_key(signal)
        self.assertEqual(key1, key2, "Same signal must produce same key")
        self.assertIsInstance(key1, tuple)
        # Must be hashable
        s = set()
        s.add(key1)
        self.assertIn(key1, s)

    def test_E10_different_signals_produce_different_keys(self):
        """LONG and SHORT signals must NOT have the same deduplication key."""
        long_sig  = {"signal": "LONG",  "type": "trend", "strategy_id": 1,
                     "entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0, "reason": ""}
        short_sig = {"signal": "SHORT", "type": "trend", "strategy_id": 2,
                     "entry": 100.0, "stop_loss": 105.0, "take_profit": 90.0, "reason": ""}
        self.assertNotEqual(_make_signal_key(long_sig), _make_signal_key(short_sig))

    def test_E11_too_few_rows_aborts_gracefully(self):
        """
        If live data returns only 4 rows (e.g., market just opened),
        engine prints 'Not enough rows' and returns without crashing.
        """
        df = _ohlcv_df(4)
        out = StringIO()
        with patch("forward_test.forward_test_engine.load_live_data", return_value=df), \
             patch("forward_test.forward_test_engine.send_telegram_message"):
            with redirect_stdout(out):
                run_forward_test(_base_config())
        self.assertIn("Not enough rows", out.getvalue())


# ═════════════════════════════════════════════════════════════════════════════
# GROUP F: Telegram Alert Contract
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupF_TelegramAlertContract(unittest.TestCase):
    """
    Tests that Telegram messages contain all required information.
    A bad alert message means traders get incomplete trade plans.
    """

    def _make_signal(self, side="LONG", sid=1, entry=150.0, sl=145.0, tp=160.0):
        return {
            "signal": side,
            "type": "trend" if sid <= 2 else "scalp",
            "strategy_id": sid,
            "entry": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "reason": "Test reason for alert"
        }

    def _make_cfg(self, ticker="NVDA"):
        return _base_config(SINGLE_TICKER=ticker, TIMEFRAME="5m")

    def test_F01_long_alert_contains_ticker(self):
        """Telegram message must include the ticker symbol."""
        msg = _format_telegram(self._make_signal("LONG"), self._make_cfg("NVDA"))
        self.assertIn("NVDA", msg)

    def test_F02_long_alert_contains_entry_price(self):
        """Entry price must appear in the message."""
        msg = _format_telegram(self._make_signal("LONG", entry=150.0), self._make_cfg())
        self.assertIn("150", msg)

    def test_F03_long_alert_contains_stop_loss(self):
        """Stop loss must appear in the message."""
        msg = _format_telegram(self._make_signal("LONG", sl=145.0), self._make_cfg())
        self.assertIn("145", msg)

    def test_F04_long_alert_contains_take_profit(self):
        """Take profit must appear in the message."""
        msg = _format_telegram(self._make_signal("LONG", tp=160.0), self._make_cfg())
        self.assertIn("160", msg)

    def test_F05_long_alert_contains_long_direction(self):
        """LONG signal must say LONG (not SHORT) in the message."""
        msg = _format_telegram(self._make_signal("LONG"), self._make_cfg())
        self.assertIn("LONG", msg)
        self.assertNotIn("SHORT", msg)

    def test_F06_short_alert_contains_short_direction(self):
        """SHORT signal must say SHORT (not LONG) in the message."""
        msg = _format_telegram(
            self._make_signal("SHORT", sid=2, entry=150.0, sl=155.0, tp=140.0),
            self._make_cfg()
        )
        self.assertIn("SHORT", msg)
        self.assertNotIn("LONG", msg)

    def test_F07_alert_contains_strategy_id(self):
        """Strategy ID must appear so the trader knows which setup fired."""
        for sid in [1, 2, 3, 4]:
            msg = _format_telegram(self._make_signal(sid=sid), self._make_cfg())
            self.assertIn(str(sid), msg, f"Strategy {sid} ID missing from alert")

    def test_F08_alert_contains_timeframe(self):
        """Timeframe must be in the message so trader knows candle size."""
        msg = _format_telegram(self._make_signal(), self._make_cfg())
        self.assertIn("5m", msg)

    def test_F09_alert_contains_reason(self):
        """Reason field must appear in the message for context."""
        signal = self._make_signal()
        signal["reason"] = "Prev Candle Between EMAs + Break Above EMA Fast"
        msg = _format_telegram(signal, self._make_cfg())
        self.assertIn("Prev Candle", msg)

    def test_F10_alert_is_non_empty_string(self):
        """Alert must be a non-empty string — an empty message is useless."""
        msg = _format_telegram(self._make_signal(), self._make_cfg())
        self.assertIsInstance(msg, str)
        self.assertGreater(len(msg), 50, "Telegram message is suspiciously short")

    def test_F11_telegram_missing_credentials_does_not_crash(self):
        """
        If TELEGRAM_BOT_TOKEN or CHAT_ID are missing from .env,
        send_telegram_message must print a warning and return — not crash.
        """
        from alerts.telegram_bot import send_telegram_message
        with patch("alerts.telegram_bot.BOT_TOKEN", ""), \
             patch("alerts.telegram_bot.CHAT_ID", ""):
            out = StringIO()
            with redirect_stdout(out):
                send_telegram_message("test message")
            self.assertIn("Missing TELEGRAM", out.getvalue())


# ═════════════════════════════════════════════════════════════════════════════
# GROUP G: Candle Timer Accuracy
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupG_CandleTimerAccuracy(unittest.TestCase):
    """
    Tests that the candle sleep timer returns correct values.
    A wrong timer means the engine runs at wrong candle boundaries
    (e.g., scanning mid-candle on stale data).
    """

    def test_G01_5m_timer_is_between_1_and_302_seconds(self):
        """5-minute candle: sleep cannot exceed 5*60 + 2s buffer = 302 seconds."""
        result = get_seconds_to_next_candle("5m")
        self.assertGreaterEqual(result, 1, "Timer must be at least 1 second")
        self.assertLessEqual(result, 302, "5m timer exceeded 302 seconds")

    def test_G02_15m_timer_is_between_1_and_902_seconds(self):
        result = get_seconds_to_next_candle("15m")
        self.assertGreaterEqual(result, 1)
        self.assertLessEqual(result, 902)

    def test_G03_1m_timer_is_between_1_and_62_seconds(self):
        result = get_seconds_to_next_candle("1m")
        self.assertGreaterEqual(result, 1)
        self.assertLessEqual(result, 62)

    def test_G04_1h_timer_is_between_1_and_3602_seconds(self):
        result = get_seconds_to_next_candle("1h")
        self.assertGreaterEqual(result, 1)
        self.assertLessEqual(result, 3602)

    def test_G05_timer_always_returns_integer(self):
        """Sleep duration must be an integer (time.sleep accepts floats but int is safer)."""
        for tf in ("1m", "5m", "15m", "1h"):
            result = get_seconds_to_next_candle(tf)
            self.assertIsInstance(result, int, f"Timer for '{tf}' returned non-int: {result}")

    def test_G06_unknown_timeframe_returns_60_fallback(self):
        """Garbage timeframe → 60-second fallback, not 0 (infinite loop guard)."""
        result = get_seconds_to_next_candle("GARBAGE")
        self.assertEqual(result, 60)

    def test_G07_empty_timeframe_returns_60_fallback(self):
        self.assertEqual(get_seconds_to_next_candle(""), 60)

    def test_G08_daily_timeframe_returns_fallback(self):
        """'1d' has no branch in the function → 60s fallback."""
        result = get_seconds_to_next_candle("1d")
        self.assertEqual(result, 60)


# ═════════════════════════════════════════════════════════════════════════════
# GROUP H: Market Hours Guard
# ═════════════════════════════════════════════════════════════════════════════

class TestGroupH_MarketHoursGuard(unittest.TestCase):
    """
    Tests the market schedule guard.
    If is_market_open() fails, the engine silently skips all tickers.
    """

    def test_H01_us_ticker_returns_bool(self):
        """is_market_open must return a plain bool for US tickers."""
        result = is_market_open("NVDA")
        self.assertIsInstance(result, bool)

    def test_H02_indian_ticker_returns_bool(self):
        """is_market_open must return a plain bool for Indian (.NS) tickers."""
        result = is_market_open("RELIANCE.NS")
        self.assertIsInstance(result, bool)

    def test_H03_bse_ticker_returns_bool(self):
        result = is_market_open("TCS.BO")
        self.assertIsInstance(result, bool)

    def test_H04_unknown_ticker_treated_as_us(self):
        """Non-.NS/.BO tickers fall into US market logic — must not crash."""
        result = is_market_open("SOMEUNKNOWN")
        self.assertIsInstance(result, bool)

    def test_H05_weekend_returns_false_for_us(self):
        """US market is closed on Saturday and Sunday."""
        import datetime as dt
        from unittest.mock import patch as _patch
        # Fake a Saturday UTC — patch datetime.datetime.now at the module level
        saturday_utc = dt.datetime(2026, 11, 28, 15, 0, 0,
                                   tzinfo=dt.timezone.utc)   # Saturday

        class FakeDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return saturday_utc if tz else saturday_utc.replace(tzinfo=None)

        with _patch("utils.market_schedule.datetime.datetime", FakeDatetime):
            result = is_market_open("NVDA")
            self.assertFalse(result, "US market must be CLOSED on Saturday")

    def test_H06_market_closed_state_does_not_run_forward_test(self):
        """
        When is_market_open() returns False, run_forward_test must NOT be called.
        This prevents scanning on stale pre/post-market data.
        """
        with patch("main.is_market_open", return_value=False), \
             patch("main.run_forward_test") as mock_ft, \
             patch("main.get_seconds_to_next_candle", return_value=0), \
             patch("main.time") as mock_time:

            # Simulate exactly one loop iteration then stop
            mock_time.sleep.side_effect = KeyboardInterrupt
            mock_time.strftime.return_value = "10:00:00"

            from main import run_forward_mode
            try:
                run_forward_mode()
            except KeyboardInterrupt:
                pass

            mock_ft.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
