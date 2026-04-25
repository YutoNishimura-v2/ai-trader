"""London-Open Asian-Range Breakout (ORB) scalper.

Rationale (well-documented in published XAUUSD scalping
frameworks): the Asian session (00:00-05:00 UTC) on gold is
range-bound and accumulates resting stops just outside that
range. The London open (07:00 UTC) injects liquidity that
typically takes one side of the Asian range and runs.

Pure breakout-buying gets stop-hunted. The robust version waits
for a CONFIRMED close beyond the range, then enters on a RETEST
of the broken edge with a rejection candle.

Per UTC day:

1. Build the Asian range as ``[range_start_utc..range_end_utc]``
   high and low.
2. Skip the day if the range is too small (< ``min_range_atr * ATR``).
3. After ``range_end_utc`` and within the ``window_min`` after,
   watch for a bar to CLOSE beyond the range high (long break)
   or below the range low (short break) by at least
   ``min_break_atr * ATR``. Mark the broken edge.
4. While the day's window is active, enter on retest:
   - long: bar low pierces the broken-high level by less than
     ``retest_tolerance_atr * ATR`` and the bar prints a bullish
     rejection candle; SL just below the Asian range low.
   - short: mirrored.
5. One trade per UTC day max; cooldown irrelevant (gated by
   the per-day flag).
6. 2-leg TP with break-even on TP1.

Optional session filter (default 'always' since the strategy is
already day-time-bounded by construction).

Prepare hook caches per-bar: ATR, the day's Asian-range high/low
(forward-fill the day's range across all bars of that UTC day),
and a ``day`` marker for state-tracking.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class LondonORB(BaseStrategy):
    name = "london_orb"

    def __init__(
        self,
        range_start_hour: int = 0,
        range_start_min: int = 0,
        range_end_hour: int = 5,
        range_end_min: int = 0,
        window_min: int = 180,
        min_range_atr: float = 1.5,
        min_break_atr: float = 0.2,
        retest_tolerance_atr: float = 0.7,
        sl_atr_buffer: float = 0.3,
        max_sl_atr: float = 5.0,    # cap SL distance to N * ATR (structural SL can be huge)
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        atr_period: int = 14,
        max_trades_per_day: int = 1,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            range_start_hour=range_start_hour,
            range_start_min=range_start_min,
            range_end_hour=range_end_hour,
            range_end_min=range_end_min,
            window_min=window_min,
            min_range_atr=min_range_atr,
            min_break_atr=min_break_atr,
            retest_tolerance_atr=retest_tolerance_atr,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            atr_period=atr_period,
            max_trades_per_day=max_trades_per_day,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        # Per-bar cached arrays (NaN where unset).
        self._range_hi: np.ndarray | None = None
        self._range_lo: np.ndarray | None = None
        self._range_done_at: np.ndarray | None = None  # 1 if range_end has passed for this bar
        # Per-day setup state (lives in on_bar; reset on day rollover).
        self._day_key: Optional[str] = None
        self._day_break_side: Optional[str] = None        # 'long' or 'short'
        self._day_break_level: Optional[float] = None     # the broken edge price
        self._day_invalidation: Optional[float] = None    # opposite Asian extreme
        self._day_atr_at_break: Optional[float] = None
        self._day_trades: int = 0
        self._day_window_end_min: int = 0  # minute-of-day at which the window expires

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        n = len(df)
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        # Normalize to UTC for grouping.
        idx_utc = idx.tz_convert("UTC")
        days = idx_utc.normalize()  # midnight of each bar's UTC day
        rs_h, rs_m = p["range_start_hour"], p["range_start_min"]
        re_h, re_m = p["range_end_hour"], p["range_end_min"]

        # Per-bar arrays: range_hi/lo populated only AFTER the
        # range has closed (causal: the range is known at re_h:re_m).
        rng_hi = np.full(n, np.nan)
        rng_lo = np.full(n, np.nan)
        done = np.zeros(n, dtype=np.int8)

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        # Group by UTC day. For each day, compute the range from bars
        # within [start, end) and forward-fill to the rest of that day.
        day_arr = days.to_numpy()
        unique_days, first_idx = np.unique(day_arr, return_index=True)
        # iterate days
        order = np.argsort(first_idx)
        for k in order:
            day = unique_days[k]
            day_mask = (day_arr == day)
            day_positions = np.flatnonzero(day_mask)
            if len(day_positions) == 0:
                continue
            day_idx = idx_utc[day_positions]
            mins = day_idx.hour * 60 + day_idx.minute
            start_min = rs_h * 60 + rs_m
            end_min = re_h * 60 + re_m
            # range bars: start_min <= bar < end_min
            in_range = (mins >= start_min) & (mins < end_min)
            after_range = mins >= end_min
            if not in_range.any():
                continue
            rh = highs[day_positions[in_range]].max()
            rl = lows[day_positions[in_range]].min()
            after_positions = day_positions[after_range]
            if len(after_positions) > 0:
                rng_hi[after_positions] = rh
                rng_lo[after_positions] = rl
                done[after_positions] = 1
        self._range_hi = rng_hi
        self._range_lo = rng_lo
        self._range_done_at = done

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, reason: str,
    ) -> Signal:
        p = self.params
        if side == SignalSide.BUY:
            tp1 = entry + p["tp1_rr"] * risk
            tp2 = entry + p["tp2_rr"] * risk
        else:
            tp1 = entry - p["tp1_rr"] * risk
            tp2 = entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1),
                      move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def _reset_day(self, day_key: str) -> None:
        self._day_key = day_key
        self._day_break_side = None
        self._day_break_level = None
        self._day_invalidation = None
        self._day_atr_at_break = None
        self._day_trades = 0
        self._day_window_end_min = 0

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if (self._atr is None or self._range_hi is None
                or self._range_lo is None or self._range_done_at is None):
            return None
        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        # Day-key roll-over (always, regardless of whether the
        # range has closed yet). Set window-end here so it's ready
        # the moment the range closes.
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._reset_day(day_key)
            self._day_window_end_min = (
                p["range_end_hour"] * 60 + p["range_end_min"] + p["window_min"]
            )

        if not self._range_done_at[i]:
            return None

        bar_min_of_day = ts_utc.hour * 60 + ts_utc.minute
        if bar_min_of_day > self._day_window_end_min:
            return None  # past the setup window
        if self._day_trades >= p["max_trades_per_day"]:
            return None

        rh = float(self._range_hi[i])
        rl = float(self._range_lo[i])
        if not np.isfinite(rh) or not np.isfinite(rl):
            return None
        rng = rh - rl
        if rng < p["min_range_atr"] * atr_val:
            return None  # dead-day

        last = history.iloc[-1]
        prev = history.iloc[-2]
        c = float(last["close"]); o = float(last["open"])
        hi = float(last["high"]); lo = float(last["low"])
        body = abs(c - o)
        upper_wick = hi - max(c, o)
        lower_wick = min(c, o) - lo

        # Establish the breakout direction (uses the bar that just CLOSED).
        if self._day_break_side is None:
            break_buf = p["min_break_atr"] * atr_val
            if c > rh + break_buf:
                self._day_break_side = "long"
                self._day_break_level = rh
                self._day_invalidation = rl
                self._day_atr_at_break = atr_val
            elif c < rl - break_buf:
                self._day_break_side = "short"
                self._day_break_level = rl
                self._day_invalidation = rh
                self._day_atr_at_break = atr_val

        if self._day_break_side is None:
            return None

        # Now look for a retest entry on subsequent bars.
        tol = p["retest_tolerance_atr"] * (self._day_atr_at_break or atr_val)
        if self._day_break_side == "long":
            in_retest = (self._day_break_level - tol) <= lo <= (self._day_break_level + tol)
            bullish = (
                c > o and lower_wick >= body
                and c > float(prev["close"])
                and c > self._day_break_level
            )
            if in_retest and bullish:
                entry = c
                struct_sl = (self._day_invalidation or rl) - p["sl_atr_buffer"] * (self._day_atr_at_break or atr_val)
                # Cap SL distance to max_sl_atr * ATR; an Asian
                # range of $50+ would otherwise force tiny lot sizes.
                cap_sl = entry - p["max_sl_atr"] * (self._day_atr_at_break or atr_val)
                sl = max(struct_sl, cap_sl)
                risk = entry - sl
                if risk > 0:
                    self._day_trades += 1
                    return self._build_signal(
                        SignalSide.BUY, entry, sl, risk,
                        reason=f"London ORB long break={self._day_break_level:.2f} inv={self._day_invalidation:.2f}",
                    )
        else:  # short
            in_retest = (self._day_break_level - tol) <= hi <= (self._day_break_level + tol)
            bearish = (
                c < o and upper_wick >= body
                and c < float(prev["close"])
                and c < self._day_break_level
            )
            if in_retest and bearish:
                entry = c
                struct_sl = (self._day_invalidation or rh) + p["sl_atr_buffer"] * (self._day_atr_at_break or atr_val)
                cap_sl = entry + p["max_sl_atr"] * (self._day_atr_at_break or atr_val)
                sl = min(struct_sl, cap_sl)
                risk = sl - entry
                if risk > 0:
                    self._day_trades += 1
                    return self._build_signal(
                        SignalSide.SELL, entry, sl, risk,
                        reason=f"London ORB short break={self._day_break_level:.2f} inv={self._day_invalidation:.2f}",
                    )

        return None
