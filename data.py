"""
Market data fetcher
===================
Wraps yfinance and normalizes the dataframe shape (flat single-symbol columns).
"""

import logging

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def get_historical_data(symbol: str, period: str = "6mo") -> pd.DataFrame:
    log.info(f"Fetching {symbol} ({period})")
    df = yf.download(symbol, period=period, auto_adjust=True, progress=False)

    if df.empty:
        raise ValueError(f"No data for {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df
