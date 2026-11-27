"""Fetch live OHLCV data and save it in the historical CSV directory.

Examples:
    uv run python data/save_live_data.py
    uv run python data/save_live_data.py --ticker AAPL --interval 5m
    uv run python data/save_live_data.py --tickers AMZN AAPL MSFT --interval 5m
"""

import argparse
from pathlib import Path

from live_data_loader import load_live_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch recent market data and save it as a CSV."
    )
    parser.add_argument(
        "--ticker",
        help="Ticker symbol to fetch (repeat --ticker for multiple symbols)",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="One or more ticker symbols, for example: --tickers AMZN AAPL MSFT",
    )
    parser.add_argument("--interval", default="5m", help="Bar interval (default: 5m)")
    parser.add_argument(
        "--max-ema-length",
        type=int,
        default=20,
        help="EMA length used to choose how much history to fetch (default: 20)",
    )
    args = parser.parse_args()

    tickers = args.tickers or ([args.ticker] if args.ticker else ["BE"])
    if args.ticker and args.tickers:
        parser.error("use either --ticker or --tickers, not both")

    output_dir = Path(__file__).resolve().parent / "historical_csvs"
    output_dir.mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        # Yahoo Finance uses hyphens for class-share symbols (for example BRK-B).
        data_ticker = ticker.replace(".", "-")
        try:
            bars = load_live_data(
                data_ticker,
                interval=args.interval,
                max_ema_length=args.max_ema_length,
            )
        except Exception as exc:
            print(f"Failed to fetch {ticker}: {exc}")
            continue

        start = bars.index.min().strftime("%Y-%m-%d")
        end = bars.index.max().strftime("%Y-%m-%d")
        filename = f"{ticker}_{args.interval}_{start}_to_{end}.csv"
        output_path = output_dir / filename
        bars.to_csv(output_path, index_label="datetime")

        print(f"Saved {len(bars)} bars for {ticker} to {output_path}")


if __name__ == "__main__":
    main()
