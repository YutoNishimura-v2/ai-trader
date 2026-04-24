"""Fractal swing-point detection.

A bar at index ``i`` is a swing high if its high is strictly greater
than every high in the window ``[i-k, i+k] \\ {i}``. Swing low is
analogous on lows. ``k`` is ``lookback // 2``.

Two entry points:

- ``find_swings(df, lookback)`` — convenience for tests and ad-hoc
  use. Vectorised over the full frame.
- ``SwingSeries(df, lookback)`` — precompute boolean masks over the
  entire series once; query confirmed swings up to any bar in O(M)
  where M is the number of swings found so far. The backtest engine
  uses this via ``BaseStrategy.prepare``; it is the fast path.

Both are fully causal: the mask at position i only depends on the
window centered on i, and the caller decides how far along the
series to read. "Confirmation" means the window on both sides is
known, so in a live setting a swing at bar i is only visible at
bar i + k.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


SwingKind = Literal["high", "low"]


@dataclass(frozen=True)
class SwingPoint:
    index: pd.Timestamp
    iloc: int
    price: float
    kind: SwingKind


def _compute_masks(
    highs: np.ndarray, lows: np.ndarray, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return (is_high, is_low) boolean arrays of length len(highs).

    Positions < k or > len - 1 - k cannot be confirmed; they stay
    False.
    """
    n = len(highs)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    if n < 2 * k + 1:
        return is_high, is_low

    win_h = sliding_window_view(highs, window_shape=2 * k + 1)
    win_l = sliding_window_view(lows, window_shape=2 * k + 1)
    center_h = win_h[:, k]
    center_l = win_l[:, k]

    ties_h = (win_h == center_h[:, None]).sum(axis=1)
    ties_l = (win_l == center_l[:, None]).sum(axis=1)

    hi_mask = (center_h == win_h.max(axis=1)) & (ties_h == 1)
    lo_mask = (center_l == win_l.min(axis=1)) & (ties_l == 1)
    lo_mask = lo_mask & ~hi_mask  # prefer high on degenerate ties

    is_high[k : n - k] = hi_mask
    is_low[k : n - k] = lo_mask
    return is_high, is_low


def find_swings(df: pd.DataFrame, lookback: int = 20) -> list[SwingPoint]:
    """Return all confirmed swing points in chronological order."""
    if lookback < 2:
        raise ValueError("lookback must be >= 2")
    k = lookback // 2
    n = len(df)
    if n < 2 * k + 1:
        return []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    is_high, is_low = _compute_masks(highs, lows, k)

    out: list[SwingPoint] = []
    idx = df.index
    for i in np.flatnonzero(is_high | is_low):
        i = int(i)
        if is_high[i]:
            out.append(SwingPoint(idx[i], i, float(highs[i]), "high"))
        else:
            out.append(SwingPoint(idx[i], i, float(lows[i]), "low"))
    return out


class SwingSeries:
    """Precomputed swing masks over a full OHLCV frame.

    Call ``confirmed_up_to(n)`` to get all swing points at ilocs
    ``< n`` (i.e., the set visible at the end of bar ``n - 1``).

    A strategy's ``prepare`` hook builds one of these; ``on_bar``
    queries the recent tail in O(M_tail) instead of rescanning the
    price history every call.
    """

    def __init__(self, df: pd.DataFrame, lookback: int = 20) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        self._lookback = lookback
        self._k = lookback // 2
        self._index = df.index
        self._highs = df["high"].to_numpy(dtype=float)
        self._lows = df["low"].to_numpy(dtype=float)
        self._is_high, self._is_low = _compute_masks(
            self._highs, self._lows, self._k
        )
        self._all_ilocs = np.flatnonzero(self._is_high | self._is_low)

    def confirmed_up_to(self, end_iloc_exclusive: int) -> list[SwingPoint]:
        """Return swing points with iloc < end_iloc_exclusive."""
        cutoff = int(end_iloc_exclusive)
        # np.searchsorted is O(log N)
        stop = int(np.searchsorted(self._all_ilocs, cutoff, side="left"))
        ilocs = self._all_ilocs[:stop]
        out: list[SwingPoint] = []
        idx = self._index
        for i in ilocs:
            i = int(i)
            if self._is_high[i]:
                out.append(SwingPoint(idx[i], i, float(self._highs[i]), "high"))
            else:
                out.append(SwingPoint(idx[i], i, float(self._lows[i]), "low"))
        return out

    def tail(self, end_iloc_exclusive: int, max_count: int) -> list[SwingPoint]:
        """Like ``confirmed_up_to`` but bounded to the last ``max_count`` swings."""
        cutoff = int(end_iloc_exclusive)
        stop = int(np.searchsorted(self._all_ilocs, cutoff, side="left"))
        start = max(0, stop - int(max_count))
        ilocs = self._all_ilocs[start:stop]
        out: list[SwingPoint] = []
        idx = self._index
        for i in ilocs:
            i = int(i)
            if self._is_high[i]:
                out.append(SwingPoint(idx[i], i, float(self._highs[i]), "high"))
            else:
                out.append(SwingPoint(idx[i], i, float(self._lows[i]), "low"))
        return out
