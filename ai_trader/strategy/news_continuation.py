"""News-continuation scalper (opposite of news_fade).

Some macro releases produce a clean directional impulse that
continues through the post-release window. news_fade catches the
overshoot+revert; news_continuation catches the trend leg AFTER
the initial spike when momentum has been confirmed.

Algorithm:
- For each high-impact event T, define a setup window
  ``[T + delay_min, T + delay_min + window_min]``.
- Inside the setup window, monitor for a sustained displacement:
  the close has moved > ``trigger_atr`` * ATR from the anchor for
  at least ``confirm_bars`` consecutive bars in the same direction.
- Enter in the SAME direction with SL on the OPPOSITE side
  (where the price has bounced from), TP at +``tp_rr`` * R OR a
  trailing stop after TP1.

Why uncorrelated with news_fade:
- news_fade fires when the displacement is large and IMMEDIATE
  (1-2 bars after delay_min); the fade catches the snap-back.
- news_continuation fires when the displacement is large AND
  PERSISTENT (confirm_bars consecutive bars); the trade catches
  the leg that did NOT snap back.
- One event can produce at most ONE of these signals; whichever
  pattern unfolds wins. Empirically gold mixes both: NFP often
  fades, CPI often continues. By having both strategies armed
  the ensemble harvests the right pattern per event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from ..news.calendar import NewsCalendar, load_news_csv
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class NewsContinuation(BaseStrategy):
    name = "news_continuation"

    def __init__(
        self,
        news_csv: str | None = None,
        impact_filter: tuple[str, ...] = ("high",),
        delay_min: int = 5,
        window_min: int = 60,
        trigger_atr: float = 1.5,
        confirm_bars: int = 3,
        sl_atr_mult: float = 0.8,
        tp_rr: float = 2.0,
        atr_period: int = 14,
        cooldown_bars: int = 5,
        use_two_legs: bool = True,
        tp1_rr: float = 1.0,
        leg1_weight: float = 0.5,
        symbol: str = "XAUUSD",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            news_csv=news_csv,
            impact_filter=impact_filter,
            delay_min=delay_min,
            window_min=window_min,
            trigger_atr=trigger_atr,
            confirm_bars=confirm_bars,
            sl_atr_mult=sl_atr_mult,
            tp_rr=tp_rr,
            atr_period=atr_period,
            cooldown_bars=cooldown_bars,
            use_two_legs=use_two_legs,
            tp1_rr=tp1_rr,
            leg1_weight=leg1_weight,
            symbol=symbol,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._event_anchors: list[tuple[datetime, float, datetime]] = []
        self._fired_events: set[datetime] = set()

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        self._fired_events = set()
        self._event_anchors = []

        if not p["news_csv"]:
            return
        try:
            events = load_news_csv(p["news_csv"])
        except FileNotFoundError:
            return

        symbol = p["symbol"]
        impacts = set(p["impact_filter"])
        idx = df.index
        delay = timedelta(minutes=p["delay_min"])
        window = timedelta(minutes=p["window_min"])

        for ev in events:
            if ev.impact not in impacts:
                continue
            if not ev.affects(symbol):
                continue
            event_time = ev.time
            pos = idx.searchsorted(event_time, side="right") - 1
            if pos < 0 or pos >= len(idx):
                continue
            anchor_price = float(df.iloc[pos]["close"])
            fade_start = event_time + delay
            fade_end = fade_start + window
            self._event_anchors.append((fade_start, anchor_price, fade_end))

        self._event_anchors.sort(key=lambda t: t[0])

    def _active_event(self, ts: datetime) -> Optional[tuple[datetime, float, datetime]]:
        for fade_start, anchor, fade_end in reversed(self._event_anchors):
            if fade_start > ts:
                continue
            if ts < fade_end:
                return (fade_start, anchor, fade_end)
            break
        return None

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
        if n < self.min_history or self._atr_cache is None or not self._event_anchors:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        if n < int(p["confirm_bars"]) + 1:
            return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        active = self._active_event(ts_dt)
        if active is None:
            return None
        fade_start, anchor, fade_end = active
        if fade_start in self._fired_events:
            return None

        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        # Confirm: last `confirm_bars` closes are all on the same side
        # of the anchor with displacement >= trigger_atr * ATR.
        cb = int(p["confirm_bars"])
        recent = history.iloc[-cb:]
        closes = recent["close"].to_numpy(dtype=float)
        disps = closes - anchor
        threshold = float(p["trigger_atr"]) * atr_val
        if np.all(disps > threshold):
            # Sustained UP — continuation long.
            entry = float(history.iloc[-1]["close"])
            # SL: low of the confirm window minus buffer.
            sl_anchor = float(recent["low"].min())
            sl = sl_anchor - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + p["tp_rr"] * risk
            self._fired_events.add(fade_start)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"news-cont long anchor={anchor:.2f} disp={float(disps[-1]):+.2f}",
            )
        if np.all(disps < -threshold):
            entry = float(history.iloc[-1]["close"])
            sl_anchor = float(recent["high"].max())
            sl = sl_anchor + p["sl_atr_mult"] * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - p["tp_rr"] * risk
            self._fired_events.add(fade_start)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"news-cont short anchor={anchor:.2f} disp={float(disps[-1]):+.2f}",
            )
        return None
