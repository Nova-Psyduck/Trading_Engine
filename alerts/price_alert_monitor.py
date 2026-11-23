"""
Price Alert Monitor
===================
Monitors live prices for configured tickers and sends Discord alerts
when price crosses specified threshold levels.

Uses yfinance with cache-busting to get the freshest possible price on
every check.  Falls back through three methods (fast_info → 1m candle →
daily close) so there is always a price to compare.

Re-crossing detection
---------------------
The monitor tracks which SIDE of the target price the ticker was on during
the *previous* check.  Every time the side flips (ABOVE↔BELOW), a new
Discord alert fires — there is **no one-shot latch**.  A configurable
cooldown (default 0 = disabled) can throttle rapid-fire alerts caused by
price hovering right at the target.

Usage (standalone):
    python -m alerts.price_alert_monitor

Usage (via main.py):
    python main.py --mode price_alert
"""

import time
import traceback
from datetime import datetime

import requests
import yfinance as yf

from alerts.discord_webhook import send_price_cross_alert, send_discord_embed

# ── Price Alert Configuration ────────────────────────────────────────────────
# Each alert is a dict:
#   ticker       : str   — the yfinance ticker symbol
#   target_price : float — the price level to watch
#   cooldown     : int   — seconds to wait before re-alerting same cross

PRICE_ALERTS = [
    {
        "ticker": "NVDL",
        "target_price": 15.46,
        "cooldown": 0,  # No cooldown — alert on every crossing (up & down)
    },
]

# How often to check prices (seconds) — keep low for near-instant alerts
CHECK_INTERVAL = 5

# ── Internal state ────────────────────────────────────────────────────────────
# Tracks whether price was above or below target on the previous check
# key: (ticker, target_price) -> "ABOVE" | "BELOW" | None
_prev_side: dict[tuple, str | None] = {}

# Tracks last alert time to enforce cooldown
# key: (ticker, target_price) -> timestamp
_last_alert_time: dict[tuple, float] = {}

# Count of consecutive price-fetch failures (for logging)
_fetch_failures: dict[str, int] = {}


# ── Price fetching (cache-bust on every call) ────────────────────────────────

def _get_realtime_price(ticker: str) -> float | None:
    """
    Fetch the most current price available for *ticker*.

    To defeat yfinance's internal session / object cache, we create a
    **fresh Session and Ticker** on every call and pass ``period="1d"``
    with ``interval="1m"`` so the response is never served from a stale
    browser-style HTTP cache.

    Strategy (fastest → slowest):
      1. ``Ticker.fast_info['lastPrice']`` — near real-time, very light
      2. Last close of a fresh 1-minute candle download
      3. Last close of a daily candle (pre/post market or as final fallback)
    """
    try:
        # ── Fresh session to avoid yfinance's internal caching ──────────
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        })
        t = yf.Ticker(ticker, session=session)

        # Method 1: fast_info (near real-time, very lightweight)
        try:
            fi = t.fast_info
            price = fi.get("lastPrice") or fi.get("last_price")
            if price and float(price) > 0:
                _fetch_failures[ticker] = 0
                return float(price)
        except Exception:
            pass

        # Method 2: 1-minute candle (updates every ~60s during market)
        try:
            hist = t.history(period="1d", interval="1m")
            if hist is not None and not hist.empty:
                _fetch_failures[ticker] = 0
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass

        # Method 3: daily fallback
        try:
            hist = t.history(period="1d")
            if hist is not None and not hist.empty:
                _fetch_failures[ticker] = 0
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass

    except Exception as e:
        cnt = _fetch_failures.get(ticker, 0) + 1
        _fetch_failures[ticker] = cnt
        # Only log every 5th consecutive failure to avoid console spam
        if cnt <= 3 or cnt % 5 == 0:
            print(f"  ⚠️  Error fetching {ticker} (#{cnt}): {e}")

    return None


# ── Core crossing logic ──────────────────────────────────────────────────────

