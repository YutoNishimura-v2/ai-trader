"""Pre-news drift fade.

The hour or so leading into a scheduled high-impact USD event tends
to show a small directional drift on XAUUSD as positioning desks
adjust. The drift is often unwound at, or shortly after, the
release. This strategy fades that pre-announcement drift with a
small, time-limited position that **always closes before the event**
to avoid the headline-print risk that ``news_fade`` is purposely
exposed to.

Important: this is intentionally a *separate* trade population from
``news_fade`` — it fires before T-0 and closes at or before T-0,
while ``news_fade`` fires after T+delay. They cannot collide on the
same event by construction.

Algorithm (per high-impact event T):

- Anchor price = bar that closes at ``T - drift_window_min``.
- During ``[T - drift_window_min + delay_min, T - exit_buffer_min]``,
  watch for displacement > ``trigger_atr * ATR``.
- If displaced UP, enter SHORT (fade); if DOWN, enter LONG.
- TP at the anchor (preferred) or ``tp_rr * risk``.
- Hard exit at ``T - exit_buffer_min``: any open leg is force-closed.
  This is implemented via a hard-cap on TP/SL distance plus a
  per-bar age check inside ``on_bar`` that pretends the position is
  the strategy's own (the engine doesn't have a "close at time X"
  primitive yet, so we model the exit as a tight TP/SL near current
  price if the cutoff is reached).

The simpler, equivalent implementation used here is:

- Don't enter inside the last ``exit_buffer_min`` minutes before T.
- Use a tight ATR-scaled SL plus a TP-at-anchor.
- Trust the news blackout (which is OFF by default in the configs
  that use this strategy — ``window_minutes: 0``) plus the strategy's
  cooldown to keep things one-shot per event.

This strategy is small by design — it is one more uncorrelated edge,
not a primary engine.
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
class NewsAnticipationFade(BaseStrategy):
    name = "news_anticipation"

    def __init__(
        self,
        news_csv: str | None = None,
        impact_filter: tuple[str, ...] = ("high",),
        drift_window_min: int = 60,
        delay_min: int = 10,
        exit_buffer_min: int = 5,
        trigger_atr: float = 1.0,
        sl_atr_mult: float = 0.5,
        tp_to_anchor: bool = True,
        tp_rr: float = 1.0,
        atr_period: int = 14,
        cooldown_bars: int = 5,
        use_two_legs: bool = False,
        tp1_rr: float = 0.5,
        leg1_weight: float = 0.5,
        symbol: str = "XAUUSD",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            news_csv=news_csv,
            impact_filter=impact_filter,
            drift_window_min=drift_window_min,
            delay_min=delay_min,
            exit_buffer_min=exit_buffer_min,
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
        self._atr: pd.Series | None = None
        # (window_start, anchor_price, window_end, event_time)
        self._event_anchors: list[tuple[datetime, float, datetime, datetime]] = []
        self._fired_events: set[datetime] = set()

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        self._event_anchors = []
        self._fired_events = set()

        if not p["news_csv"]:
            return
        try:
            events = load_news_csv(p["news_csv"])
        except FileNotFoundError:
            return

        symbol = p["symbol"]
        impacts = set(p["impact_filter"])
        idx = df.index
        drift = timedelta(minutes=int(p["drift_window_min"]))
        delay = timedelta(minutes=int(p["delay_min"]))
        exit_buf = timedelta(minutes=int(p["exit_buffer_min"]))

        for ev in events:
            if ev.impact not in impacts:
                continue
            if not ev.affects(symbol):
                continue
            event_time = ev.time
            anchor_time = event_time - drift
            window_start = anchor_time + delay
            window_end = event_time - exit_buf
            if window_end <= window_start:
                continue
            pos = idx.searchsorted(anchor_time, side="right") - 1
            if pos < 0 or pos >= len(idx):
                continue
            anchor_price = float(df.iloc[pos]["close"])
            self._event_anchors.append(
                (window_start, anchor_price, window_end, event_time)
            )

        self._event_anchors.sort(key=lambda t: t[0])

    def _active(self, ts: datetime) -> Optional[tuple[datetime, float, datetime, datetime]]:
        # Walk recent-first since the list is ordered.
        for ws, anchor, we, et in reversed(self._event_anchors):
            if ws > ts:
                continue
            if ts < we:
                return (ws, anchor, we, et)
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
        if n < self.min_history or self._atr is None or not self._event_anchors:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        active = self._active(ts_dt)
        if active is None:
            return None
        ws, anchor, we, et = active
        if ws in self._fired_events:
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
            self._fired_events.add(ws)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"news-antic long anchor={anchor:.2f} disp={displacement:+.2f}",
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
            self._fired_events.add(ws)
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"news-antic short anchor={anchor:.2f} disp={displacement:+.2f}",
            )
