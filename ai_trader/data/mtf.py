"""Multi-timeframe context for M1 strategies.

A common need: trade on M1 but bias by M5/M15/H1 structure. Done
naively this is a lookahead minefield — at M1 bar with timestamp
``t``, the M5 bar that *contains* ``t`` is still in-progress; a
strategy must only consult M5 bars that closed *before* ``t``.

``MTFContext`` precomputes resampled OHLC frames for the
requested higher timeframes, indexes them by their close time
(NOT the bar's start time), and exposes:

  ctx.last_closed("M5", t) -> the most recent M5 bar whose close
                              time <= t (i.e., the bar is fully
                              formed as of t).

This is the safe primitive for MTF features.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd


_TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _resample_to_tf(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf not in _TF_MIN:
        raise ValueError(f"unsupported timeframe {tf!r}")
    minutes = _TF_MIN[tf]
    if minutes == 1:
        return m1.copy()
    rule = f"{minutes}min"
    out = m1.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum",
    }).dropna()
    # Add an explicit close_time = bar_start + tf duration. A bar
    # is "closed" only at close_time and not visible before.
    out["close_time"] = out.index + pd.Timedelta(minutes=minutes)
    return out


@dataclass
class MTFContext:
    """Multi-timeframe view of a base M1 frame.

    Build once in prepare(); query per-bar via last_closed().
    """
    base: pd.DataFrame
    timeframes: list[str] = field(default_factory=list)
    _frames: dict[str, pd.DataFrame] = field(default_factory=dict, init=False, repr=False)
    _close_indices: dict[str, np.ndarray] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._frames = {}
        self._close_indices = {}
        for tf in self.timeframes:
            df = _resample_to_tf(self.base, tf)
            self._frames[tf] = df
            # Numpy nanosecond array of close times for fast
            # searchsorted. Strip the timezone first (tz-aware
            # datetime64 can't be converted directly).
            ct = df["close_time"]
            if getattr(ct.dt, "tz", None) is not None:
                ct = ct.dt.tz_convert("UTC").dt.tz_localize(None)
            self._close_indices[tf] = ct.astype("datetime64[ns]").to_numpy()

    def has(self, tf: str) -> bool:
        return tf in self._frames

    def last_closed_idx(self, tf: str, t: datetime) -> Optional[int]:
        """Return the iloc of the most recent fully-closed bar at
        time t, or None if no bar has closed yet."""
        if tf not in self._frames:
            raise KeyError(f"timeframe {tf!r} not registered")
        ts = pd.Timestamp(t)
        if ts.tz is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        ts_ns = np.datetime64(ts.to_numpy(), "ns")
        idx = self._close_indices[tf]
        # We want the largest i such that close_time[i] <= ts.
        pos = int(np.searchsorted(idx, ts_ns, side="right")) - 1
        if pos < 0:
            return None
        return pos

    def last_closed(self, tf: str, t: datetime) -> Optional[pd.Series]:
        pos = self.last_closed_idx(tf, t)
        if pos is None:
            return None
        return self._frames[tf].iloc[pos]

    def frame(self, tf: str) -> pd.DataFrame:
        return self._frames[tf]
