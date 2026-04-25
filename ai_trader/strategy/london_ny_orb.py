"""London/NY Open 15-min ORB scalper for XAUUSD.

Different from `london_orb` (which uses Asian range). This strategy
uses a SHORT 15-minute opening range right at session open, then
trades a CONFIRMED breakout on the M5 close. Source: TradingView
"Gold ORB Strategy (15-min Range, 5-min Entry)" + GrandAlgo's
ORB guide.

Algorithm:

1. Define an opening range: open_start..open_start+range_minutes.
   Defaults: London open 07:00-07:15 UTC, or NY open 13:30-13:45 UTC.
2. After the range closes, watch M5 candles. A long entry triggers
   when an M5 candle CLOSES above the range high (wicks don't
   count). Short on close below range low.
3. SL = 50% of the ORB range, on the opposite side of entry from the
   broken edge (not under recent swing — keeps it tight).
4. TP = 2× ORB range (1:2 RR per source).
5. One trade per UTC day max. Trade only inside trade_window (e.g.,
   open_start..open_start + 4h to avoid late-day chop).
6. Optional ATR floor on range size (skip days too quiet).
7. Two-leg TP1+BE on the runner (matching iter28 convention).
"""
from __future__ import annotations

from datetime import time as dtime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class LondonNyOrb(BaseStrategy):
    name = "london_ny_orb"

    def __init__(
        self,
        # When the opening range starts (UTC). Defaults to London open.
        open_hour: int = 7,
        open_minute: int = 0,
        range_minutes: int = 15,
        # How long after the range we may take a breakout (UTC hours).
        trade_window_hours: int = 4,
        # Confirmation TF for the breakout close (must be a divisor of 60).
        confirm_minutes: int = 5,
        # Skip days where the ORB itself is too small.
        min_range_atr: float = 0.5,
        atr_period: int = 14,
        # Stop = 50% of the ORB range; TP = 2x ORB range.
        sl_pct_of_range: float = 0.5,
        tp_mult_of_range: float = 2.0,
        tp1_rr: float = 1.0,
        leg1_weight: float = 0.5,
        weekdays: list[int] | tuple[int, ...] | None = None,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            open_hour=open_hour, open_minute=open_minute,
            range_minutes=range_minutes,
            trade_window_hours=trade_window_hours,
            confirm_minutes=confirm_minutes,
            min_range_atr=min_range_atr, atr_period=atr_period,
            sl_pct_of_range=sl_pct_of_range,
            tp_mult_of_range=tp_mult_of_range,
            tp1_rr=tp1_rr, leg1_weight=leg1_weight,
            weekdays=tuple(weekdays) if weekdays is not None else None,
        )
        self.min_history = min_history or max(atr_period * 3, 120)
        # State per UTC day.
        self._day_key: str | None = None
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._range_done: bool = False
        self._traded_today: bool = False
        # Precomputed ATR (causal).
        self._atr: pd.Series | None = None
        # Last M5 close timestamp we've already considered.
        self._last_handled_m5_minute: int = -1

    def prepare(self, df: pd.DataFrame) -> None:
        self._atr = atr(df, period=int(self.params["atr_period"]))

    def _reset_day(self):
        self._range_high = None
        self._range_low = None
        self._range_done = False
        self._traded_today = False
        self._last_handled_m5_minute = -1

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr is None:
            return None
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        wds = p.get("weekdays")
        if wds is not None and ts_utc.weekday() not in wds:
            return None

        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key
            self._reset_day()
        if self._traded_today:
            return None

        # Are we inside the opening range?
        open_h = int(p["open_hour"])
        open_m = int(p["open_minute"])
        rng_min = int(p["range_minutes"])
        open_dt = ts_utc.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        range_end_dt = open_dt + pd.Timedelta(minutes=rng_min)
        window_end_dt = open_dt + pd.Timedelta(hours=int(p["trade_window_hours"]))

        # Before open range opens: nothing to do.
        if ts_utc < open_dt:
            return None
        # During the opening range: build the range.
        if ts_utc < range_end_dt:
            last = history.iloc[-1]
            h = float(last["high"]); l = float(last["low"])
            self._range_high = h if self._range_high is None else max(self._range_high, h)
            self._range_low  = l if self._range_low  is None else min(self._range_low,  l)
            return None
        # Exactly at range close: mark done if we have a valid range.
        if not self._range_done:
            if self._range_high is None or self._range_low is None:
                # Day didn't include the open range (data gap).
                return None
            atr_val = float(self._atr.iloc[n - 1])
            if not np.isfinite(atr_val) or atr_val <= 0:
                return None
            range_size = self._range_high - self._range_low
            if range_size < float(p["min_range_atr"]) * atr_val:
                # Too quiet a day. Skip.
                self._traded_today = True
                return None
            self._range_done = True

        if ts_utc >= window_end_dt:
            self._traded_today = True
            return None

        # Watch only on M5 closes (or whatever confirm_minutes is).
        cm = int(p["confirm_minutes"])
        cur_min = ts_utc.hour * 60 + ts_utc.minute
        # M5 closes at 00:05, 00:10, etc. Accept current bar only if
        # ts_utc.minute % cm == 0 AND we haven't already handled this
        # M5 close.
        if ts_utc.minute % cm != 0:
            return None
        if cur_min == self._last_handled_m5_minute:
            return None

        # Aggregate the most recent `cm` M1 bars to form an M5 close.
        seg = history.iloc[-cm:]
        if len(seg) < cm:
            return None
        m5_close = float(seg["close"].iloc[-1])
        m5_high = float(seg["high"].max())
        m5_low = float(seg["low"].min())
        self._last_handled_m5_minute = cur_min

        rh = float(self._range_high)
        rl = float(self._range_low)
        rng = rh - rl
        sl_off = float(p["sl_pct_of_range"]) * rng
        tp_off = float(p["tp_mult_of_range"]) * rng

        entry = m5_close
        if m5_close > rh:
            sl = rh - sl_off          # 50% inside the range
            tp_struct = entry + tp_off  # 2× range
            risk = entry - sl
            if risk <= 0:
                return None
            self._traded_today = True
            return self._build_signal(SignalSide.BUY, entry, sl, tp_struct, risk,
                                      reason=f"orb-long break {rh:.2f}")
        if m5_close < rl:
            sl = rl + sl_off
            tp_struct = entry - tp_off
            risk = sl - entry
            if risk <= 0:
                return None
            self._traded_today = True
            return self._build_signal(SignalSide.SELL, entry, sl, tp_struct, risk,
                                      reason=f"orb-short break {rl:.2f}")
        return None

    def _build_signal(self, side, entry, sl, tp_struct, risk, reason):
        p = self.params
        if side == SignalSide.BUY:
            tp1 = entry + float(p["tp1_rr"]) * risk
            tp2 = max(tp_struct, entry + 1.5 * risk)
        else:
            tp1 = entry - float(p["tp1_rr"]) * risk
            tp2 = min(tp_struct, entry - 1.5 * risk)
        w1 = float(p["leg1_weight"])
        if w1 >= 0.999:
            legs = (SignalLeg(weight=1.0, take_profit=float(tp1), tag="tp1"),)
        else:
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1),
                          move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)
