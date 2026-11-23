import numpy as np


def calculate_atr(df, period=14):
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError("period must be a positive integer")

    missing = {"high", "low", "close"}.difference(df.columns)
    if missing:
        raise KeyError(f"Missing required ATR columns: {sorted(missing)}")

    high_low = df["high"] - df["low"]
    tr = np.maximum(
        high_low,
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    # The first candle has no previous close, so its true range is high - low.
    tr = tr.where(tr.notna(), high_low)

    atr = tr.rolling(period).mean()

    return atr  

