"""Deterministic synthetic XAUUSD OHLCV.

The goal is NOT to produce realistic gold prices, only to give
tests and CI a reproducible, strategy-sensitive series.

Design:
  - Regime-switching geometric Brownian motion: every ``regime_bars``
    the drift flips sign, so the series contains both trending and
    pullback segments. This lets a trend-pullback strategy actually
    take trades in CI.
  - Intra-bar high/low are drawn from a half-normal scaled by
    volatility, so wicks exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np
import pandas as pd


_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}


def generate_synthetic_ohlcv(
    days: int = 30,
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4"] = "M5",
    start_price: float = 2000.0,
    annual_vol: float = 0.18,
    regime_bars: int = 240,
    regime_drift: float = 0.00015,
    seed: int = 7,
    start: datetime | None = None,
) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC timestamp with OHLCV columns."""
    if timeframe not in _TF_MINUTES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    minutes = _TF_MINUTES[timeframe]
    bars = int(days * 24 * 60 / minutes)
    rng = np.random.default_rng(seed)

    bars_per_year = 365 * 24 * 60 / minutes
    sigma = annual_vol / np.sqrt(bars_per_year)

    drifts = np.zeros(bars)
    sign = 1.0
    for i in range(0, bars, regime_bars):
        drifts[i : i + regime_bars] = sign * regime_drift
        sign *= -1.0

    shocks = rng.normal(loc=0.0, scale=sigma, size=bars)
    log_returns = drifts + shocks

    close = start_price * np.exp(np.cumsum(log_returns))
    open_ = np.empty_like(close)
    open_[0] = start_price
    open_[1:] = close[:-1]

    wick = np.abs(rng.normal(0.0, sigma, size=bars)) * close
    high = np.maximum(open_, close) + wick * 0.6
    low = np.minimum(open_, close) - wick * 0.6

    volume = rng.integers(50, 500, size=bars).astype(float)

    if start is None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.date_range(start=start, periods=bars, freq=f"{minutes}min", tz=timezone.utc)

    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )
    df.index.name = "time"
    return df
