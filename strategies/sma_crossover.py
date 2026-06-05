"""
SMA Crossover strategy
======================
Buy when short MA crosses above long MA (golden cross).
Sell when short MA crosses below long MA (death cross).
"""

import pandas as pd

KEY  = "sma_crossover"
NAME = "SMA Crossover"
DESCRIPTION = "Golden/death cross between two simple moving averages."

PARAMS = [
    {"key": "short_window", "label": "Short MA", "default": 20, "type": "int", "min": 2},
    {"key": "long_window",  "label": "Long MA",  "default": 50, "type": "int", "min": 3},
]

INDICATORS = [
    {"key": "sma_short", "label": "Short MA", "color": "#1d9e75", "dash": True},
    {"key": "sma_long",  "label": "Long MA",  "color": "#d85a30", "dash": True},
]


def signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    short = int(params["short_window"])
    long_ = int(params["long_window"])
    if short >= long_:
        raise ValueError("short_window must be less than long_window")

    df = df.copy()
    df["sma_short"] = df["Close"].rolling(short).mean()
    df["sma_long"]  = df["Close"].rolling(long_).mean()
    above = (df["sma_short"] > df["sma_long"]).astype(int)
    df["signal"] = above.diff()
    return df
