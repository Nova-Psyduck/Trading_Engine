import pandas as pd


def calculate_vwap(bars: pd.DataFrame) -> float:
    """
    Calculate the Volume Weighted Average Price (VWAP) for a given DataFrame of bars.

    Parameters
    ----------
    bars : pd.DataFrame
        DataFrame with columns for High, Low, Close, and Volume.
        Supports single-level columns or MultiIndex columns (e.g. from yfinance).
        Column names can be uppercase, lowercase, or capitalized.

    Returns
    -------
    float
        The calculated VWAP as a float, or the last close price if volume is 0.
    """
    if bars.empty:
        return 0.0

    df = bars.copy()

    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    # Normalize column names to lowercase
    col_map = {c: str(c).strip().lower() for c in df.columns}
    df.rename(columns=col_map, inplace=True)

    required = ["high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise KeyError(
                f"[calculate_vwap] Missing required column '{col}'. Available: {list(df.columns)}"
            )

    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    total_vol = df["volume"].sum()
    if total_vol == 0:
        return float(df["close"].iloc[-1])

    return float((tp * df["volume"]).sum() / total_vol)
