# Trading Engine — Complete Codebase Walkthrough

> This document explains **every important file** in this codebase — what it does, how it works, what every function is responsible for, and how all the pieces connect. Written so anyone reading the code for the first time can understand it end-to-end.

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [How the System Works — Big Picture](#2-how-the-system-works--big-picture)
3. [config.py — The Control Panel](#3-configpy--the-control-panel)
4. [main.py — The Entry Point](#4-mainpy--the-entry-point)
5. [data/download_polygon_data.py — Historical Data Downloader](#5-datadownload_polygon_datapy--historical-data-downloader)
6. [data/historical_loader.py — CSV Loader](#6-datahistorical_loaderpy--csv-loader)
7. [data/live_data_loader.py — Live Data Fetcher](#7-datalive_data_loaderpy--live-data-fetcher)
8. [indicators/ema.py — EMA Indicator](#8-indicatorsemapy--ema-indicator)
9. [indicators/atr.py — ATR Indicator](#9-indicatorsatrpy--atr-indicator)
10. [strategies/ema_pullback.py — The Strategy Brain](#10-strategiesema_pullbackpy--the-strategy-brain)
11. [backtest/backtest_engine.py — Historical Simulation](#11-backtestbacktest_enginepy--historical-simulation)
12. [forward_test/forward_test_engine.py — Live Paper Trading](#12-forward_testforward_test_enginepy--live-paper-trading)
13. [alerts/telegram_bot.py — Telegram Alerts](#13-alertstelegram_botpy--telegram-alerts)
14. [upload/tradelogs_update.py — Google Sheets Upload](#14-uploadtradelogs_updatepy--google-sheets-upload)
15. [utils/market_schedule.py — Market Hours Check](#15-utilsmarket_schedulepy--market-hours-check)
16. [utils/logger.py — Logging System](#16-utilsloggerpy--logging-system)
17. [How Everything Connects — Full Data Flow](#17-how-everything-connects--full-data-flow)
18. [Key Design Decisions Explained](#18-key-design-decisions-explained)

---

## 1. What This Project Does

This is a **Python algorithmic trading research system**. It does three things:

1. **Backtesting** — Takes 1 year of historical stock price data and runs a strategy on it to simulate how much money it would have made or lost.
2. **Forward Testing (Paper Trading)** — Runs the same strategy on live market data every 5 minutes, fires Telegram alerts when a signal appears — without touching real money.
3. **Result Logging** — Saves every trade to CSV and auto-uploads results to Google Sheets for analysis.

The strategy it implements is called the **EMA Pullback Strategy** — it uses two EMA (Exponential Moving Average) lines (EMA 26 and EMA 55) to find moments when price pulls back to the EMA zone and then breaks out again.

> **What it does NOT do:** Execute real trades. It only finds signals and simulates them.

---

## 2. How the System Works — Big Picture

```
config.py         ← You set your preferences here (ticker, mode, EMA periods, etc.)
     │
     ▼
main.py           ← Reads config, starts the pipeline
     │
     ├─── BACKTEST MODE
     │         │
     │    historical_loader.py     ← Reads CSV file from disk
     │         │
     │    ema.py + atr.py          ← Adds EMA 26, EMA 55, ATR columns to the data
     │         │
     │    ema_pullback.py          ← Checks last 4 candles → signal or None
     │         │
     │    backtest_engine.py       ← Simulates entry/exit on every candle, tracks P&L
     │         │
     │    tradelogs_update.py      ← Saves trades.csv + uploads to Google Sheets
     │
     ├─── FORWARD MODE
     │         │
     │    market_schedule.py       ← Is the market open right now?
     │         │
     │    live_data_loader.py      ← Fetch latest candles from yfinance
     │         │
     │    ema.py                   ← Add EMA columns
     │         │
     │    ema_pullback.py          ← Check for signal
     │         │
     │    telegram_bot.py          ← Send alert to Telegram
     │
     └─── SCREENER MODE
               │
          two_one_screener.py      ← Scans tickers for daily VWAP vs 2-day VWAP
               │
          vwap.py                  ← Calculates intraday & multi-day VWAP
               │
          telegram_bot.py          ← Sends formatted trade plans with R2/S2 targets
```

---

## 3. `config.py` — The Control Panel

**Role:** Single dictionary `CONFIG` that controls the entire system. Change this file — everything adapts.

### Full Breakdown

```python
end_date   = datetime.now()
start_date = end_date - timedelta(days=30)
```
> At the top, the date range is calculated dynamically — always "last 30 days from today". So you never need to manually update dates.

---

```python
"USE_MULTI": False,
"SINGLE_TICKER": "NVDA",
"MULTI_TICKER": ["MSFT", "AAPL", "TSLA", "AMD", ...]
```
> **`USE_MULTI`** is the toggle between running on one stock vs. all the stocks in `MULTI_TICKER`. When `False`, only `SINGLE_TICKER` runs.

---

```python
"TIMEFRAME": "5m"
```
> The candle size. `"5m"` = 5-minute candles. Every piece of data fetching and candle-timing logic reads this.

---

```python
"EMA_FAST": 26,
"EMA_SLOW": 55,
"ATR_PERIOD": 14,
```
> The indicator periods. `EMA_FAST` reacts quickly to price changes. `EMA_SLOW` is smoother and represents the bigger trend.

---

```python
"INITIAL_BALANCE": 10000,
"RISK_PERCENT": 0.01,
```
> Starting capital for the backtest simulation. `RISK_PERCENT` is defined but not fully wired yet (future position sizing).

---

```python
"ENABLE_S1": True,
"ENABLE_S2": True,
"ENABLE_S3": True,
"ENABLE_S4": True,
"TREND_RR": 2.0,
"SCALP_RR": 1.2,
```
> Toggle each of the 4 sub-strategies independently. `TREND_RR` = risk-to-reward ratio for trend trades (risk $1 to make $2). `SCALP_RR` = risk $1 to make $1.20.

---

```python
"MODE": "backtest",   # or "forward"
"UPLOAD_TO_SHEETS": True,
```
> Default run mode. `UPLOAD_TO_SHEETS` controls whether results auto-upload to Google Sheets after every backtest.

---

```python
"STRATEGY_SHEET_ID": "...",
"TRADE_LOG_SHEET_ID": "...",
```
> Google Sheets IDs — the long string in the URL of the spreadsheet. Two separate sheets: one for per-trade logs, one for strategy summaries.

---

## 4. `main.py` — The Entry Point

**Role:** The program starts here. It reads config, optionally accepts CLI arguments, and routes to either backtest or forward mode.

### Functions

#### `main()`
```python
parser.add_argument("--mode", ...)
parser.add_argument("--use_multi", ...)
```
> Parses command-line arguments. So `uv run main.py --mode forward` overrides whatever `config.py` says. CLI wins over config.

---

#### `run_backtest_mode()`
```python
CONFIG["RUN_DIR"] = get_next_run_dir()
```
> First thing it does: creates a numbered results folder (`01_run`, `02_run`, etc.) so each run is preserved and never overwritten.

```python
for ticker in tickers:
    df = load_historical_data(...)
    df["ema_fast"] = calculate_ema(df, CONFIG["EMA_FAST"])
    df["ema_slow"] = calculate_ema(df, CONFIG["EMA_SLOW"])
    df["atr"]      = calculate_atr(df, CONFIG["ATR_PERIOD"])
    run_backtest(df, ticker_config, ...)
```
> For each ticker: load data → add indicators → run backtest. If `USE_MULTI` is True, loops over all tickers in the list.

---

#### `run_forward_mode()`
```python
while True:
    for ticker in tickers:
        if is_market_open(ticker):
            run_forward_test(ticker_config)
        else:
            print("Market is CLOSED. Waiting...")

    wait_seconds = get_seconds_to_next_candle(CONFIG["TIMEFRAME"])
    time.sleep(wait_seconds)
```
> An infinite loop. Each cycle: check if market is open → run the scanner → sleep until next candle boundary.

```python
except KeyboardInterrupt:
    break       # Ctrl+C = clean exit
except Exception as e:
    time.sleep(60)   # Any crash = retry after 60s
```
> Keeps running overnight without babysitting.

---

#### `run_screener_mode()`
```python
run_screener(CONFIG, extra_tickers=extra_tickers, strict=strict, csv_path=csv_path)
```
> Executes the 2/1 VWAP Screener pipeline across configured tickers (single or multi), surfaces qualifying Long/Short plans with stops and Floor Pivot (R2/S2) targets, logs results, and sends Telegram alerts.

---

#### `get_seconds_to_next_candle(timeframe)`
```python
next_minute = ((now.minute // minutes) + 1) * minutes
wait_time   = (delta_minutes * 60) - now.second
return max(1, int(wait_time)) + 2   # +2s buffer for API update lag
```
> Calculates exactly how many seconds until the next 5-minute candle closes and the API has updated data.  
> Example: if it's 10:37:30, next 5m candle is at 10:40, so it sleeps ~150 seconds + 2 buffer.

---

#### `get_next_run_dir()`
```python
existing_runs = [int(d.split("_")[0]) for d in os.listdir("results") if d.endswith("_run")]
next_num = max(existing_runs) + 1 if existing_runs else 1
run_dir = os.path.join("results", f"{next_num:02d}_run")
```
> Scans the `results/` folder, finds the highest run number, creates the next one: `01_run`, `02_run`, etc.

---

## 5. `data/download_polygon_data.py` — Historical Data Downloader

**Role:** One-time script. Downloads historical OHLCV data from Polygon.io and saves it to `data/historical_csvs/`.

### How it works

```python
api_key = os.getenv("POLYGON_API_KEY")
client  = RESTClient(api_key)
```
> Reads your Polygon.io API key from `.env` and creates the client.

---

```python
def parse_timeframe(tf_str):
    if tf_str.endswith('m'):
        return int(tf_str[:-1]), 'minute'   # "5m" → (5, "minute")
```
> Converts `"5m"` from config into what the Polygon API expects.

---

```python
for a in client.list_aggs(ticker, multiplier, timespan, start, end, limit=50000):
    aggs.append(a)
```
> Fetches all aggregated candles (OHLCV bars). `list_aggs` automatically handles pagination.

---

```python
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
df.index = df.index.tz_localize('UTC').tz_convert('US/Pacific')
df = df.between_time('06:30', '13:00')   # Regular Trading Hours only (9:30–16:00 ET)
```
> Polygon returns timestamps in milliseconds (Unix epoch, UTC). Convert to datetime, switch to Pacific, then filter to only keep regular trading hours.

---

```python
filename = f"{ticker}_{CONFIG['TIMEFRAME']}_{start}_to_{end}.csv"
```
> Saves with a consistent naming pattern. `historical_loader.py` reconstructs this exact filename to load it.

---

```python
if i < len(tickers) - 1:
    time.sleep(13)   # Polygon free tier: 5 calls/minute → sleep 13s between calls
```

---

## 6. `data/historical_loader.py` — CSV Loader

**Role:** Reads a local CSV file and returns a clean DataFrame ready for indicator calculation.

```python
def load_historical_data(ticker, interval, start=None, end=None):
    filename = f"{ticker}_{interval}_{start}_to_{end}.csv"
    filepath = os.path.join(current_dir, "historical_csvs", filename)
```
> Reconstructs the exact filename saved by the downloader. If not found, tells you to run `download_polygon_data.py` first.

---

```python
df['datetime'] = pd.to_datetime(df['datetime'])
df.set_index('datetime', inplace=True)
df.sort_index(inplace=True)
```
> Sets datetime as index (required for `.iloc[-1]` lookups in the strategy). Sorted oldest→newest so the strategy sees data in correct chronological order.

**Output DataFrame:**
```
datetime (index)        | open  | high  | low   | close | volume
2025-05-05 06:30:00 PDT | 100.0 | 102.0 | 99.0  | 101.0 | 50000
2025-05-05 06:35:00 PDT | 101.0 | 103.0 | 100.0 | 102.0 | 45000
...
```

---

## 7. `data/live_data_loader.py` — Live Data Fetcher

**Role:** Fetches the most recent candles from yfinance for the forward test.

### Key Logic

#### Dynamic Burn-In Calculation
```python
candles_per_day = max(1, 390 / minutes_per_candle)   # 390 min trading day
target_candles  = max(max_ema_length * 4, 100)        # 4x EMA period for stability
days_needed     = int((target_candles / candles_per_day) * 1.45)  # +45% buffer for weekends
```
> Instead of hardcoding "fetch 60 days", it **calculates** exactly how much history is needed.  
> **Why 4x the EMA period?** An EMA's value at candle N is influenced by all prior candles. To get a reliable EMA 55, you need ~220 prior candles. 4×55 = 220, plus 45% buffer for weekends and holidays.

---

```python
raw = yf.download(tickers=ticker, start=..., interval=interval, auto_adjust=True)
```
> `auto_adjust=True` avoids confusion between Close and Adjusted Close.

---

```python
if isinstance(df.columns, pd.MultiIndex):
    df.columns = [col[0] for col in df.columns]   # strip ticker level
df.columns = [str(c).strip().lower() for c in df.columns]
```
> yfinance 0.2.x returns columns as tuples like `("Close", "NVDA")`. This strips the ticker level and lowercases everything.

---

```python
df.dropna(subset=required, inplace=True)
for col in required:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df.dropna(subset=required, inplace=True)
```
> Double-pass NaN removal then force numeric type. Guards against object-dtype columns that can appear in certain yfinance versions.

---

## 8. `indicators/ema.py` — EMA Indicator

**Role:** Computes Exponential Moving Average on the close price.

```python
def calculate_ema(df, period, column="close"):
    return df[column].ewm(span=period, adjust=False).mean()
```

The whole file is 8 lines. But the detail matters:

- **`ewm(span=period)`** — Exponentially weighted mean. For EMA 26: `alpha = 2/(26+1) = 0.074`. Each new candle contributes 7.4% weight, prior candles decay exponentially.

- **`adjust=False`** — Uses the recursive formula: `EMA[t] = alpha × price[t] + (1 - alpha) × EMA[t-1]`  
  This matches how **TradingView** computes EMA. With `adjust=True`, pandas uses a different formula that doesn't match TradingView. This detail matters for strategy validation.

Called from `main.py`:
```python
df["ema_fast"] = calculate_ema(df, 26)   # → adds column "ema_fast"
df["ema_slow"] = calculate_ema(df, 55)   # → adds column "ema_slow"
```

---

## 9. `indicators/atr.py` — ATR Indicator

**Role:** Computes Average True Range — a volatility measure.

```python
tr = np.maximum(
    df["high"] - df["low"],                            # intraday range
    np.maximum(
        abs(df["high"] - df["close"].shift(1)),        # gap up scenario
        abs(df["low"]  - df["close"].shift(1))         # gap down scenario
    )
)
atr = tr.rolling(period).mean()
```

**True Range** = max of these 3 values, accounting for overnight gaps.  
**ATR** = 14-period rolling average of True Range.

> Currently calculated and added to the DataFrame but not yet used by the strategy. Marked for future use in dynamic stop-loss placement (e.g., SL = entry ± 1.5 × ATR).

---

## 10. `strategies/ema_pullback.py` — The Strategy Brain

**Role:** Looks at the last 4 candles, applies 4 rule-based setups, returns a signal dict or None.

> **Critical rule:** This file ONLY generates signals. It never touches positions, money, or trades.

### Helper Functions

#### `is_green(candle)` and `is_red(candle)`
```python
def is_green(candle) -> bool:
    return float(candle["close"]) > float(candle["open"])
```
> The `float()` cast converts pandas Series values to plain Python scalars — avoids the "truth value of a Series is ambiguous" error from yfinance multi-index columns.

---

#### `is_body_between_emas(candle)`
```python
upper = max(fast, slow)
lower = min(fast, slow)
return (max(o, c) < upper) and (min(o, c) > lower)
```
> Returns True if **both** open AND close are inside the EMA band. Direction-agnostic: works whether EMA 26 > EMA 55 or not.

---

### `generate_signal(df, config)` — The Core Function

```python
latest = df.iloc[-1]    # current candle
prev1  = df.iloc[-2]    # 1 candle ago
prev2  = df.iloc[-3]    # 2 candles ago
prev3  = df.iloc[-4]    # 3 candles ago

uptrend   = ema_fast > ema_slow   # EMA 26 above EMA 55
downtrend = ema_fast < ema_slow
```

---

### Strategy 3 — SHORT SCALP (checked FIRST)

**Setup:** Market is in uptrend, but 3 consecutive red (bearish) candles form near the EMA zone — buyers are exhausted.

```
Uptrend + 3 red candles + prev1.close > EMA Slow + prev3.open < EMA Fast
```

```python
entry = current_candle.open         # enter at open of current candle
sl    = prev3["open"]               # stop above first red candle's open
risk  = sl - entry
tp    = entry - 1.2 * risk          # 1.2× risk below entry
```

---

### Strategy 4 — LONG SCALP (checked SECOND)

**Setup:** Mirror of S3. Downtrend + 3 consecutive green candles → counter-trend long.

---

### Strategy 1 — LONG TREND (checked THIRD)

**Setup:** Market is in uptrend. The previous candle's body was entirely inside the EMA zone (pullback into the zone). The current candle closes above EMA Fast = breakout. Classic EMA pullback-and-continuation.

```
Uptrend + prev1 body between EMAs + current.close > EMA Fast
```

```python
entry = current_candle.close    # entry = close of current candle
sl    = ema_slow                # stop at EMA 55 (trend anchor)
risk  = entry - sl
tp    = entry + 2 * risk        # 2:1 risk-reward
```

> If price breaks below EMA 55, the uptrend assumption is invalidated → that's the stop loss level.

---

### Strategy 2 — SHORT TREND (checked FOURTH)

**Setup:** Mirror of S1. Downtrend + pullback into EMA zone + break below EMA Fast = short signal.

---

### Why S3/S4 are checked BEFORE S1/S2?

Scalp setups (S3/S4) can coincide with conditions that also satisfy trend setups (S1/S2). By checking scalps first, the system won't misclassify a scalp pattern as a trend trade.

---

### Signal Output Dict
```python
{
    "signal":      "LONG" | "SHORT",
    "type":        "trend" | "scalp",
    "strategy_id": 1 | 2 | 3 | 4,
    "entry":       float,       # price to enter at
    "stop_loss":   float,       # price to exit at a loss
    "take_profit": float,       # price to exit at a profit
    "reason":      str          # human-readable explanation
}
# Returns None if no setup found
```

---

## 11. `backtest/backtest_engine.py` — Historical Simulation

**Role:** Replays historical candle data and simulates what would happen if you followed the strategy's signals.

### `run_backtest(df, config, start_date, end_date)`

#### State variables
```python
position = 0       # 0 = flat, 1 = long, -1 = short
units    = 0       # number of shares held
entry_price, stop_loss, take_profit = None, None, None
```
> Only one position at a time. No pyramiding or scaling.

---

#### The main loop
```python
for i in range(5, len(df)):
    current_df = df.iloc[:i+1]   # only past + current candle — NO FUTURE DATA
    row = df.iloc[i]             # the current candle
```
> Starts at candle 5 (needs 4 prior candles for the strategy). Critically, passes only `df.iloc[:i+1]` to `generate_signal` — never future data. This prevents **lookahead bias**.

---

#### End-of-Day forced exit
```python
if row.name.date() != df.iloc[i+1].name.date():
    is_last_candle = True
```
> If the next candle is on a different day → force close any open trade at that day's closing price. No overnight positions.

---

#### Entry logic
```python
if position == 0:
    signal = generate_signal(current_df, config)
    if signal:
        position = 1 if signal["signal"] == "LONG" else -1
        units = max(1, int(balance / entry_price))   # all-in position sizing
```

---

#### Exit logic — LONG position
```python
elif position == 1:
    if low <= stop_loss or high >= take_profit:
        exit_price = stop_loss if low <= stop_loss else take_profit
        pnl = (exit_price - entry_price) * units
```
> Checks candle's `low` against SL and `high` against TP. Assumes worst-case fill (SL fills at exact SL price).

---

#### `_log_trade()` — 20 fields per trade
```
Trade Date | Ticker | Market | Timeframe | Side | Trade Type | Strategy No |
Indicators | Units | Entry Time | Entry Price | Stop Loss | Take Profit |
Exit Time | Exit Price | Duration (min) | Entry Reason | Exit Reason |
PnL ($) | Return % | Result
```
> Every trade stored with full context. This becomes `trades.csv`.

---

#### Output file structure
```
results/
  01_run/
    nvda_results(2025-10-01 - 2025-11-17)/
      strategy 1/
        trades.csv            ← every trade for S1
        trades_summary.txt    ← win rate, PnL, etc.
      strategy 2/
        trades.csv
        trades_summary.txt
```
> Split by strategy so you can compare S1 vs S2 vs S3 vs S4 independently.

---

#### `_generate_summary()` — Performance Report
Calculates and prints:
- Total Trades, Wins, Losses, Win Rate %
- Net Profit $, Final Balance $
- Avg Win $, Avg Loss $, Max Win $, Max Loss $

---

## 12. `forward_test/forward_test_engine.py` — Live Paper Trading

**Role:** Runs the strategy live. Called once per candle by `main.py`'s while loop.

> `run_forward_test()` runs **exactly once** per call. The infinite loop and sleep live in `main.py`. Keeps responsibilities separated.

### Step-by-step pipeline with timing

```python
# 1. Fetch data (timed)
t_fetch_start = time.perf_counter()
df = load_live_data(ticker, interval, max_ema_length=max_ema)
fetch_ms = (time.perf_counter() - t_fetch_start) * 1000

# 2. Compute indicators (timed)
t_compute_start = time.perf_counter()
df["ema_fast"] = calculate_ema(df, ema_fast)
df["ema_slow"] = calculate_ema(df, ema_slow)
df.dropna(subset=["ema_fast", "ema_slow"], inplace=True)

# 3. Generate signal
signal = generate_signal(df, config)
compute_ms = (time.perf_counter() - t_compute_start) * 1000

# 4. Fire alert if signal
if signal:
    _print_signal(signal, config)
    t_tg_start = time.perf_counter()
    send_telegram_message(_format_telegram(signal, config))
    tg_ms = (time.perf_counter() - t_tg_start) * 1000

# 5. Log latency
_log_latency_to_csv(ts, ticker, timeframe, fetch_ms, compute_ms, tg_ms)
```
> Every step is timed to millisecond precision. All timings are logged to `latency/latency_logs.csv` for long-term performance monitoring.

---

#### `_make_signal_key(signal)` — Deduplication
```python
return (signal["signal"], signal["type"], signal["strategy_id"], round(signal["entry"], 2))
```
> Hashable key from the signal. Same signal won't be re-alerted on consecutive candles. Prevents Telegram spam.

---

#### `_format_telegram()` — Timezone-aware messages
```python
if ticker.endswith(".NS") or ticker.endswith(".BO"):
    ts = now_utc.astimezone(ZoneInfo("Asia/Kolkata")).strftime("... IST")
else:
    ts = now_utc.astimezone(ZoneInfo("America/New_York")).strftime("... EST")
```
> Automatically formats in IST for Indian stocks and EST for US stocks.

---

## 13. `alerts/telegram_bot.py` — Telegram Alerts

**Role:** Sends a formatted message to a Telegram chat.

```python
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
response = requests.post(url, data=payload)
```
> Simple HTTP POST. Fails gracefully — a Telegram error never crashes the main loop.

**Setup steps (one-time):**
1. Message `@BotFather` on Telegram → `/newbot` → get `BOT_TOKEN`
2. Send any message to your bot → call `getUpdates` → get `CHAT_ID`
3. Add both to `.env`

---

## 14. `upload/tradelogs_update.py` — Google Sheets Upload

**Role:** Uploads backtest results to two Google Sheets — one with the full trade log, one with a summary row.

### Auth
```python
def _get_client():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")   # full JSON as one-line string
    service_account_info = json.loads(raw)
    creds = Credentials.from_service_account_info(service_account_info, scopes=_SCOPES)
    return gspread.authorize(creds)
```

---

### Retry decorator
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
```
> Both upload functions have this. Handles transient Google API rate limits (HTTP 429) automatically with exponential backoff: wait 2s, then 4s, then 8s before giving up.

---

### `upload_trade_log_tab()` — Full Trade Log
```python
try:
    worksheet = spreadsheet.worksheet(tab_name)
    worksheet.clear()          # overwrite if tab exists
except gspread.WorksheetNotFound:
    worksheet = spreadsheet.add_worksheet(title=tab_name, ...)

worksheet.update("A1", [header_row] + data_rows)
```
> Creates a new tab named after the result folder. If it already exists, clears and overwrites. Writes header + all trade rows from A1.

---

### `append_strategy_row()` — Summary Row
```python
all_values   = worksheet.get_all_values()
last_row_idx = len(all_values)
next_row     = last_row_idx + 1

col_end    = chr(ord("A") + len(new_row) - 1)   # calculates "M" for 13 columns
cell_range = f"A{next_row}:{col_end}{next_row}"
worksheet.update(cell_range, [new_row])
```
> Reads the entire strategy sheet, finds the last row, writes one new summary row immediately below. 13 columns A–M. Columns C, L, M are intentionally blank for manual entry.

---

## 15. `utils/market_schedule.py` — Market Hours Check

**Role:** Returns True if the market is currently open for a given ticker.

```python
def is_market_open(ticker: str) -> bool:
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    if ticker.endswith(".NS") or ticker.endswith(".BO"):
        # Indian Market: 9:30 AM - 3:30 PM IST, Mon-Fri
        now_ist = now_utc.astimezone(ZoneInfo("Asia/Kolkata"))
    else:
        # US Market: 9:30 AM - 4:00 PM ET, Mon-Fri
        now_est = now_utc.astimezone(ZoneInfo("America/New_York"))

    if now.weekday() > 4:   # Saturday = 5, Sunday = 6
        return False

    return market_open <= current_time <= market_close
```
> Always starts from UTC and converts — never uses local system time. DST-aware via Python's `zoneinfo` stdlib module.

---

## 16. `utils/logger.py` — Logging System

**Role:** Wraps `loguru` into a reusable factory that never creates duplicate log sinks.

### The Problem It Solves
When multiple modules all call `logger.add(sys.stdout)`, the same message gets printed multiple times. Solution:

```python
_base.remove()    # Remove loguru's default handler

# Stdout sink added ONCE at module load — shared by all callers
_base.add(sys.stdout, format=_LOG_FORMAT, level="DEBUG", enqueue=True)

# Track registered file sinks to never add same path twice
_registered_file_sinks: set[str] = set()
```

```python
def get_logger(name: str = "ct_ig", log_path: Path | None = None):
    # Optional rotating file sink — added at most once per unique path
    if log_path and str(Path(log_path).resolve()) not in _registered_file_sinks:
        _base.add(str(log_path), rotation="50 MB", retention="30 days", ...)
        _registered_file_sinks.add(key)

    return _base.bind(name=name)   # context-bound logger
```
> `bind(name=name)` tags every log line with the caller's module name:
> `2026-11-17 22:30:00 | INFO     | tradelogs_update | ✅ Written 42 rows to tab 'nvda_results...'`

---

## 17. How Everything Connects — Full Data Flow

### Backtest Mode

```
uv run main.py --mode backtest
        │
        ▼
main.py → parse args → run_backtest_mode()
        │
        ├── get_next_run_dir() → "results/01_run/"
        │
        └── for ticker in ["NVDA"]:
                │
                ▼
            historical_loader.py → reads NVDA_5m_2025-05-04_to_2026-05-04.csv
            → DataFrame (79,000+ rows)
                │
                ▼
            ema.py → df["ema_fast"] (EMA 26)
            ema.py → df["ema_slow"] (EMA 55)
            atr.py → df["atr"] (ATR 14)
                │
                ▼
            backtest_engine.py → run_backtest(df, config)
              ↳ loop i = 5 to 79000:
                  current_df = df[:i+1]              ← no future data!
                  signal = generate_signal(current_df)
                      ↳ ema_pullback.py checks S3→S4→S1→S2
                      ↳ returns dict or None
                  if signal → enter trade, record state
                  if position open → check SL/TP/EOD
                  → _log_trade() → trade_log[]
              ↳ save trades.csv + trades_summary.txt per strategy
                │
                ▼
            tradelogs_update.py:
              ↳ upload_trade_log_tab() → Google Sheets tab with all trades
              ↳ append_strategy_row() → 1 summary row to strategy sheet
```

---

### Forward Mode

```
uv run main.py --mode forward
        │
        ▼
main.py → run_forward_mode()
        │
        └── while True:
                for ticker in ["NVDA"]:
                    is_market_open("NVDA") → True? (9:30-16:00 ET)
                        │
                        ▼
                    forward_test_engine.py → run_forward_test(config)
                      1. load_live_data("NVDA", "5m") → yfinance → ~230 candles
                      2. calculate_ema → ema_fast, ema_slow columns
                         dropna(ema columns)
                      3. generate_signal(df) → ema_pullback.py → signal or None
                      4. if signal:
                           → print to console
                           → format Telegram message
                           → send_telegram_message() → HTTP POST to Telegram API
                      5. log fetch_ms, compute_ms, tg_ms to latency_logs.csv

                get_seconds_to_next_candle("5m") → e.g. 152 seconds
                time.sleep(152)   ← wake exactly at next 5m boundary
```

---

## 18. Key Design Decisions Explained

### 1. No Lookahead Bias in Backtest
```python
current_df = df.iloc[:i+1]   # only past + current candle
signal = generate_signal(current_df, config)
```
Every signal evaluation only sees data up to the current candle. The strategy cannot peek at future prices. This is the most critical correctness property of a valid backtest.

---

### 2. Strategy Layer is Stateless
`generate_signal()` takes a DataFrame and config, returns a dict or None. No internal state. This makes it:
- Identical code path for backtest and forward test
- Easy to test in isolation
- Simple to swap in a different strategy without touching the backtest engine

---

### 3. Full Capital Deployment
```python
units = max(1, int(balance / entry_price))
```
The backtest always puts all available capital into each trade. Maximizes P&L numbers for research purposes. In a real system, `RISK_PERCENT` would control position sizing instead.

---

### 4. EOD Forced Exits
```python
if row.name.date() != df.iloc[i+1].name.date():
    is_last_candle = True
```
No overnight positions. All open trades are closed at day's end. Models an intraday-only trading style and avoids overnight gap risk.

---

### 5. Candle-Boundary Sleep Alignment
```python
next_minute = ((now.minute // 5) + 1) * 5
wait_time   = (delta_minutes * 60) - now.second + 2
```
The forward test doesn't sleep for a fixed 300 seconds. It calculates the exact seconds until the next 5-minute boundary on the clock, so it always wakes exactly when new candle data is available.

---

### 6. Config Copy Per Ticker
```python
ticker_config = CONFIG.copy()
ticker_config["SINGLE_TICKER"] = ticker
```
In multi-ticker mode, a fresh copy of CONFIG is made for each ticker. Prevents one ticker's state from leaking into another's config.

---

### 7. Google Sheets Retry with Tenacity
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
```
Google Sheets API has rate limits. Tenacity handles HTTP 429/503 errors with exponential backoff automatically, without cluttering the upload logic with manual retry loops.

---

### 8. Dynamic EMA Burn-In Calculation
```python
target_candles = max(max_ema_length * 4, 100)
days_needed    = int((target_candles / candles_per_day) * 1.45)
```
Instead of hardcoding how many days of history to fetch, the live data loader calculates it dynamically based on the EMA period. If you change EMA 55 to EMA 200 in config, it automatically fetches more history to ensure the EMA is mathematically stable.

---

*This document covers every important file and function in the Trading Engine codebase.*