def _check_and_alert(alert_config: dict) -> None:
    """
    Compare the live price to the target and fire a Discord alert every
    time the side flips (ABOVE↔BELOW).

    Key guarantee: after a successful alert the ``_prev_side`` is kept at
    the *new* side so that the **next** flip in the opposite direction will
    trigger another alert.  There is no flag that permanently silences
    future crossings.
    """
    ticker = alert_config["ticker"]
    target = alert_config["target_price"]
    cooldown = alert_config.get("cooldown", 0)
    key = (ticker, target)

    price = _get_realtime_price(ticker)
    if price is None:
        return

    # Determine current side
    if price > target:
        current_side = "ABOVE"
    elif price < target:
        current_side = "BELOW"
    else:
        # Exactly at target — keep previous side so the next tick that
        # moves away still counts as a cross.
        print(f"  📊 {ticker}: ${price:.4f} (exactly AT ${target:.2f}) — waiting for clear move")
        return

    prev = _prev_side.get(key)

    # First run: just record the side, don't alert
    if prev is None:
        _prev_side[key] = current_side
        print(f"  📍 {ticker} initial price: ${price:.4f} ({current_side} target ${target:.2f})")
        return

    # ── Detect crossing ──────────────────────────────────────────────────
    crossed = False
    direction = ""

    if prev == "BELOW" and current_side == "ABOVE":
        crossed = True
        direction = "ABOVE"
    elif prev == "ABOVE" and current_side == "BELOW":
        crossed = True
        direction = "BELOW"

    # Always update stored side — this is what allows re-crossing to work.
    _prev_side[key] = current_side

    if not crossed:
        print(f"  📊 {ticker}: ${price:.4f} (still {current_side} ${target:.2f}) — no cross")
        return

    # ── Cooldown check ───────────────────────────────────────────────────
    now = time.time()
    last = _last_alert_time.get(key, 0)
    if cooldown > 0 and (now - last) < cooldown:
        remaining = int(cooldown - (now - last))
        print(f"  ⏳ {ticker} crossed {direction} ${target:.2f} but cooldown active ({remaining}s left)")
        return

    # 🚨 CROSSING DETECTED — send alert
    print(f"\n  🚨 {ticker} CROSSED {direction} ${target:.2f}! Current: ${price:.4f}")
    success = send_price_cross_alert(
        ticker=ticker,
        current_price=price,
        target_price=target,
        direction=direction,
        timeframe="live",
    )

    if success:
        print(f"  ✅ Discord alert sent for {ticker} crossing {direction} ${target:.2f}")
        _last_alert_time[key] = now
    else:
        print(f"  ❌ Failed to send Discord alert for {ticker}")


# ── Main monitor loop ────────────────────────────────────────────────────────

def run_price_alert_monitor(alerts: list[dict] | None = None, check_interval: int | None = None) -> None:
    """
    Main loop: continuously monitors prices and sends Discord alerts on crossings.
    """
    alerts = alerts or PRICE_ALERTS
    interval = check_interval or CHECK_INTERVAL

    # Print startup banner
    print("\n" + "=" * 60)
    print("  📡 PRICE ALERT MONITOR — STARTED (REAL-TIME)")
    print("=" * 60)
    print(f"  Check interval : {interval}s")
    print(f"  Monitoring {len(alerts)} alert(s):\n")
    for a in alerts:
        cd = a.get("cooldown", 0)
        cd_label = f"{cd}s" if cd > 0 else "disabled (alert every cross)"
        print(f"    • {a['ticker']} @ ${a['target_price']:.2f}  [cooldown: {cd_label}]")
    print("\n" + "=" * 60 + "\n")

    # Send startup notification to Discord
    alert_lines = "\n".join(
        f"• **{a['ticker']}** @ **${a['target_price']:.2f}**"
        for a in alerts
    )
    send_discord_embed(
        title="📡 Price Alert Monitor — ONLINE (Real-Time)",
        description=(
            f"Monitoring the following price levels every **{interval}s**:\n\n"
            f"{alert_lines}\n\n"
            f"_Re-crossing alerts enabled — every up/down cross triggers a new alert._"
        ),
        color=0x3498DB,
        footer="Trading Engine • Price Alert Monitor",
    )

    while True:
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 🔍 Checking prices...")

            for alert_config in alerts:
                _check_and_alert(alert_config)

            print(f"[{ts}] ✅ Done. Next in {interval}s\n")
            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n⏹ Price Alert Monitor stopped by user.")
            send_discord_embed(
                title="📡 Price Alert Monitor — OFFLINE",
                description="Monitor has been stopped.",
                color=0x95A5A6,
                footer="Trading Engine • Price Alert Monitor",
            )
            break

        except Exception as e:
            print(f"\n❌ Error in monitor loop: {e}")
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    run_price_alert_monitor()
