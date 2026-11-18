"""
two_one_screener.py — Root CLI entrypoint for 2/1 VWAP Screener
===============================================================
Delegates to screener.two_one_screener.

Run via main.py (recommended):
    uv run main.py --mode screener
    uv run main.py --mode screener --use_multi true

Or standalone:
    uv run two_one_screener.py
    uv run two_one_screener.py --tickers AAPL MSFT NVDA
    uv run two_one_screener.py --strict --csv results/screener.csv
"""

from screener.two_one_screener import main, run_screener

__all__ = ["main", "run_screener"]

if __name__ == "__main__":
    main()
