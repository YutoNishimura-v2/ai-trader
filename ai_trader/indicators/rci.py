"""Rank Correlation Index (RCI) — causal rolling Spearman-style oscillator.

Japanese technical analysis often uses RCI similarly to RSI: extreme
negative values suggest downside momentum exhaustion (reversal
readiness for longs), and vice versa for shorts.

We implement the classic rank-correlation form on the last ``period``
closes (1 bar = 1 period in the base timeframe, typically M1):

  RCI = (1 - 6 * Σ d_i² / (n * (n² - 1))) * 100

where ``d_i`` is the difference between each bar's *price rank* and
its *time rank* within the window (oldest time rank = 1, newest = n;
price rank 1 = lowest close in the window, n = highest). Ties use
average ranks (pandas ``rank`` semantics).

Output is in ``[-100, 100]`` (approximately); treat values as
comparable across instruments only in a loose sense.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rci(close: pd.Series, *, period: int = 14) -> pd.Series:
    if period < 3:
        raise ValueError("RCI period must be >= 3")
    n = len(close)
    out = np.full(n, np.nan, dtype=float)
    vals = close.to_numpy(dtype=float)
    for i in range(period - 1, n):
        seg = vals[i - period + 1 : i + 1]
        if not np.all(np.isfinite(seg)):
            continue
        s = pd.Series(seg)
        price_ranks = s.rank(method="average").to_numpy(dtype=float)
        time_ranks = np.arange(1.0, period + 1.0, dtype=float)
        d = price_ranks - time_ranks
        denom = float(period * (period**2 - 1))
        if denom <= 0:
            continue
        out[i] = (1.0 - 6.0 * float(np.dot(d, d)) / denom) * 100.0
    return pd.Series(out, index=close.index, dtype=float)
