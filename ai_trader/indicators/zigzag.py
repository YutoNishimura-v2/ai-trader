"""ZigZag pivot detection (ATR-threshold filtered).

Unlike fractal pivots (where a high is a swing if it dominates a
``2k+1``-bar centred window), ZigZag returns *alternating*
HH/LL pivots filtered by an absolute price-move threshold:

- Start in an unknown state.
- Track the running max-high and min-low since the last confirmed
  pivot.
- A new "high" pivot is confirmed when price falls > threshold
  below the running max-high. The pivot is the running-max-high
  bar; the state flips to "looking for next low".
- Mirror for low pivots.

Because the threshold is in absolute price units (we use
``threshold_atr × ATR_at_running_extreme``), a small movement on
quiet bars never makes a pivot, and a big movement on volatile
bars makes pivots earlier. This produces a much cleaner sequence
of HH/HL/LH/LL than fractals at small lookback values.

Strictly causal: ``ZigZagSeries.confirmed_up_to(n)`` returns only
pivots whose confirmation bar is < n. The pivot's *iloc* is the
extreme bar, but its *confirmation bar* is later. This matches
how a live trader would see the pivot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .atr import atr


PivotKind = Literal["high", "low"]


@dataclass(frozen=True)
class ZigZagPivot:
    """A confirmed pivot.

    - ``iloc``: bar index of the actual extreme (the pivot)
    - ``confirm_iloc``: bar index when the threshold-reversal
      confirmed it (always > iloc); a live trader sees the pivot
      no earlier than this bar
    - ``price``: price at the pivot
    - ``kind``: 'high' or 'low'
    """
    iloc: int
    confirm_iloc: int
    price: float
    kind: PivotKind


def compute_zigzag(
    df: pd.DataFrame,
    *,
    threshold_atr: float = 1.0,
    atr_period: int = 14,
) -> list[ZigZagPivot]:
    """Compute all confirmed pivots over the full frame.

    The threshold at any moment = ``threshold_atr × ATR(running)``;
    using the ATR at the *current bar* (causal) means thresholds
    adapt to local volatility.
    """
    if threshold_atr <= 0:
        raise ValueError("threshold_atr must be > 0")
    n = len(df)
    if n < atr_period * 2:
        return []
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    a = atr(df, period=atr_period).to_numpy(dtype=float)

    pivots: list[ZigZagPivot] = []
    # State machine: direction = +1 looking for highs (running up),
    #                            -1 looking for lows (running down),
    #                             0 unknown (initial)
    direction = 0
    extreme_iloc = 0
    extreme_price = highs[0]

    for i in range(1, n):
        if not np.isfinite(a[i]):
            continue
        thr = threshold_atr * a[i]
        h = highs[i]; l = lows[i]
        if direction == 0:
            # Pick initial direction based on first significant move.
            if h - lows[0] > thr:
                direction = +1
                extreme_iloc = i
                extreme_price = h
            elif highs[0] - l > thr:
                direction = -1
                extreme_iloc = i
                extreme_price = l
            continue
        if direction == +1:
            # Tracking a high. Update running max if exceeded.
            if h > extreme_price:
                extreme_price = h
                extreme_iloc = i
            # Reversal: low has fallen > thr below running max.
            elif extreme_price - l > thr:
                pivots.append(ZigZagPivot(
                    iloc=extreme_iloc, confirm_iloc=i,
                    price=float(extreme_price), kind="high",
                ))
                direction = -1
                extreme_price = l
                extreme_iloc = i
        else:  # direction == -1
            if l < extreme_price:
                extreme_price = l
                extreme_iloc = i
            elif h - extreme_price > thr:
                pivots.append(ZigZagPivot(
                    iloc=extreme_iloc, confirm_iloc=i,
                    price=float(extreme_price), kind="low",
                ))
                direction = +1
                extreme_price = h
                extreme_iloc = i

    return pivots


class ZigZagSeries:
    """Precomputed ZigZag pivots over a frame.

    A strategy's ``prepare()`` builds one of these and ``on_bar``
    queries the recent pivots up to ``confirm_iloc < n``. By using
    confirm_iloc (not iloc) as the cutoff we get the same view a
    live trader would see at bar n-1.
    """

    def __init__(self, df: pd.DataFrame, *, threshold_atr: float = 1.0, atr_period: int = 14) -> None:
        self._index = df.index
        self._pivots = compute_zigzag(df, threshold_atr=threshold_atr, atr_period=atr_period)
        self._confirm_ilocs = np.array([p.confirm_iloc for p in self._pivots], dtype=np.int64)

    def confirmed_up_to(self, end_iloc_exclusive: int) -> list[ZigZagPivot]:
        cutoff = int(end_iloc_exclusive)
        stop = int(np.searchsorted(self._confirm_ilocs, cutoff, side="left"))
        return self._pivots[:stop]

    def tail(self, end_iloc_exclusive: int, max_count: int) -> list[ZigZagPivot]:
        cutoff = int(end_iloc_exclusive)
        stop = int(np.searchsorted(self._confirm_ilocs, cutoff, side="left"))
        start = max(0, stop - int(max_count))
        return self._pivots[start:stop]

    @property
    def all(self) -> list[ZigZagPivot]:
        return list(self._pivots)
