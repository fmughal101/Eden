"""
Indicator library
=================
Pure indicator functions + a registry that the composite strategy uses to
dispatch user-chosen indicators by `type` string. No I/O. All functions
return pandas Series (or a tuple of Series for multi-output indicators).
"""

import pandas as pd


# ─── Indicator implementations ────────────────────────────────────────────────

def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(int(period)).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=int(period), adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / int(period), adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / int(period), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=int(signal), adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(close: pd.Series, period: int = 20,
              stddev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(int(period)).mean()
    sd = close.rolling(int(period)).std()
    upper = mid + float(stddev) * sd
    lower = mid - float(stddev) * sd
    return upper, mid, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(),
         (high - prev_close).abs(),
         (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / int(period), adjust=False).mean()


# ─── Registry ─────────────────────────────────────────────────────────────────
# `fn`        — callable(close|df, **args) -> Series | tuple[Series, ...]
# `input`     — "close" passes the Close series; "df" passes the full df (for ATR).
# `args`      — ordered arg names; same order is used to build the auto alias.
# `outputs`   — column suffixes appended to the alias for each output. Single-output
#               indicators use [""], multi-output emit one column per suffix.
# `colors`    — chart color per output (must match `outputs` length).
# `dash`      — true = dashed line on chart (typical for MAs / bands).
# `price_scale` — true if the indicator shares the price y-axis (chart will plot it).
#                 RSI/MACD are computed for signaling but skipped on the price chart.

INDICATOR_REGISTRY: dict = {
    "sma": {
        "fn": sma, "input": "close", "args": ["period"],
        "outputs": [""], "colors": ["#1d9e75"], "dash": True,
        "price_scale": True, "label": "SMA",
    },
    "ema": {
        "fn": ema, "input": "close", "args": ["period"],
        "outputs": [""], "colors": ["#5fd17a"], "dash": True,
        "price_scale": True, "label": "EMA",
    },
    "rsi": {
        "fn": rsi, "input": "close", "args": ["period"],
        "outputs": [""], "colors": ["#d4a13a"], "dash": False,
        "price_scale": False, "label": "RSI",
    },
    "macd": {
        "fn": macd, "input": "close", "args": ["fast", "slow", "signal"],
        "outputs": ["", "_signal", "_hist"],
        "colors": ["#5a8fc4", "#d4a13a", "#8a8880"],
        "dash": False, "price_scale": False, "label": "MACD",
    },
    "bollinger": {
        "fn": bollinger, "input": "close", "args": ["period", "stddev"],
        "outputs": ["_upper", "_mid", "_lower"],
        "colors": ["#5a8fc4", "#8a8880", "#5a8fc4"],
        "dash": True, "price_scale": True, "label": "Bollinger",
    },
    "atr": {
        "fn": atr, "input": "df", "args": ["period"],
        "outputs": [""], "colors": ["#8a8880"], "dash": False,
        "price_scale": False, "label": "ATR",
    },
}


def auto_alias(ind_type: str, args: dict) -> str:
    """Default id when the frontend doesn't provide one (or for sanity):
    e.g. {"type":"sma","args":{"period":50}} -> "sma_50".
    """
    spec = INDICATOR_REGISTRY[ind_type]
    parts = [ind_type] + [str(args[a]) for a in spec["args"] if a in args]
    return "_".join(parts).replace(".", "p")


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import numpy as np
    rng = np.random.default_rng(42)
    closes = pd.Series(100 + rng.standard_normal(60).cumsum())
    df = pd.DataFrame({
        "Close": closes,
        "High": closes + 1,
        "Low": closes - 1,
    })
    print(f"SMA(10) tail:\n{sma(closes, 10).tail()}\n")
    print(f"EMA(10) tail:\n{ema(closes, 10).tail()}\n")
    print(f"RSI(14) tail:\n{rsi(closes, 14).tail()}\n")
    m, s, h = macd(closes)
    print(f"MACD tail:  m={m.iloc[-1]:.3f}  s={s.iloc[-1]:.3f}  h={h.iloc[-1]:.3f}")
    u, mid, lo = bollinger(closes, 20, 2.0)
    print(f"BB tail:   upper={u.iloc[-1]:.3f}  mid={mid.iloc[-1]:.3f}  lower={lo.iloc[-1]:.3f}")
    print(f"ATR(14) tail:  {atr(df, 14).iloc[-1]:.3f}")
