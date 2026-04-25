"""News-fade scalper.

The opposite of news-blackout: instead of skipping high-impact
events, ONLY trade them. After a NFP / CPI / FOMC release, gold
typically:
  1) spikes one direction on the headline number
  2) mean-reverts within 15-60 minutes as the initial overreaction
     fades and the algos settle on a fair price.

Strategy:
- Use the same news CSV (`data/news/xauusd_2026.csv`) as the
  blackout filter; here it's the *trigger*, not the suppressor.
- For each event time T, define a fade window
  ``[T + delay_min, T + delay_min + window_min]``.
- Inside that window, watch for an "extreme" move: the bar's
  CLOSE has moved > ``trigger_atr`` × ATR away from the price
  at T (the bar that contained T).
- Enter in the OPPOSITE direction with a tight ATR-based SL and
  a TP at the price-at-T (the "anchor").
- One trade per event (cooldown = remaining window after a fill).

Why this might have edge:
- News reactions are well-documented to overshoot.
- Most strategies SIT OUT news (our blackout does too); fading
  the overshoot is a different population of trades.
- The trade has a structural anchor (the pre-news price) so SL/TP
  aren't arbitrary multiples — they reference reality.

prepare() caches ATR. The event list is loaded once via the same
``NewsCalendar`` machinery as the blackout, so adding events is a
CSV edit.
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
class NewsFade(BaseStrategy):
    name = "news_fade"

    def __init__(
        self,
        news_csv: str | None = None,
        impact_filter: tuple[str, ...] = ("high",),
        delay_min: int = 5,
        window_min: int = 60,
        trigger_atr: float = 2.0,
        sl_atr_mult: float = 0.5,
        tp_to_anchor: bool = True,
        tp_rr: float = 1.5,
        atr_period: int = 14,
        cooldown_bars: int = 5,
        use_two_legs: bool = True,
        tp1_rr: float = 0.6,
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
            sl_atr_mult=sl_atr_mult,
            tp_to_anchor=tp_to_anchor,
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
        self._calendar: NewsCalendar | None = None
        # Map: event_time → (anchor_price, fade_window_end). Built
        # in prepare() once.
        self._event_anchors: list[tuple[datetime, float, datetime]] = []
        # Tracker: which events we've already fired on (one-trade-
        # per-event rule).
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
            # Find the bar that CONTAINS the event time (use
            # forward-fill: the bar whose timestamp is the latest
            # one <= event_time). At M1 every event in our CSV
            # falls on a bar boundary, but we be defensive.
            pos = idx.searchsorted(event_time, side="right") - 1
            if pos < 0 or pos >= len(idx):
                continue
            anchor_price = float(df.iloc[pos]["close"])
            fade_start = event_time + delay
            fade_end = fade_start + window
            self._event_anchors.append((fade_start, anchor_price, fade_end))

        # Sort for fast lookup.
        self._event_anchors.sort(key=lambda t: t[0])

    def _active_event(self, ts: datetime) -> Optional[tuple[datetime, float, datetime]]:
        """Return the most recent active event window covering ts."""
        for fade_start, anchor, fade_end in reversed(self._event_anchors):
            if fade_start > ts:
                continue
            if ts < fade_end:
                return (fade_start, anchor, fade_end)
            # Otherwise fade window expired; older events are even
            # earlier, so we can break.
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
        if n < self.min_history:
            return None
        if self._atr_cache is None:
            return None
        if not self._event_anchors:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
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

        last = history.iloc[-1]
        c = float(last["close"])
        displacement = c - anchor
        if abs(displacement) < p["trigger_atr"] * atr_val:
            return None

        # Fade: long if price has moved DOWN (displacement < 0).
        if displacement < 0:
            entry = c
            sl = c - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            tp = anchor if p["tp_to_anchor"] else entry + p["tp_rr"] * risk
            if tp <= entry:
                return None
            self._fired_events.add(fade_start)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"news-fade long anchor={anchor:.2f} disp={displacement:+.2f}",
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
            self._fired_events.add(fade_start)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"news-fade short anchor={anchor:.2f} disp={displacement:+.2f}",
            )
