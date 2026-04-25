"""Asian-range sweep-and-reclaim scalper for XAUUSD.

Gold often raids one side of the quiet Asian box around London / NY
liquidity, then snaps back through the level. This strategy trades the
reclaim, not the initial stop-hunt:

- Build the Asian range from 00:00-05:00 UTC.
- During the active window, detect a sweep beyond one edge by an
  ATR-scaled buffer.
- Enter reversal only if the same bar closes back inside the range
  with a rejection wick.
- SL goes beyond the sweep extreme, capped by ATR; TP1 moves runner to
  break-even and TP2 targets the opposite box edge or an R multiple.
"""
from __future__ import annotations

from datetime import timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class SessionSweepReclaim(BaseStrategy):
    name = "session_sweep_reclaim"

    def __init__(
        self,
        range_start_hour: int = 0,
        range_end_hour: int = 5,
        trade_start_hour: int = 7,
        trade_end_hour: int = 16,
        atr_period: int = 14,
        min_range_atr: float = 0.8,
        min_sweep_atr: float = 0.15,
        sl_atr_buffer: float = 0.25,
        max_sl_atr: float = 2.0,
        tp_mode: str = "opposite_edge",  # "opposite_edge" | "rr"
        tp1_rr: float = 0.6,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        max_trades_per_day: int = 1,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            range_start_hour=range_start_hour,
            range_end_hour=range_end_hour,
            trade_start_hour=trade_start_hour,
            trade_end_hour=trade_end_hour,
            atr_period=atr_period,
            min_range_atr=min_range_atr,
            min_sweep_atr=min_sweep_atr,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            tp_mode=tp_mode,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            max_trades_per_day=max_trades_per_day,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        self._range_hi: np.ndarray | None = None
        self._range_lo: np.ndarray | None = None
        self._day_key: Optional[str] = None
        self._day_trades: int = 0
        self._long_swept: bool = False
        self._short_swept: bool = False

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        n = len(df)
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        idx_utc = idx.tz_convert("UTC")
        days = idx_utc.normalize().to_numpy()
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        rng_hi = np.full(n, np.nan)
        rng_lo = np.full(n, np.nan)
        start_min = p["range_start_hour"] * 60
        end_min = p["range_end_hour"] * 60
        for day in np.unique(days):
            pos = np.flatnonzero(days == day)
            if len(pos) == 0:
                continue
            day_idx = idx_utc[pos]
            mins = day_idx.hour * 60 + day_idx.minute
            in_range = (mins >= start_min) & (mins < end_min)
            after = mins >= end_min
            if not in_range.any():
                continue
            hi = highs[pos[in_range]].max()
            lo = lows[pos[in_range]].min()
            rng_hi[pos[after]] = hi
            rng_lo[pos[after]] = lo
        self._range_hi = rng_hi
        self._range_lo = rng_lo

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, tp2: float, reason: str
    ) -> Signal:
        p = self.params
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr is None or self._range_hi is None or self._range_lo is None:
            return None
        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key
            self._day_trades = 0
            self._long_swept = False
            self._short_swept = False
        if self._day_trades >= p["max_trades_per_day"]:
            return None
        if not (p["trade_start_hour"] <= ts_utc.hour < p["trade_end_hour"]):
            return None
        hi = float(self._range_hi[i])
        lo = float(self._range_lo[i])
        if not np.isfinite(hi) or not np.isfinite(lo):
            return None
        if hi - lo < p["min_range_atr"] * atr_val:
            return None

        last = history.iloc[-1]
        o = float(last["open"]); h = float(last["high"])
        l = float(last["low"]); c = float(last["close"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        sweep = p["min_sweep_atr"] * atr_val

        if l < lo - sweep:
            self._long_swept = True
        if h > hi + sweep:
            self._short_swept = True

        # Sweep below Asian low, reclaim back inside → long.
        if self._long_swept and c > lo and c > o:
            entry = c
            structural_sl = l - p["sl_atr_buffer"] * atr_val
            capped_sl = entry - p["max_sl_atr"] * atr_val
            sl = max(structural_sl, capped_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            tp2 = hi if p["tp_mode"] == "opposite_edge" else entry + p["tp2_rr"] * risk
            if tp2 <= entry:
                tp2 = entry + p["tp2_rr"] * risk
            self._day_trades += 1
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp2,
                reason=f"session-sweep-reclaim long lo={lo:.2f} hi={hi:.2f}",
            )

        # Sweep above Asian high, reclaim back inside → short.
        if self._short_swept and c < hi and c < o:
            entry = c
            structural_sl = h + p["sl_atr_buffer"] * atr_val
            capped_sl = entry + p["max_sl_atr"] * atr_val
            sl = min(structural_sl, capped_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            tp2 = lo if p["tp_mode"] == "opposite_edge" else entry - p["tp2_rr"] * risk
            if tp2 >= entry:
                tp2 = entry - p["tp2_rr"] * risk
            self._day_trades += 1
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp2,
                reason=f"session-sweep-reclaim short lo={lo:.2f} hi={hi:.2f}",
            )
        return None
