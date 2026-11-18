# 2/1 VWAP Screener — Documentation

**Directory:** `screener/`  
**File:** `screener/two_one_screener.py`  
**Root Entrypoint:** `two_one_screener.py`  
**Purpose:** Scans one or more stock tickers in real-time or pre-market, evaluating daily VWAP vs. 2-day rolling VWAP to formulate high-probability trade setups with Floor Pivot (R2/S2) targets and risk-to-reward ratios.

---

## Strategy Rules & Mechanics

The 2/1 VWAP screener compares the **Current Session Daily VWAP** against the combined **2-Day Rolling VWAP** (yesterday's session + today's closed 5-min bars):

| Parameter | LONG Rule | SHORT Rule |
|---|---|---|
| **Bias Condition** | Daily VWAP > 2-Day VWAP | Daily VWAP < 2-Day VWAP |
| **Strict Filter (Optional)** | Price >= Daily VWAP | Price <= Daily VWAP |
| **Stop Loss** | Today's lowest price so far (min 1% buffer) | Today's highest price so far (min 1% buffer) |
| **Target** | Classical Pivot R2 | Classical Pivot S2 |
| **Exit** | Stop hit, Target hit, or End-of-Day exit | Stop hit, Target hit, or End-of-Day exit |

### Target Formula (Classical Floor Trader Pivots)

Targets are calculated from the previous session's Daily Bar:
```
Pivot (P)   = (High_prev + Low_prev + Close_prev) / 3
Daily Range = High_prev - Low_prev

R2 (Long Target)  = Pivot + Daily Range
S2 (Short Target) = Pivot - Daily Range
```

---

## How to Run

### Via `main.py` (Recommended Engine Mode)

```bash
# Uses SINGLE_TICKER or MULTI_TICKER from config.py based on USE_MULTI
uv run main.py --mode screener

# Force multi-ticker screening
uv run main.py --mode screener --use_multi true

# Screen specific extra tickers
uv run main.py --mode screener --tickers AAPL MSFT NVDA TSLA

# Enable strict mode and save results to CSV
uv run main.py --mode screener --strict --csv results/screener.csv
```

### Standalone CLI

```bash
# Run standalone using config.py defaults
uv run two_one_screener.py

# Standalone with extra tickers and strict filter
uv run two_one_screener.py --tickers AMD NVDA QCOM --strict

# Load tickers from a text file (comma or newline separated)
uv run two_one_screener.py --file tickers.txt

# Suppress Telegram alert during testing
uv run two_one_screener.py --no-telegram
```

---

## Configuration (`config.py`)

Settings under `config.py` can be tuned directly:

```python
# MODES
"MODE": "backtest",  # "backtest", "forward", or "screener"

# 2/1 VWAP SCREENER CONFIG
"SCREENER_STRICT": False,           # if True, price must also be on the same side of daily VWAP
"SCREENER_TELEGRAM_ALERTS": True,   # set False to suppress Telegram alerts from screener
"SCREENER_CSV_PATH": "",            # optional path to save screener results (e.g. "results/screener.csv")
```
