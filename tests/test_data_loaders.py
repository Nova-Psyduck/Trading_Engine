"""Offline data-level tests for the historical and live OHLCV loaders.

Run with:
    python -m unittest tests.test_data_loaders -v

The live loader's yfinance call is mocked, so these tests need no network or
market-data credentials.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data.historical_loader import load_historical_data
from data.live_data_loader import load_live_data


OHLCV = ["open", "high", "low", "close", "volume"]


def _bars(index=None):
    if index is None:
        index = pd.to_datetime(["2026-01-02 09:35", "2026-01-02 09:30"])
    return pd.DataFrame(
        {
            "Open": [101.0, 100.0],
            "High": [103.0, 102.0],
            "Low": [99.0, 98.0],
            "Close": [102.0, 101.0],
            "Volume": [1200, 1000],
        },
        index=index,
    )


class TestHistoricalDataLoader(unittest.TestCase):
    def test_loads_csv_as_sorted_datetime_index_with_valid_ohlcv(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_file = Path(tmp) / "historical_loader.py"
            csv_dir = Path(tmp) / "historical_csvs"
            csv_dir.mkdir()
            csv_path = csv_dir / "TEST_5m_2026-01-01_to_2026-01-03.csv"
            _bars().rename_axis("datetime").to_csv(csv_path)

            with patch("data.historical_loader.__file__", str(module_file)):
                result = load_historical_data(
                    "TEST", "5m", "2026-01-01", "2026-01-03"
                )

        self.assertEqual(list(result.columns), OHLCV)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result.index, pd.DatetimeIndex)
        self.assertTrue(result.index.is_monotonic_increasing)
        self.assertFalse(result.index.has_duplicates)
        self.assertTrue(result[OHLCV].notna().all().all())
        self.assertTrue(all(pd.api.types.is_numeric_dtype(result[c]) for c in OHLCV))
        self.assertTrue((result["high"] >= result["low"]).all())
        self.assertTrue((result["high"] >= result[["open", "close"]].max(axis=1)).all())
        self.assertTrue((result["low"] <= result[["open", "close"]].min(axis=1)).all())
        self.assertEqual(result.index[0], pd.Timestamp("2026-01-02 09:30"))

    def test_missing_csv_returns_empty_dataframe(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_file = Path(tmp) / "historical_loader.py"
            with patch("data.historical_loader.__file__", str(module_file)):
                result = load_historical_data(
                    "MISSING", "5m", "2026-01-01", "2026-01-03"
                )

        self.assertIsInstance(result, pd.DataFrame)
        self.assertTrue(result.empty)


class TestLiveDataLoader(unittest.TestCase):
    @patch("data.live_data_loader.yf.download")
    def test_normalizes_multiindex_columns_and_returns_ohlcv(self, mock_download):
        raw = _bars()
        raw.columns = pd.MultiIndex.from_tuples(
            [(name, "TEST") for name in raw.columns]
        )
        mock_download.return_value = raw

        result = load_live_data("TEST", interval="5m", max_ema_length=20)

        self.assertEqual(list(result.columns), OHLCV)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result.index, pd.DatetimeIndex)
        self.assertTrue(result[OHLCV].notna().all().all())
        self.assertTrue(all(pd.api.types.is_numeric_dtype(result[c]) for c in OHLCV))
        mock_download.assert_called_once()

    @patch("data.live_data_loader.yf.download")
    def test_drops_rows_with_missing_or_non_numeric_ohlcv(self, mock_download):
        raw = pd.DataFrame(
            {
                "OPEN": [100, 101, "bad"],
                "HIGH": [102, None, 103],
                "LOW": [98, 99, 100],
                "CLOSE": [101, 100, 102],
                "VOLUME": [1000, 1100, 1200],
                "EXTRA": ["ignored"] * 3,
            },
            index=pd.date_range("2026-01-02 09:30", periods=3, freq="5min"),
        )
        mock_download.return_value = raw

        result = load_live_data("TEST", interval="5m", max_ema_length=20)

        self.assertEqual(list(result.columns), OHLCV)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[OHLCV].notna().all().all())
        self.assertEqual(result.iloc[0]["close"], 101)

    @patch("data.live_data_loader.yf.download")
    def test_empty_provider_response_raises_clear_error(self, mock_download):
        mock_download.return_value = pd.DataFrame()

        with self.assertRaisesRegex(ValueError, "No data returned"):
            load_live_data("TEST", interval="5m", max_ema_length=20)

    @patch("data.live_data_loader.yf.download")
    def test_missing_ohlcv_column_raises_clear_error(self, mock_download):
        mock_download.return_value = pd.DataFrame(
            {"Open": [100], "High": [102], "Low": [98], "Close": [101]},
            index=pd.to_datetime(["2026-01-02 09:30"]),
        )

        with self.assertRaisesRegex(KeyError, "volume"):
            load_live_data("TEST", interval="5m", max_ema_length=20)

    @patch("data.live_data_loader.yf.download")
    def test_all_invalid_rows_raise_empty_after_cleaning_error(self, mock_download):
        mock_download.return_value = pd.DataFrame(
            {"Open": [None], "High": [None], "Low": [None],
             "Close": [None], "Volume": [None]},
            index=pd.to_datetime(["2026-01-02 09:30"]),
        )

        with self.assertRaisesRegex(ValueError, "empty after cleaning"):
            load_live_data("TEST", interval="5m", max_ema_length=20)


if __name__ == "__main__":
    unittest.main()
