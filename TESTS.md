# Trading Engine — Complete Test Documentation

> This document explains every test in the codebase in plain English.
> Anyone reading this — developer, trader, or reviewer — should be able to understand
> what is verified, why it matters, and what a failure means.

---

## How to Run All Tests

```bash
# From project root
cd /home/siduck/algotrading/Trading_Engine

# Run ALL tests (188 total)
.venv/bin/python -m unittest discover -s tests -v

# Run only pre-deployment readiness tests (71 tests)
.venv/bin/python -m unittest tests.test_predeployment -v

# Run only pipeline breakpoint tests (51 tests)
.venv/bin/python -m unittest tests.test_pipeline_breakpoints -v

# Run a single test by name
.venv/bin/python -m unittest tests.test_predeployment.TestGroupD_SignalGeneration.test_D01_s1_long_trend_entry_less_than_tp_greater_than_sl -v
```

> [!IMPORTANT]
> **All tests are 100% offline.** No network, no API keys, no live market needed.
> Every yfinance call is mocked. Tests run in under 1 second.

---

## Test Files Overview

| File | Purpose | Tests |
|---|---|---|
| [`test_predeployment.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_predeployment.py) | Pre-deployment live market readiness | 71 |
| [`test_pipeline_breakpoints.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_pipeline_breakpoints.py) | Failure mode / crash prevention | 51 |
| [`test_indicators.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_indicators.py) | EMA, ATR, VWAP math correctness | 15 |
| [`test_data_loaders.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_data_loaders.py) | Historical and live data loading | 7 |
| [`test_screener_functionality.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_screener_functionality.py) | 2/1 VWAP screener logic | 10 |
| [`test_recrossing.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_recrossing.py) | Price alert re-crossing reliability | 7 |
| [`test_monitor_delay.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_monitor_delay.py) | Price alert monitor timing | 8 |
| [`test_price_crossing_frequency.py`](file:///home/siduck/algotrading/Trading_Engine/tests/test_price_crossing_frequency.py) | Cooldown and crossing frequency | varies |

**Total: 188 tests | Expected result: 186 pass, 2 known pre-existing failures**

> [!NOTE]
> The 2 pre-existing failures are in `test_data_loaders.py` — they test for a specific historical CSV file that is not in the repo. These are stale assertions and do not affect live trading.

---

## 📋 test_predeployment.py — Pre-Deployment Readiness Tests

> Run these before every deployment. They verify the full pipeline works as expected
> in live market conditions, with 100% offline synthetic data.

---

### Group A — Config & Environment Validation

**What this tests:** Everything in `config.py`. If any of these fail, `main.py` will crash immediately on startup before touching the market.

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| A01 | `test_A01_all_required_config_keys_present` | All 20 required CONFIG keys exist | Missing key → `KeyError` at runtime |
| A02 | `test_A02_ema_fast_is_positive_integer` | `EMA_FAST` is a positive integer | Zero/negative crashes pandas EWM |
| A03 | `test_A03_ema_slow_is_positive_integer` | `EMA_SLOW` is a positive integer | Same as above |
| A04 | `test_A04_ema_fast_less_than_ema_slow` | `EMA_FAST < EMA_SLOW` | Fast must react faster — wrong order means inverted trend detection |
| A05 | `test_A05_single_ticker_is_non_empty_string` | Ticker is a non-empty string | Empty ticker string causes silent yfinance failure |
| A06 | `test_A06_timeframe_is_valid_format` | Timeframe ends with `'m'` or `'h'` and has a valid integer prefix | Bad timeframe → candle timer falls back to 60s, signals on wrong boundary |
| A07 | `test_A07_initial_balance_is_positive` | `INITIAL_BALANCE > 0` | Negative balance breaks backtest math |
| A08 | `test_A08_mode_is_valid` | MODE is one of `{backtest, forward, screener, price_alert}` | Invalid mode → "Invalid MODE" message and exit |
| A09 | `test_A09_at_least_one_strategy_enabled` | At least one of S1–S4 is enabled | All disabled → engine runs but never produces signals |
| A10 | `test_A10_multi_ticker_is_list` | `MULTI_TICKER` is a non-empty list | Type mismatch crashes `for ticker in tickers` loop |

---

### Group B — Live Data Contract

**What this tests:** The DataFrame returned by `load_live_data()` must meet strict quality standards. Any violation here means broken or nonsensical trade signals downstream.

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| B01 | `test_B01_schema_is_exactly_ohlcv` | Columns are exactly `[open, high, low, close, volume]` in that order | Wrong column names crash `generate_signal` with `KeyError` |
| B02 | `test_B02_all_values_are_numeric_and_finite` | No NaN or Inf in any OHLCV column | NaN propagates into EMA → generates garbage signals |
| B03 | `test_B03_high_always_gte_low` | `high >= low` on every row | Inverted high/low means corrupt candle data from yfinance |
| B04 | `test_B04_high_covers_open_and_close` | `high >= max(open, close)` on every row | High below close is physically impossible — data corruption |
| B05 | `test_B05_index_is_sorted_datetime_no_duplicates` | Index is sorted ascending DatetimeIndex with no duplicate timestamps | Unsorted index makes `iloc[-1]` point to wrong candle — wrong signal |
| B06 | `test_B06_enough_rows_for_ema_warmup` | More than 55 rows returned | Too few rows → EMA(55) has no context → all signals are noise |
| B07 | `test_B07_volume_is_positive` | Volume is non-negative on all rows | Negative volume is impossible — indicates data corruption |

---

### Group C — EMA Indicator Sanity

**What this tests:** `calculate_ema()` output is mathematically correct and properly aligned. These are the core indicators that determine trend direction.

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| C01 | `test_C01_ema_fast_no_nan_on_full_df` | EMA(26) has no NaN values on 80-row df | NaN in EMA → all boolean guards in `generate_signal` evaluate to False |
| C02 | `test_C02_ema_slow_no_nan_on_full_df` | EMA(55) has no NaN values | Same as above |
| C03 | `test_C03_ema_length_equals_df_length` | EMA output length == DataFrame length | Length mismatch → `iloc[-1]` reads wrong candle |
| C04 | `test_C04_ema_index_matches_df_index` | EMA index is identical to DataFrame index | Index mismatch → pandas alignment silently produces NaN |
| C05 | `test_C05_fast_ema_above_slow_in_uptrend` | EMA(26) > EMA(55) on last 10 rows in uptrend | Trend detection logic depends on this — wrong here means wrong signals |
| C06 | `test_C06_fast_ema_below_slow_in_downtrend` | EMA(26) < EMA(55) on last 10 rows in downtrend | Same as above but for shorts |
| C07 | `test_C07_flat_market_emas_converge` | Both EMAs converge to same value in flat market | Confirms no calculation drift over time |
| C08 | `test_C08_ema_does_not_mutate_original_df` | `calculate_ema()` doesn't add columns to input df | Side effects corrupt the df used by other strategies |

---

### Group D — Signal Generation (Every Strategy)

**What this tests:** Each of the 4 strategies fires correctly, produces valid trade plans, and never creates a bad trade (zero risk, wrong direction, missing fields).

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| D01 | `test_D01_s1_long_trend_entry_less_than_tp_greater_than_sl` | S1 LONG: `SL < Entry < TP` | Trade direction must be correct — reversed values mean losing trade |
| D02 | `test_D02_s2_short_trend_entry_greater_than_tp_less_than_sl` | S2 SHORT: `TP < Entry < SL` | Same for short — inverted SL/TP means instant stop-out |
| D03 | `test_D03_s3_short_scalp_fires_on_3_red_candles_in_uptrend` | S3 fires on 3 consecutive red candles in uptrend | Validates the exhaustion pattern logic |
| D04 | `test_D04_s4_long_scalp_fires_on_3_green_candles_in_downtrend` | S4 fires on 3 consecutive green candles in downtrend | Same for long scalp |
| D05 | `test_D05_no_signal_in_flat_market` | No crash in a flat/sideways market | Engine must be stable when no edge exists |
| D06 | `test_D06_signal_never_returned_when_risk_is_zero` | `risk > 0` enforced — SL never equals Entry | Zero-risk trades have infinite R:R on paper but break position sizing |
| D07 | `test_D07_signal_strategy_id_is_1_to_4` | `strategy_id` is always in `[1, 2, 3, 4]` | Invalid ID breaks Telegram alert formatting |
| D08 | `test_D08_signal_type_is_trend_or_scalp` | `type` is always `'trend'` or `'scalp'` | Invalid type breaks any downstream logic that branches on trade type |
| D09 | `test_D09_disabling_s1_prevents_long_trend_signal` | `ENABLE_S1=False` prevents S1 from firing | Config flags must actually work |
| D10 | `test_D10_signal_prices_are_finite_positive_floats` | All prices (entry/SL/TP) are finite and > 0 | NaN or zero prices are uncatchable in a live environment |

---

### Group E — Forward Test Engine — Real Market Loop Simulation

**What this tests:** `run_forward_test()` — the core loop function called every candle in live mode. Simulates everything that happens between "market opens" and "signal fires".

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| E01 | `test_E01_full_pipeline_completes_without_exception` | Full pipeline on 60-row df raises no exception | **Most critical test** — any crash here kills the live loop |
| E02 | `test_E02_output_contains_ticker_and_timeframe` | Console output includes ticker and timeframe | Confirms correct data was fetched for the right instrument |
| E03 | `test_E03_output_shows_row_count_and_latest_close` | "got N rows, latest close=X" is printed | Verifies data was received and last price is readable |
| E04 | `test_E04_no_signal_bar_prints_no_signal_message` | "No signal this bar" is printed when no edge | Confirms the engine is alive and scanning even with no signal |
| E05 | `test_E05_data_error_prints_warning_does_not_crash` | ValueError from data loader → "Data error" printed, no crash | yfinance failures must not kill the loop |
| E06 | `test_E06_column_error_prints_error_does_not_crash` | KeyError from data loader → "Column error" printed, no crash | Schema changes in yfinance must not kill the loop |
| E07 | `test_E07_telegram_failure_does_not_crash_engine` | Telegram network failure → engine continues | Internet down must not stop signal generation |
| E08 | `test_E08_latency_csv_is_written_after_each_run` | `_log_latency_to_csv()` called once per run | Performance tracking must work for monitoring |
| E09 | `test_E09_make_signal_key_is_consistent_and_hashable` | Same signal always produces same tuple key | Deduplication breaks if key changes between calls |
| E10 | `test_E10_different_signals_produce_different_keys` | LONG and SHORT signals have different keys | Prevents a LONG from deduplicating a SHORT on the same price |
| E11 | `test_E11_too_few_rows_aborts_gracefully` | 4-row df → "Not enough rows" printed, no crash | Market-open state has very few candles — must handle gracefully |

---

### Group F — Telegram Alert Contract

**What this tests:** Every Telegram message contains the right information. A missing field means a trader gets an incomplete trade plan in the market.

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| F01 | `test_F01_long_alert_contains_ticker` | Ticker symbol is in the message | Without ticker, trader doesn't know what stock to trade |
| F02 | `test_F02_long_alert_contains_entry_price` | Entry price appears in message | Trade entry point is the most critical piece |
| F03 | `test_F03_long_alert_contains_stop_loss` | Stop loss appears in message | Without SL, trader can't manage risk |
| F04 | `test_F04_long_alert_contains_take_profit` | Take profit appears in message | Without TP, trade has no target |
| F05 | `test_F05_long_alert_contains_long_direction` | LONG signal says "LONG", not "SHORT" | Wrong direction = reversed trade = guaranteed loss |
| F06 | `test_F06_short_alert_contains_short_direction` | SHORT signal says "SHORT", not "LONG" | Same but for shorts |
| F07 | `test_F07_alert_contains_strategy_id` | Strategy number (1–4) is in message | Trader needs to know which setup fired for journaling |
| F08 | `test_F08_alert_contains_timeframe` | Timeframe (e.g. "5m") is in message | Different timeframes have different urgency levels |
| F09 | `test_F09_alert_contains_reason` | The reason string from signal dict is in message | Context for why the signal fired |
| F10 | `test_F10_alert_is_non_empty_string` | Message is at least 50 characters long | An empty or one-word message is useless |
| F11 | `test_F11_telegram_missing_credentials_does_not_crash` | Missing `.env` credentials → prints warning, returns | Bad .env must not crash the app |

---

### Group G — Candle Timer Accuracy

**What this tests:** `get_seconds_to_next_candle()`. This function controls when the loop wakes up. If it returns wrong values, the engine scans mid-candle on incomplete data.

| Test ID | Test Name | What It Checks | Bounds |
|---|---|---|---|
| G01 | `test_G01_5m_timer_is_between_1_and_302_seconds` | 5m candle sleep | 1 – 302s |
| G02 | `test_G02_15m_timer_is_between_1_and_902_seconds` | 15m candle sleep | 1 – 902s |
| G03 | `test_G03_1m_timer_is_between_1_and_62_seconds` | 1m candle sleep | 1 – 62s |
| G04 | `test_G04_1h_timer_is_between_1_and_3602_seconds` | 1h candle sleep | 1 – 3602s |
| G05 | `test_G05_timer_always_returns_integer` | Return type is `int` | Floats can cause unexpected sleep behavior |
| G06 | `test_G06_unknown_timeframe_returns_60_fallback` | Unknown format → 60s | Never returns 0 (infinite loop) |
| G07 | `test_G07_empty_timeframe_returns_60_fallback` | Empty string → 60s | Same protection |
| G08 | `test_G08_daily_timeframe_returns_fallback` | `"1d"` → 60s | Daily timeframe not explicitly supported → safe fallback |

---

### Group H — Market Hours Guard

**What this tests:** `is_market_open()`. This gate controls whether the forward engine runs for a ticker at all. A wrong result silently skips signals or runs on closed-market data.

| Test ID | Test Name | What It Checks | Why It Matters |
|---|---|---|---|
| H01 | `test_H01_us_ticker_returns_bool` | `is_market_open("NVDA")` returns `True` or `False` | Must be a boolean, not a string or None |
| H02 | `test_H02_indian_ticker_returns_bool` | `.NS` tickers return bool | Indian market uses IST timezone |
| H03 | `test_H03_bse_ticker_returns_bool` | `.BO` tickers return bool | BSE is a different suffix |
| H04 | `test_H04_unknown_ticker_treated_as_us` | Unknown tickers default to US schedule | No crash on new/unusual tickers |
| H05 | `test_H05_weekend_returns_false_for_us` | Returns `False` on Saturday | Market is physically closed on weekends |
| H06 | `test_H06_market_closed_state_does_not_run_forward_test` | When market closed, `run_forward_test` is never called | Prevents scanning on stale pre/post-market data |

---

## 📋 test_pipeline_breakpoints.py — Crash Prevention Tests

> Tests every point in the pipeline where a bad input can silently break
> signal generation or crash the live loop.

### Quick Summary Table

| Stage | Class | What Can Break | Tests |
|---|---|---|---|
| Config | `TestStage0_Config` | Missing keys, bad EMA periods, all strategies off | 7 |
| Data Loader | `TestStage1_LiveDataLoader` | Empty df, missing columns, all-NaN, string values, MultiIndex | 6 |
| EMA Calc | `TestStage2_EMACalculation` | NaN price, wrong column, 1-row df, length mismatch | 7 |
| Warm-up Guard | `TestStage3_WarmUpGuard` | 0/3/4 row df → `generate_signal` must return None | 4 |
| Signal Logic | `TestStage4_GenerateSignal` | Missing columns, risk=0, doji, boundary EMAs, Series ambiguity | 11 |
| Engine | `TestStage5_ForwardTestEngineIntegration` | ValueError/KeyError/RuntimeError/Telegram failure | 6 |
| Timer | `TestStage6_CandleTimer` | Bad timeframe strings → 60s fallback | 6 |
| Schedule | `TestStage7_MarketSchedule` | Timezone crash, unknown ticker | 3 |

---

## 📋 test_indicators.py — Math Correctness

| Test | What It Verifies |
|---|---|
| `test_ema_calculation` | Manual EMA math with alpha=0.5 (period=3) |
| `test_atr_calculation` | ATR rolling window over known high/low values |
| `test_vwap_calculation` | VWAP = ΣTP×V / ΣV |
| `test_ema_constant_prices_and_index_alignment` | Constant prices → constant EMA; index preserved |
| `test_ema_short_series_and_custom_column` | 2-row series; custom column name |
| `test_ema_invalid_period_raises` | Period 0 or -1 raises `ValueError` |
| `test_ema_empty_input_returns_empty_series_and_missing_column_raises` | Empty df; bad column |
| `test_ema_missing_price_is_handled_without_losing_alignment` | NaN in close → no length change |
| `test_atr_gap_uses_previous_close` | Gap-up candle: TR = high - prev_close |
| `test_atr_warmup_short_series_and_index_alignment` | 1-row ATR(2) → all NaN |
| `test_atr_constant_range_and_invalid_period` | Constant 2-unit range; bad period raises |
| `test_atr_missing_values_preserve_index_and_warmup` | NaN in high → length preserved |
| `test_atr_missing_required_column_raises` | Missing 'close' → KeyError |
| `test_vwap_constant_price_and_zero_volume` | Zero volume fallback to last close |
| `test_vwap_empty_and_missing_columns` | Empty df → 0.0; missing 'high' → KeyError |
| `test_vwap_does_not_divide_by_zero_with_zero_volume_rows` | Zero-vol row skipped in VWAP |
| `test_vwap_normalizes_names_and_multiindex_columns` | Mixed case and MultiIndex handled |
| `test_indicators_do_not_mutate_input` | All indicators are pure functions |

---

## 📋 test_screener_functionality.py — 2/1 VWAP Screener

| Test | What It Verifies |
|---|---|
| `test_long_signal_generated` | Daily VWAP > 2-day VWAP → LONG signal |
| `test_short_signal_generated` | Daily VWAP < 2-day VWAP → SHORT signal |
| `test_empty_intraday_returns_none` | No data → None |
| `test_single_day_intraday_returns_none` | Only 1 day of bars → None (need ≥ 2) |
| `test_strict_mode_filters_wrong_side` | Strict=True: price must be on signal side of VWAP |
| `test_result_has_all_fields` | 10 required result dict keys all present |
| `test_stop_at_least_1pct_away` | Stop is at least 1% from price (risk management rule) |
| `test_scan_ticker_delay_consistency` | 30 runs: mean < 50ms, stddev < 20ms, max < 200ms |
| `test_delay_scales_linearly_with_tickers` | 5 tickers ≤ 10× time of 1 ticker |
| `test_format_with_mixed_results` | Telegram message has LONG and SHORT sections |
| `test_format_with_no_results` | Empty results message is valid |

---

## 📋 test_recrossing.py — Price Alert Re-crossing

| Test | What It Verifies |
|---|---|
| `test_two_crossings_within_30s` | 2 crossings, 30s apart → 2 alerts (no "one-shot" bug) |
| `test_five_rapid_crossings` | 5 crossings with no sleep → 5 alerts, correct directions |
| `test_ten_crossings_no_breakdown` | 10 crossings → monitor never breaks |
| `test_cooldown_suppresses_duplicate` | Cooldown=60s → 2nd crossing in <60s is suppressed |
| `test_cooldown_expires_and_refires` | After 60s, same price cross fires again |
| `test_initial_tick_does_not_alert` | First price read never fires an alert |
| `test_exactly_at_target_does_not_alert` | Price exactly at target → no alert, waits for clear move |

---

## Pre-Deployment Checklist

Before running `python main.py --mode forward` in a live market:

```
[ ] Run: .venv/bin/python -m unittest discover -s tests -v
[ ] Expected: 186 pass, 2 known failures (historical CSV tests)
[ ] Confirm .env has TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
[ ] Confirm config.py MODE = "forward"
[ ] Confirm config.py SINGLE_TICKER or USE_MULTI = True with valid MULTI_TICKER
[ ] Confirm config.py TIMEFRAME matches your intended candle size
[ ] Confirm at least one ENABLE_S1..S4 is True
[ ] Confirm EMA_FAST < EMA_SLOW (default: 26 < 55 ✓)
```

> [!TIP]
> Run the test suite on market days at ~9:25 AM ET to also warm up imports
> and catch any import-time errors before the market opens.

---

## Understanding a Test Failure

If a test fails, here is what to do:

| Failing Group | Likely Root Cause | Action |
|---|---|---|
| Group A (Config) | Someone edited `config.py` and removed a key | Add the key back |
| Group B (Live Data) | yfinance schema changed in a new version | Update `live_data_loader.py` |
| Group C (EMA) | `calculate_ema` signature or behavior changed | Check `indicators/ema.py` |
| Group D (Signal) | Strategy condition was edited incorrectly | Review `ema_pullback.py` |
| Group E (Engine) | Error handling was removed from `forward_test_engine.py` | Restore try/except blocks |
| Group F (Telegram) | `_format_telegram()` message template was changed | Check it still includes all fields |
| Group G (Timer) | `get_seconds_to_next_candle()` was edited | Check timeframe parsing logic |
| Group H (Market) | `is_market_open()` timezone logic changed | Check `market_schedule.py` |
