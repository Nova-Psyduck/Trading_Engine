import unittest
import pandas as pd
import numpy as np
from indicators.ema import calculate_ema
from indicators.atr import calculate_atr
from indicators.vwap import calculate_vwap

class TestIndicators(unittest.TestCase):
    def setUp(self):
        # Create a simple trend: 10, 11, 12, 13, 14
        self.df = pd.DataFrame({
            "open": [10, 11, 12, 13, 14],
            "high": [10.5, 11.5, 12.5, 13.5, 14.5],
            "low": [9.5, 10.5, 11.5, 12.5, 13.5],
            "close": [10, 11, 12, 13, 14],
            "volume": [100, 100, 100, 100, 100]
        }, index=pd.date_range("2026-01-01 09:30", periods=5, freq="5min"))

    def test_ema_calculation(self):
        # EMA(period=3)
        # alpha = 2 / (3 + 1) = 0.5
        # EMA[0] = Price[0] = 10
        # EMA[1] = 0.5 * 11 + 0.5 * 10 = 10.5
        # EMA[2] = 0.5 * 12 + 0.5 * 10.5 = 11.25
        ema = calculate_ema(self.df, 3)
        self.assertAlmostEqual(ema.iloc[0], 10.0)
        self.assertAlmostEqual(ema.iloc[1], 10.5)
        self.assertAlmostEqual(ema.iloc[2], 11.25)

    def test_atr_calculation(self):
        # High-Low is always 1.0
        # PrevClose-High/Low is also 1.5/0.5
        # TR = max(1.0, 1.5, 0.5) = 1.5 (except first row which is 1.0)
        # ATR(period=2)
        atr = calculate_atr(self.df, 2)
        # Row 0: TR=1.0
        # Row 1: TR=max(11.5-10.5, abs(11.5-10), abs(10.5-10)) = max(1.0, 1.5, 0.5) = 1.5
        # Row 2: TR=max(12.5-11.5, abs(12.5-11), abs(11.5-11)) = max(1.0, 1.5, 0.5) = 1.5
        # First true range is high-low = 1.0, so the first 2-bar ATR is 1.25.
        self.assertAlmostEqual(atr.iloc[1], 1.25)
        self.assertAlmostEqual(atr.iloc[2], 1.5)

    def test_vwap_calculation(self):
        # Typical Price = (10.5 + 9.5 + 10) / 3 = 10.0
        # VWAP = sum(TP * V) / sum(V)
        # Row 0: TP=10, V=100 -> VWAP=10.0
        # Row 1: TP=11, V=100 -> VWAP=(1000 + 1100) / 200 = 10.5
        # Our implementation of calculate_vwap returns a single float for the whole df
        vwap = calculate_vwap(self.df)
        expected_tp = ((self.df["high"] + self.df["low"] + self.df["close"]) / 3.0)
        expected_vwap = (expected_tp * self.df["volume"]).sum() / self.df["volume"].sum()
        self.assertAlmostEqual(vwap, expected_vwap)

    def test_ema_constant_prices_and_index_alignment(self):
        constant = self.df.assign(close=42.0)
        result = calculate_ema(constant, 3)
        pd.testing.assert_index_equal(result.index, constant.index)
        np.testing.assert_allclose(result.to_numpy(), np.full(len(constant), 42.0))

    def test_ema_short_series_and_custom_column(self):
        short = self.df.iloc[:2]
        result = calculate_ema(short, 5, column="close")
        self.assertEqual(len(result), len(short))
        self.assertAlmostEqual(result.iloc[0], 10.0)
        self.assertAlmostEqual(result.iloc[1], 10.0 + (2 / 6))

    def test_ema_invalid_period_raises(self):
        for period in (0, -1):
            with self.subTest(period=period), self.assertRaises(ValueError):
                calculate_ema(self.df, period)

    def test_ema_empty_input_returns_empty_series_and_missing_column_raises(self):
        self.assertTrue(calculate_ema(self.df.iloc[:0], 3).empty)
        with self.assertRaises(KeyError):
            calculate_ema(self.df, 3, column="adjusted_close")

    def test_ema_missing_price_is_handled_without_losing_alignment(self):
        data = self.df.copy()
        data.loc[data.index[2], "close"] = np.nan
        result = calculate_ema(data, 3)
        pd.testing.assert_index_equal(result.index, data.index)
        self.assertEqual(len(result), len(data))
        self.assertTrue(np.isfinite(result.iloc[-1]))

    def test_atr_gap_uses_previous_close(self):
        bars = pd.DataFrame({
            "high": [11.0, 14.0], "low": [9.0, 12.0], "close": [10.0, 13.0]
        })
        result = calculate_atr(bars, 1)
        # Second bar's true range is 4: the gap from prior close (10) to high (14).
        self.assertAlmostEqual(result.iloc[1], 4.0)

    def test_atr_warmup_short_series_and_index_alignment(self):
        short = self.df.iloc[:1]
        result = calculate_atr(short, 2)
        pd.testing.assert_index_equal(result.index, short.index)
        self.assertTrue(result.isna().all())

    def test_atr_constant_range_and_invalid_period(self):
        bars = self.df.copy()
        bars["high"] = 11.0
        bars["low"] = 9.0
        bars["close"] = 10.0
        result = calculate_atr(bars, 2)
        self.assertAlmostEqual(result.iloc[-1], 2.0)
        for period in (0, -1):
            with self.subTest(period=period), self.assertRaises(ValueError):
                calculate_atr(bars, period)

    def test_atr_missing_values_preserve_index_and_warmup(self):
        bars = self.df.copy()
        bars.loc[bars.index[2], "high"] = np.nan
        result = calculate_atr(bars, 2)
        pd.testing.assert_index_equal(result.index, bars.index)
        self.assertEqual(len(result), len(bars))

    def test_atr_missing_required_column_raises(self):
        with self.assertRaisesRegex(KeyError, "close"):
            calculate_atr(self.df.drop(columns="close"), 2)

    def test_vwap_constant_price_and_zero_volume(self):
        bars = self.df.copy()
        bars[["high", "low", "close"]] = 25.0
        self.assertAlmostEqual(calculate_vwap(bars), 25.0)
        bars["volume"] = 0
        self.assertAlmostEqual(calculate_vwap(bars), 25.0)  # documented zero-volume fallback: last close

    def test_vwap_empty_and_missing_columns(self):
        self.assertEqual(calculate_vwap(pd.DataFrame()), 0.0)
        with self.assertRaisesRegex(KeyError, "high"):
            calculate_vwap(pd.DataFrame({"close": [10.0], "volume": [1]}))

    def test_vwap_does_not_divide_by_zero_with_zero_volume_rows(self):
        bars = self.df.iloc[:2].copy()
        bars.loc[bars.index[0], "volume"] = 0
        result = calculate_vwap(bars)
        self.assertTrue(np.isfinite(result))
        self.assertAlmostEqual(result, 11.0)  # only the second bar contributes weight

    def test_vwap_normalizes_names_and_multiindex_columns(self):
        bars = self.df[["high", "low", "close", "volume"]].copy()
        bars.columns = pd.MultiIndex.from_tuples([(name.upper(), "TEST") for name in bars.columns])
        self.assertAlmostEqual(calculate_vwap(bars), calculate_vwap(self.df))

    def test_indicators_do_not_mutate_input(self):
        original = self.df.copy(deep=True)
        calculate_ema(self.df, 3)
        calculate_atr(self.df, 2)
        calculate_vwap(self.df)
        pd.testing.assert_frame_equal(self.df, original)

if __name__ == "__main__":
    unittest.main()
