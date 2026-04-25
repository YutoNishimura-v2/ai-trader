"""Post-news continuation / breakout strategy for XAUUSD.

``news_fade`` captures one population: macro releases that spike and
mean-revert. Some GOLD releases do the opposite: the first impulse is
the start of a one-way repricing. This strategy is the complementary
event-driven candidate:

1. For each high-impact event, build the initial post-release range
   from ``T`` through ``T + range_min``.
2. After ``T + delay_min``, watch for price to close beyond that range
   by ``break_atr`` * ATR.
3. Enter continuation on a retest/hold of the broken edge with a
   candle in the breakout direction.
4. Use two legs: TP1 quickly moves the runner to break-even, TP2
   captures the trend extension.

All anchors/ranges are computed in ``prepare`` from bars whose close
time is known before the first eligible entry bar; ``on_bar`` only
reads the current and prior bars plus cached causal state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from ..news.calendar import load_news_csv
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@dataclass(frozen=True)
class _NewsRange:
    event_time: datetime
    trade_start: datetime
    trade_end: datetime
    high: float
    low: float


@register_strategy
class NewsBreakout(BaseStrategy):
    name = "news_breakout"

    def __init__(
        self,
        news_csv: str | None = None,
        impact_filter: tuple[str, ...] = ("high",),
        symbol: str = "XAUUSD",
        initial_range_min: int = 5,
        delay_min: int = 10,
        window_min: int = 90,
        break_atr: float = 0.5,
        retest_tolerance_atr: float = 0.5,
        sl_atr_buffer: float = 0.3,
        max_sl_atr: float = 2.5,
        atr_period: int = 14,
        cooldown_bars: int = 5,
        max_trades_per_event: int = 1,
        tp1_rr: float = 0.6,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            news_csv=news_csv,
            impact_filter=impact_filter,
            symbol=symbol,
            initial_range_min=initial_range_min,
            delay_min=delay_min,
            window_min=window_min,
            break_atr=break_atr,
            retest_tolerance_atr=retest_tolerance_atr,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            atr_period=atr_period,
            cooldown_bars=cooldown_bars,
            max_trades_per_event=max_trades_per_event,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        self._ranges: list[_NewsRange] = []
        self._fired_counts: dict[datetime, int] = {}
        self._last_signal_iloc: int = -(10**9)

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        self._ranges = []
        self._fired_counts = {}
        if not p["news_csv"]:
            return
        try:
            events = load_news_csv(p["news_csv"])
        except FileNotFoundError:
            return

        idx = df.index
        impacts = set(p["impact_filter"])
        symbol = p["symbol"]
        range_delta = timedelta(minutes=p["initial_range_min"])
        delay_delta = timedelta(minutes=p["delay_min"])
        window_delta = timedelta(minutes=p["window_min"])

        for ev in events:
            if ev.impact not in impacts or not ev.affects(symbol):
                continue
            start = ev.time
            range_end = start + range_delta
            lo = idx.searchsorted(start, side="left")
            # Use bars whose timestamps are strictly before
            # ``range_end``. A bar at exactly T+range_min is the
            # first post-range bar and must not be part of the cached
            # high/low; otherwise the breakout bar can leak into its
            # own trigger range.
            hi = idx.searchsorted(range_end, side="left")
            # If the requested initial range is longer than the delay,
            # the range is unknowable until it completes; never allow
            # entries before both clocks have elapsed.
            trade_start = max(start + delay_delta, range_end)
            if lo < 0 or hi <= lo or lo >= len(df):
                continue
            range_df = df.iloc[lo:min(hi, len(df))]
            if range_df.empty:
                continue
            self._ranges.append(
                _NewsRange(
                    event_time=start,
                    trade_start=trade_start,
                    trade_end=trade_start + window_delta,
                    high=float(range_df["high"].max()),
                    low=float(range_df["low"].min()),
                )
            )
        self._ranges.sort(key=lambda r: r.trade_start)

    def _active_range(self, ts: datetime) -> Optional[_NewsRange]:
        for r in reversed(self._ranges):
            if r.trade_start > ts:
                continue
            if ts < r.trade_end:
                return r
            break
        return None

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        reason: str,
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
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if self._atr is None or not self._ranges:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_dt = ts_dt.astimezone(timezone.utc)
        active = self._active_range(ts_dt)
        if active is None:
            return None
        if self._fired_counts.get(active.event_time, 0) >= p["max_trades_per_event"]:
            return None

        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last
        o = float(last["open"])
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        prev_c = float(prev["close"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        tol = p["retest_tolerance_atr"] * atr_val
        break_buf = p["break_atr"] * atr_val

        # Long continuation: close has accepted above the news range
        # and the bar retested/held the broken high.
        if c > active.high + break_buf:
            retested = l <= active.high + tol
            held = c > active.high and c > o and lower_wick >= 0.5 * max(body, 1e-9)
            if retested and held:
                entry = c
                structural_sl = active.low - p["sl_atr_buffer"] * atr_val
                capped_sl = entry - p["max_sl_atr"] * atr_val
                sl = max(structural_sl, capped_sl)
                risk = entry - sl
                if risk > 0:
                    self._last_signal_iloc = n
                    self._fired_counts[active.event_time] = self._fired_counts.get(active.event_time, 0) + 1
                    return self._build_signal(
                        SignalSide.BUY, entry, sl, risk,
                        reason=f"news-breakout long hi={active.high:.2f} lo={active.low:.2f}",
                    )

        # Short continuation: close has accepted below the news range
        # and the bar retested/held the broken low.
        if c < active.low - break_buf:
            retested = h >= active.low - tol
            held = c < active.low and c < o and upper_wick >= 0.5 * max(body, 1e-9)
            if retested and held:
                entry = c
                structural_sl = active.high + p["sl_atr_buffer"] * atr_val
                capped_sl = entry + p["max_sl_atr"] * atr_val
                sl = min(structural_sl, capped_sl)
                risk = sl - entry
                if risk > 0:
                    self._last_signal_iloc = n
                    self._fired_counts[active.event_time] = self._fired_counts.get(active.event_time, 0) + 1
                    return self._build_signal(
                        SignalSide.SELL, entry, sl, risk,
                        reason=f"news-breakout short hi={active.high:.2f} lo={active.low:.2f}",
                    )
        return None
