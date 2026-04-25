"""Friday late-session liquidation fade for XAUUSD.

The user's discretionary read of gold (and a long-documented Friday
behavior) is that during the last 1-3 hours of NY trading on Fridays,
positions are closed for the weekend. That liquidation tends to:

- accelerate any open trend into the close (overshoot), then
- snap back against that final move during the very last minutes,
  before the market shuts.

This strategy attempts to *fade* the late-Friday move on M1 XAUUSD.
It is a low-frequency, calendar-driven trade — comparable in flavor
to ``news_fade`` but anchored on the day-of-week instead of an event
in a CSV.

Algorithm:

- Only on a Friday.
- Build an "anchor" price at ``anchor_hour`` UTC (default 18:00 UTC,
  i.e. early NY afternoon).
- During ``[anchor_hour + delay_min, fri_close_hour]``, watch for
  price to displace by > ``trigger_atr`` x ATR from the anchor.
- Enter a fade in the opposite direction, with TP back at the anchor
  (or ``tp_rr * risk``) and an ATR-scaled SL.
- Hard exit by Friday close hour (no weekend exposure).

This is the same structural shape as ``news_fade`` (anchor-based,
non-overlapping with price-action scalpers, calendar-driven). It is
explicitly designed to be **uncorrelated** with the existing
strategies' edges so the ensemble's effective sample size grows.
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
class FridayFlushFade(BaseStrategy):
    name = "friday_flush_fade"

    def __init__(
        self,
        anchor_hour: int = 18,
        delay_min: int = 30,
        fri_close_hour: int = 20,
        trigger_atr: float = 1.5,
        sl_atr_mult: float = 0.6,
        tp_to_anchor: bool = True,
        tp_rr: float = 1.5,
        atr_period: int = 14,
        cooldown_bars: int = 5,
        use_two_legs: bool = True,
        tp1_rr: float = 0.6,
        leg1_weight: float = 0.5,
        max_trades_per_day: int = 1,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            anchor_hour=anchor_hour,
            delay_min=delay_min,
            fri_close_hour=fri_close_hour,
            trigger_atr=trigger_atr,
            sl_atr_mult=sl_atr_mult,
            tp_to_anchor=tp_to_anchor,
            tp_rr=tp_rr,
            atr_period=atr_period,
            cooldown_bars=cooldown_bars,
            use_two_legs=use_two_legs,
            tp1_rr=tp1_rr,
            leg1_weight=leg1_weight,
            max_trades_per_day=max_trades_per_day,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        # Per-day anchor: { day_iso: anchor_price }
        self._anchor_by_day: dict[str, float] = {}
        self._fired_days: set[str] = set()
        self._last_signal_iloc: int = -(10**9)

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        self._anchor_by_day = {}
        self._fired_days = set()

        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        idx_utc = idx.tz_convert("UTC")

        # Friday only. weekday() == 4.
        is_fri = idx_utc.weekday == 4
        anchor_hour = int(p["anchor_hour"])
        # The anchor bar is the FIRST Friday bar at or after anchor_hour.
        # Iterate per-day for clarity; data is small (~110 days).
        days = idx_utc.normalize().to_numpy()
        unique_days = np.unique(days[is_fri])
        closes = df["close"].to_numpy(dtype=float)
        for d in unique_days:
            day_mask = (days == d) & is_fri & (idx_utc.hour >= anchor_hour)
            pos = np.flatnonzero(day_mask)
            if len(pos) == 0:
                continue
            anchor_price = float(closes[pos[0]])
            day_iso = pd.Timestamp(d).date().isoformat()
            self._anchor_by_day[day_iso] = anchor_price

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, tp: float, reason: str,
    ) -> Signal:
        p = self.params
        if not p["use_two_legs"]:
            return Signal(side=side, entry=None, stop_loss=sl, take_profit=float(tp), reason=reason)
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr is None:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        if ts_utc.weekday() != 4:
            return None
        # Time gates
        anchor_hour = int(p["anchor_hour"])
        close_hour = int(p["fri_close_hour"])
        bar_hm = ts_utc.hour * 60 + ts_utc.minute
        start_min = anchor_hour * 60 + int(p["delay_min"])
        end_min = close_hour * 60
        if bar_hm < start_min or bar_hm >= end_min:
            return None
        day_iso = ts_utc.date().isoformat()
        if day_iso in self._fired_days:
            return None
        anchor = self._anchor_by_day.get(day_iso)
        if anchor is None:
            return None
        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        c = float(history.iloc[-1]["close"])
        displacement = c - anchor
        if abs(displacement) < p["trigger_atr"] * atr_val:
            return None

        if displacement < 0:
            entry = c
            sl = c - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            tp = anchor if p["tp_to_anchor"] else entry + p["tp_rr"] * risk
            if tp <= entry:
                return None
            self._fired_days.add(day_iso)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"friday-flush long anchor={anchor:.2f} disp={displacement:+.2f}",
            )
        else:
            entry = c
            sl = c + p["sl_atr_mult"] * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            tp = anchor if p["tp_to_anchor"] else entry - p["tp_rr"] * risk
            if tp >= entry:
                return None
            self._fired_days.add(day_iso)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"friday-flush short anchor={anchor:.2f} disp={displacement:+.2f}",
            )
