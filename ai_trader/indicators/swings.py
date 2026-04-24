"""Fractal swing-point detection.

A bar at index ``i`` is a swing high if its high is strictly greater
than every high in the window ``[i-k, i+k] \\ {i}``. Swing low is
analogous on lows. ``k`` is ``lookback // 2``.

This is intentionally simple: we are not trying to beat ZigZag. The
bot's trend classifier only needs a few recent pivots to decide if
higher highs / higher lows are forming.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


SwingKind = Literal["high", "low"]


@dataclass(frozen=True)
class SwingPoint:
    index: pd.Timestamp
    iloc: int
    price: float
    kind: SwingKind


def find_swings(df: pd.DataFrame, lookback: int = 20) -> list[SwingPoint]:
    """Return all confirmed swing points in chronological order.

    A swing at bar ``i`` is confirmed ``lookback // 2`` bars later.
    """
    if lookback < 2:
        raise ValueError("lookback must be >= 2")
    k = lookback // 2
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    out: list[SwingPoint] = []

    for i in range(k, n - k):
        window_high = highs[i - k : i + k + 1]
        window_low = lows[i - k : i + k + 1]
        if highs[i] == window_high.max() and (window_high == highs[i]).sum() == 1:
            out.append(SwingPoint(df.index[i], i, float(highs[i]), "high"))
        elif lows[i] == window_low.min() and (window_low == lows[i]).sum() == 1:
            out.append(SwingPoint(df.index[i], i, float(lows[i]), "low"))

    return out
