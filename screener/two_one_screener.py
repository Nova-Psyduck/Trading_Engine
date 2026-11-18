"""
screener/two_one_screener.py  —  2/1 VWAP Screener
==================================================
Scans one or more tickers and surfaces trade plans based on the
daily VWAP vs 2-day VWAP relationship.

Strategy Rules
--------------
  LONG  : daily VWAP > 2-day VWAP
  SHORT : daily VWAP < 2-day VWAP
  Stop  : day low so far (shorts: day high), never closer than 1 % from price
  Target: R2 from prior session high/low/close (shorts: S2)
  Exit  : stop hit  |  R2/S2 hit  |  end-of-day close  — no overnight holds

Integration
-----------
Reads tickers and settings from config.py (USE_MULTI / SINGLE_TICKER /
MULTI_TICKER / SCREENER_* keys). Can also be run standalone with extra
CLI flags that override config.

Run via main.py (recommended):
    uv run main.py --mode screener
    uv run main.py --mode screener --use_multi true

Or standalone:
    uv run two_one_screener.py
    uv run python screener/two_one_screener.py --tickers AAPL MSFT NVDA
    uv run two_one_screener.py --strict --csv results/screener.csv
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

from config import CONFIG
from alerts.telegram_bot import send_telegram_message
from indicators.vwap import calculate_vwap
from utils.logger import get_logger
from utils.market_schedule import is_market_open

log = get_logger("two_one_screener")

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_STOP_PCT = 0.01   # stop never closer than 1 % from price


# ── Data extraction helper ────────────────────────────────────────────────────

def _extract_ticker_df(df: pd.DataFrame, sym: str) -> pd.DataFrame | None:
    """
    Safely extract a single ticker's DataFrame from a yfinance download result.
    Handles MultiIndex columns whether ticker is level 0 or level 1,
    or single-level Index when single ticker is downloaded.
    Normalizes columns to Title Case: ['Open', 'High', 'Low', 'Close', 'Volume'].
    """
    if df.empty:
        return None

    try:
        if isinstance(df.columns, pd.MultiIndex):
            if sym in df.columns.get_level_values(0):
                sub = df.xs(sym, axis=1, level=0).copy()
            elif sym in df.columns.get_level_values(1):
                sub = df.xs(sym, axis=1, level=1).copy()
            else:
                return None
        else:
            sub = df.copy()

        # Rename to standardized Title Case
        col_map = {c: str(c).strip().capitalize() for c in sub.columns}
        sub.rename(columns=col_map, inplace=True)

        if "Close" not in sub.columns:
            return None

        sub.dropna(subset=["Close"], inplace=True)
        return sub if not sub.empty else None

    except Exception as exc:
        log.debug(f"[{sym}] Extraction failed: {exc}")
        return None


# ── Per-ticker scan ───────────────────────────────────────────────────────────

def _scan_ticker(sym: str, intraday: pd.DataFrame, daily: pd.DataFrame, strict: bool) -> dict | None:
    """
    Evaluate the 2/1 VWAP rules for a single ticker.

    Parameters
    ----------
    sym      : ticker symbol (upper-case)
    intraday : raw yfinance 5-minute download (may be MultiIndex for multi-ticker)
    daily    : raw yfinance 1-day download    (may be MultiIndex for multi-ticker)
    strict   : if True, also require price to be on the same side as the signal

    Returns a dict with the trade plan, or None if the rules are not met.
    """
    try:
        # Extract per-ticker DataFrames
        b = _extract_ticker_df(intraday, sym)
        d = _extract_ticker_df(daily, sym)

        if b is None or d is None or b.empty or d.empty:
            log.warning(f"[{sym}] Empty data after extraction — skipping")
            return None

        # ── Normalise index to timezone-naive New York time ───────────────────
        if b.index.tz is not None:
            b.index = b.index.tz_convert("America/New_York").tz_localize(None)
        if d.index.tz is not None:
            d.index = d.index.tz_convert("America/New_York").tz_localize(None)

        # ── Identify today and previous session ───────────────────────────────
        days = sorted(b.index.normalize().unique())
        if len(days) < 2:
            log.debug(f"[{sym}] Need at least 2 intraday days — only {len(days)} found")
            return None

        today = days[-1]
        prev  = days[-2]

        # Only use bars whose candle has fully closed (bar close time <= now)
        now_naive = pd.Timestamp.now(tz="America/New_York").tz_localize(None)
        today_bars = b[b.index.normalize() == today]
        done_bars  = today_bars[today_bars.index + pd.Timedelta(minutes=5) <= now_naive]

        # Fallback for closed market / off-hours testing
        if done_bars.empty:
            if not is_market_open(sym) and not today_bars.empty:
                done_bars = today_bars
            else:
                log.debug(f"[{sym}] No completed 5-min bars yet for today")
                return None

        price  = float(done_bars["Close"].iloc[-1])
        d_vwap = calculate_vwap(done_bars)

        prev_bars  = b[b.index.normalize() == prev]
        vwap_2day  = calculate_vwap(pd.concat([prev_bars, done_bars]))

        # ── Direction decision ────────────────────────────────────────────────
        if d_vwap > vwap_2day:
            side = "LONG"
        elif d_vwap < vwap_2day:
            side = "SHORT"
        else:
            return None   # dead-flat — no edge

        # ── Strict filter: price must also be on the correct side of daily VWAP ─
        price_vs_dvwap = "above" if price >= d_vwap else "below"
        if strict:
            if side == "LONG"  and price_vs_dvwap != "above":
                return None
            if side == "SHORT" and price_vs_dvwap != "below":
                return None

        # ── Stop: day extreme, minimum 1 % away from price ───────────────────
        if side == "LONG":
            stop = min(float(done_bars["Low"].min()), price * (1 - MIN_STOP_PCT))
        else:
            stop = max(float(done_bars["High"].max()), price * (1 + MIN_STOP_PCT))

        # ── Target: R2 (LONG) or S2 (SHORT) from prior session ───────────────
        prior_daily = d[d.index < today]
        if prior_daily.empty:
            log.debug(f"[{sym}] No prior daily data found")
            return None

        p     = prior_daily.iloc[-1]
        pivot = (float(p["High"]) + float(p["Low"]) + float(p["Close"])) / 3.0
        daily_range = float(p["High"]) - float(p["Low"])

        if side == "LONG":
            target = pivot + daily_range       # R2
        else:
            target = pivot - daily_range       # S2

        # ── Reward / risk validation ──────────────────────────────────────────
        risk   = abs(price - stop)
        reward = (target - price) if side == "LONG" else (price - target)

        if reward <= 0:
            log.debug(f"[{sym}] Price already past R2/S2 — no reward remaining")
            return None

        rr = round(reward / risk, 2) if risk > 0 else 0.0

        return {
            "ticker":         sym,
            "side":           side,
            "price":          round(price, 2),
            "daily_vwap":     round(d_vwap, 2),
            "vwap_2day":      round(vwap_2day, 2),
            "stop":           round(stop, 2),
            "stop_pct":       round(risk / price * 100, 2),
            "target_R2_S2":   round(target, 2),
            "reward_risk":    rr,
            "price_vs_dvwap": price_vs_dvwap,
        }

    except (KeyError, IndexError, ZeroDivisionError, TypeError) as exc:
        log.warning(f"[{sym}] Scan error ({type(exc).__name__}): {exc}")
        return None


# ── Telegram formatter ────────────────────────────────────────────────────────

def _format_telegram(results: list[dict]) -> str:
    """
    Format the screener results into a Telegram Markdown message.
    Groups by LONG / SHORT and lists all qualifying trade plans.
    """
    ts_now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    ts_str = ts_now.strftime("%Y-%m-%d %H:%M ET")

    lines = [f"📊 *2/1 VWAP Screener* — {ts_str}\n"]

    for side in ("LONG", "SHORT"):
        icon   = "🟢" if side == "LONG" else "🔴"
        subset = [r for r in results if r["side"] == side]
        lines.append(f"{icon} *{side} Candidates ({len(subset)})*")

        if not subset:
            lines.append("  _none_")
        else:
            for r in sorted(subset, key=lambda x: x["reward_risk"], reverse=True):
                lines.append(
                    f"  *{r['ticker']}*  price={r['price']}  "
                    f"stop={r['stop']} ({r['stop_pct']}%)  "
                    f"target={r['target_R2_S2']}  RR={r['reward_risk']}"
                )
        lines.append("")

    lines.append("Plan: enter near price · stop at 'stop' · target at 'target\\_R2\\_S2' · exit at close if neither hit.")
    return "\n".join(lines)


# ── Core screener runner (called by main.py OR standalone) ───────────────────

def run_screener(config: dict, extra_tickers: list[str] | None = None,
                 strict: bool | None = None, csv_path: str | None = None) -> list[dict]:
    """
    Run the 2/1 VWAP screener for all configured tickers.

    Parameters
    ----------
    config        : CONFIG dict from config.py
    extra_tickers : additional tickers passed via CLI (merged with config list)
    strict        : override SCREENER_STRICT from config if provided
    csv_path      : override SCREENER_CSV_PATH from config if provided

    Returns list of result dicts (one per passing ticker).
    """
    # ── Resolve ticker list ───────────────────────────────────────────────────
    if config.get("USE_MULTI"):
        tickers = list(config.get("MULTI_TICKER", []))
    else:
        tickers = [config.get("SINGLE_TICKER", "NVDA")]

    if extra_tickers:
        tickers = sorted(set(tickers + [t.upper() for t in extra_tickers]))

    if not tickers:
        log.error("No tickers configured. Set SINGLE_TICKER or MULTI_TICKER in config.py")
        return []

    # ── Resolve optional overrides ────────────────────────────────────────────
    use_strict  = strict   if strict   is not None else config.get("SCREENER_STRICT",   False)
    save_csv    = csv_path if csv_path is not None else config.get("SCREENER_CSV_PATH", "")

    log.info(f"2/1 VWAP Screener | tickers={tickers} | strict={use_strict}")

    # ── Market open check for first ticker (all are US equities here) ─────────
    sample = tickers[0]
    if not is_market_open(sample):
        log.warning("Market is currently CLOSED — running screener on last available session data.")

    # ── Download data once for all tickers (batch call) ───────────────────────
    log.info(f"Downloading 5-min intraday data for {len(tickers)} ticker(s)…")
    t0 = time.perf_counter()

    intraday = yf.download(
        tickers,
        period="5d",
        interval="5m",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        prepost=False,
    )
    daily = yf.download(
        tickers,
        period="1mo",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
    )

    fetch_ms = (time.perf_counter() - t0) * 1000
    log.info(f"Data downloaded in {fetch_ms:.0f} ms")

    if intraday.empty or daily.empty:
        log.error("yfinance returned empty data — aborting screener")
        return []

    # ── Scan each ticker ──────────────────────────────────────────────────────
    results: list[dict] = []
    for sym in tickers:
        result = _scan_ticker(sym, intraday, daily, strict=use_strict)
        if result:
            results.append(result)
            log.info(f"[{sym}] ✅ {result['side']}  price={result['price']}  "
                     f"stop={result['stop']}  target={result['target_R2_S2']}  "
                     f"RR={result['reward_risk']}")
        else:
            log.debug(f"[{sym}] No qualifying setup")

    # ── Print summary table ───────────────────────────────────────────────────
    _print_results(results, tickers)

    # ── Telegram alert ────────────────────────────────────────────────────────
    if results and config.get("SCREENER_TELEGRAM_ALERTS", True):
        try:
            msg = _format_telegram(results)
            send_telegram_message(msg)
            log.info("✅ Telegram alert sent")
        except Exception as tg_err:
            log.warning(f"Telegram send failed: {tg_err}")
    elif not results:
        log.info("No signals — skipping Telegram alert")

    # ── Optional CSV save ─────────────────────────────────────────────────────
    if results and save_csv:
        os.makedirs(os.path.dirname(os.path.abspath(save_csv)) or ".", exist_ok=True)
        df_out = pd.DataFrame(results).sort_values(["side", "reward_risk"], ascending=[True, False])
        df_out.to_csv(save_csv, index=False)
        log.info(f"Results saved to {save_csv}")

    return results


# ── Pretty-print helper ───────────────────────────────────────────────────────

def _print_results(results: list[dict], all_tickers: list[str]) -> None:
    """Print a formatted table to the terminal, grouped by side."""
    pd.set_option("display.width",       250)
    pd.set_option("display.max_columns",  30)

    ts_now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    print(f"\n{'='*65}")
    print(f"  📊  2/1 VWAP Screener  |  {ts_now:%Y-%m-%d %H:%M} ET")
    print(f"  Scanned {len(all_tickers)} ticker(s) — {len(results)} qualifying setup(s)")
    print(f"{'='*65}\n")

    if not results:
        print("  No ticker passes the 2/1 VWAP rules right now.\n")
        return

    df = pd.DataFrame(results).sort_values(["side", "reward_risk"], ascending=[True, False])
    for side in ("LONG", "SHORT"):
        icon  = "🟢" if side == "LONG" else "🔴"
        part  = df[df["side"] == side]
        print(f"{icon}  {side} candidates ({len(part)}):")
        if len(part):
            print(part.drop(columns="side").to_string(index=False))
        else:
            print("    none")
        print()

    print("Plan: enter near price  ·  stop at 'stop'  ·  target at 'target_R2_S2'  ·  exit at close if untouched.\n")


# ── Standalone CLI entry-point ────────────────────────────────────────────────

def main() -> None:
    """
    Standalone CLI entry-point.
    All options here are additive on top of what config.py already provides.
    """
    ap = argparse.ArgumentParser(
        description="2/1 VWAP Screener — standalone runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run two_one_screener.py                        # uses config.py tickers
  uv run two_one_screener.py --tickers AAPL MSFT    # override / add tickers
  uv run two_one_screener.py --file tickers.txt     # load tickers from file
  uv run two_one_screener.py --strict               # price must be on correct VWAP side
  uv run two_one_screener.py --csv results/out.csv  # also save to CSV
        """,
    )
    ap.add_argument("--tickers", nargs="*", default=[],
                    help="Extra tickers to scan (merged with config.py list)")
    ap.add_argument("--file",   default="",
                    help="Text/CSV file with tickers (one per line or comma-separated)")
    ap.add_argument("--csv",    default="",
                    help="Save results to this CSV file (overrides SCREENER_CSV_PATH in config)")
    ap.add_argument("--strict", action="store_true", default=None,
                    help="Also require price above daily VWAP (LONG) / below daily VWAP (SHORT)")
    ap.add_argument("--no-telegram", action="store_true",
                    help="Suppress Telegram alerts for this run")
    args = ap.parse_args()

    # Load extra tickers from --file if provided
    extra: list[str] = list(args.tickers)
    if args.file:
        try:
            text = open(args.file, encoding="utf-8-sig").read().replace(",", "\n")
            extra += [t.strip().upper() for t in text.splitlines() if t.strip()]
        except FileNotFoundError:
            log.error(f"Ticker file not found: {args.file}")

    local_cfg = CONFIG.copy()
    if args.no_telegram:
        local_cfg["SCREENER_TELEGRAM_ALERTS"] = False
    if args.csv:
        local_cfg["SCREENER_CSV_PATH"] = args.csv

    run_screener(local_cfg, extra_tickers=extra or None, strict=args.strict)


if __name__ == "__main__":
    main()
