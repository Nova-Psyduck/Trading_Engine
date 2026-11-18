import os
import time
import argparse
from datetime import datetime
from config import CONFIG
from data.historical_loader import load_historical_data
from indicators.ema import calculate_ema
from indicators.atr import calculate_atr
from backtest.backtest_engine import run_backtest
from forward_test.forward_test_engine import run_forward_test
from utils.market_schedule import is_market_open
from utils.logger import get_logger

log = get_logger("main")

def get_seconds_to_next_candle(timeframe: str) -> int:
    """Calculate the number of seconds until the next candlestick boundary."""
    now = datetime.now()
    try:
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
            next_minute = ((now.minute // minutes) + 1) * minutes
            delta_minutes = next_minute - now.minute
            wait_time = (delta_minutes * 60) - now.second
            return max(1, int(wait_time)) + 2  # 2s buffer for API update
            
        elif timeframe.endswith('h'):
            hours = int(timeframe[:-1])
            next_hour = ((now.hour // hours) + 1) * hours
            delta_hours = next_hour - now.hour
            wait_time = (delta_hours * 3600) - (now.minute * 60) - now.second
            return max(1, int(wait_time)) + 2
            
    except Exception:
        pass
        
    return 60  # Default to 60 seconds if format unknown

def get_next_run_dir() -> str:
    os.makedirs("results", exist_ok=True)
    existing_runs = []
    for d in os.listdir("results"):
        if d.endswith("_run") and os.path.isdir(os.path.join("results", d)):
            try:
                num = int(d.split("_")[0])
                existing_runs.append(num)
            except ValueError:
                pass
    next_num = max(existing_runs) + 1 if existing_runs else 1
    run_dir = os.path.join("results", f"{next_num:02d}_run")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir

def run_backtest_mode():

    print("\n Running BACKTEST...\n")

    CONFIG["RUN_DIR"] = get_next_run_dir()
    use_multi = CONFIG["USE_MULTI"]
    
    if use_multi:
        tickers = CONFIG["MULTI_TICKER"]
    else:
        ticker_val = CONFIG["SINGLE_TICKER"]
        tickers = [ticker_val]

    for ticker in tickers:
        print(f"\n--- Backtesting for {ticker} ---\n")
        df = load_historical_data(
            ticker=ticker,
            interval=CONFIG["TIMEFRAME"],
            start=CONFIG["START_DATE"],
            end=CONFIG["END_DATE"]
        )

        if df is None or df.empty:
            print(f"❌ No data loaded for {ticker}. Check ticker/timeframe.")
            continue

        # Indicators
        df["ema_fast"] = calculate_ema(df, CONFIG["EMA_FAST"])
        df["ema_slow"] = calculate_ema(df, CONFIG["EMA_SLOW"])
        df["atr"] = calculate_atr(df, CONFIG["ATR_PERIOD"])

        ticker_config = CONFIG.copy()
        ticker_config["SINGLE_TICKER"] = ticker

        run_backtest(
            df,
            ticker_config,
            CONFIG["START_DATE"],
            CONFIG["END_DATE"]
        )


def run_forward_mode():

    print("\n Running FORWARD TEST (LIVE)...\n")
    
    use_multi = CONFIG["USE_MULTI"]
    
    if use_multi:
        tickers = CONFIG["MULTI_TICKER"]
        # Limit to maximum 3 tickers for now as requested
        # if len(tickers) > 3:
        #     print(f"Limiting to maximum of 3 tickers for forward test. Ignoring: {', '.join(tickers[3:])}")
        #     tickers = tickers[:3]
    else:
        # Single mode fallback
        ticker_val = CONFIG["SINGLE_TICKER"]
        tickers = [ticker_val]
        
    mode_str = "multi" if use_multi else "single"
    print(f"Starting Live Trading Engine for ({mode_str} mode): {', '.join(tickers)}...\n")
    
    while True:
        try:
            for ticker in tickers:
                if is_market_open(ticker):
                    print(f"[{time.strftime('%H:%M:%S')}] Market is OPEN for {ticker}. Running scanner...")
                    
                    # Create an override config specifically for this ticker iteration
                    ticker_config = CONFIG.copy()
                    ticker_config["SINGLE_TICKER"] = ticker
                    run_forward_test(ticker_config)
                    
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] Market is CLOSED for {ticker}. Waiting...")
            
            wait_seconds = get_seconds_to_next_candle(CONFIG["TIMEFRAME"])
            print(f"\n[{time.strftime('%H:%M:%S')}] Cycle complete. Sleeping for {wait_seconds} seconds (until next candle)...\n")
            # Sleep until the next candle boundary
            time.sleep(wait_seconds)
            
        except KeyboardInterrupt:
            print("\n⏹ Live Trading Engine stopped by user.")
            break
        except Exception as e:
            print(f"\n❌ Error in live loop: {e}")
            time.sleep(60)

def run_screener_mode(extra_tickers: list[str] | None = None, strict: bool | None = None,
                      csv_path: str | None = None, telegram_alerts: bool = True):

    print("\n Running 2/1 VWAP SCREENER...\n")
    from screener.two_one_screener import run_screener
    screener_config = CONFIG.copy()
    if not telegram_alerts:
        screener_config["SCREENER_TELEGRAM_ALERTS"] = False
    run_screener(screener_config, extra_tickers=extra_tickers, strict=strict, csv_path=csv_path)

def main():

    parser = argparse.ArgumentParser(description="Trading Engine")
    parser.add_argument("--mode", type=str, choices=["backtest", "forward", "screener", "price_alert"], help="Run mode (backtest, forward, screener, or price_alert)")
    parser.add_argument("--use_multi", type=str, choices=["true", "false", "True", "False"], help="Use multi ticker (true/false)")
    parser.add_argument("--tickers", nargs="*", default=None, help="Extra tickers for screener")
    parser.add_argument("--file", type=str, default=None, help="Screener ticker file (one per line or comma-separated)")
    parser.add_argument("--strict", action="store_true", default=None, help="Screener: price must be on correct VWAP side")
    parser.add_argument("--csv", type=str, default=None, help="Screener: CSV file path to save results")
    parser.add_argument("--no-telegram", action="store_true", help="Screener: suppress Telegram alerts")
    args = parser.parse_args()

    if args.mode:
        CONFIG["MODE"] = args.mode
    if args.use_multi is not None:
        CONFIG["USE_MULTI"] = args.use_multi.lower() == "true"

    mode = CONFIG["MODE"].lower()

    if mode == "backtest":
        run_backtest_mode()

    elif mode == "forward":
        run_forward_mode()

    elif mode == "screener":
        extra_tickers = list(args.tickers or [])
        if args.file:
            try:
                with open(args.file, encoding="utf-8-sig") as ticker_file:
                    contents = ticker_file.read().replace(",", "\n")
                extra_tickers.extend(
                    ticker.strip().upper()
                    for ticker in contents.splitlines()
                    if ticker.strip()
                )
            except OSError as exc:
                log.error(f"Could not read ticker file {args.file}: {exc}")
                return
        run_screener_mode(
            extra_tickers=extra_tickers or None,
            strict=args.strict,
            csv_path=args.csv,
            telegram_alerts=not args.no_telegram,
        )

    elif mode == "price_alert":
        from alerts.price_alert_monitor import run_price_alert_monitor
        run_price_alert_monitor()

    else:
        print(" Invalid MODE in config. Use backtest, forward, screener, or price_alert.")


if __name__ == "__main__":
    main()
