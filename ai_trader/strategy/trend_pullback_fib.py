"""Strategy A: trend-following with Fibonacci pullback entries.

Algorithm per bar:
  1. Detect swing pivots with a ``swing_lookback`` fractal window.
  2. Classify trend from the last ``min_trend_legs`` highs & lows.
     Only act on UP or DOWN states (not RANGE).
  3. Compute the fib retracement zone (``fib_entry_min``..``fib_entry_max``)
     on the latest impulse leg.
  4. Require price to have *entered* the zone and then shown a
     rejection candle in the direction of the trend (close back past
     the opposing wick mid).
  5. Emit a BUY (uptrend) / SELL (downtrend) market Signal with:
       - SL = zone boundary ± ``sl_atr_mult`` * ATR
       - TP = entry ± ``tp_rr`` * (entry - SL)
  6. Respect ``cooldown_bars`` between signals.

This is intentionally conservative. It will skip most bars; the risk
manager's daily targets assume low trade count.
"""
from __future__ import annotations

import pandas as pd

from ..indicators import (
    atr,
    classify_trend,
    fib_retracement_zone,
    find_swings,
)
from ..indicators.trend import TrendState
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class TrendPullbackFib(BaseStrategy):
    name = "trend_pullback_fib"

    def __init__(
        self,
        swing_lookback: int = 20,
        min_trend_legs: int = 2,
        fib_entry_min: float = 0.382,
        fib_entry_max: float = 0.500,
        sl_atr_mult: float = 1.5,
        tp_rr: float = 2.0,
        atr_period: int = 14,
        cooldown_bars: int = 6,
        min_history: int | None = None,
        # Multi-leg mgmt (plan v3 §A.5). When enabled, the signal splits
        # into two legs: TP1 at tp1_rr * risk (closer), TP2 at tp_rr * risk
        # (the existing "full" target). When TP1 fills, the runner's SL
        # moves to entry (break-even).
        use_two_legs: bool = False,
        tp1_rr: float = 1.0,
        leg1_weight: float = 0.5,
    ) -> None:
        super().__init__(
            swing_lookback=swing_lookback,
            min_trend_legs=min_trend_legs,
            fib_entry_min=fib_entry_min,
            fib_entry_max=fib_entry_max,
            sl_atr_mult=sl_atr_mult,
            tp_rr=tp_rr,
            atr_period=atr_period,
            cooldown_bars=cooldown_bars,
            use_two_legs=use_two_legs,
            tp1_rr=tp1_rr,
            leg1_weight=leg1_weight,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(swing_lookback * 4, atr_period * 3, 60)

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        reason: str,
    ) -> Signal:
        p = self.params
        tp_full = entry + p["tp_rr"] * risk if side == SignalSide.BUY else entry - p["tp_rr"] * risk
        if not p.get("use_two_legs"):
            return Signal(
                side=side, entry=None, stop_loss=sl, take_profit=float(tp_full), reason=reason,
            )
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        w2 = 1.0 - w1
        # Leg 1 (TP1, closer) triggers break-even on the runner.
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1),
                      move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=w2, take_profit=float(tp_full), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None

        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        # Only look at a bounded tail. Enough bars to catch the last
        # few impulse legs (min_trend_legs * swing_lookback * some slack).
        tail_bars = max(p["swing_lookback"] * p["min_trend_legs"] * 8, 200)
        tail = history.iloc[-tail_bars:] if len(history) > tail_bars else history

        swings = find_swings(tail, lookback=p["swing_lookback"])
        if len(swings) < 2 * p["min_trend_legs"]:
            return None

        trend = classify_trend(swings, min_legs=p["min_trend_legs"])
        if trend.state == TrendState.RANGE:
            return None
        if trend.impulse_start is None or trend.impulse_end is None:
            return None

        zone = fib_retracement_zone(
            impulse_low=trend.impulse_start.price,
            impulse_high=trend.impulse_end.price,
            level_min=p["fib_entry_min"],
            level_max=p["fib_entry_max"],
        )

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last
        atr_val = atr(tail, period=p["atr_period"]).iloc[-1]
        if pd.isna(atr_val) or atr_val <= 0:
            return None

        in_zone = zone.low <= last["low"] <= zone.high or zone.low <= last["high"] <= zone.high
        if not in_zone:
            return None

        body = abs(last["close"] - last["open"])
        upper_wick = last["high"] - max(last["close"], last["open"])
        lower_wick = min(last["close"], last["open"]) - last["low"]

        if trend.state == TrendState.UP:
            # Require a bullish rejection: close above open, lower wick
            # notably larger than the body, and close above previous close.
            bullish = (
                last["close"] > last["open"]
                and lower_wick >= body
                and last["close"] > prev["close"]
            )
            if not bullish:
                return None
            entry = float(last["close"])
            sl = float(zone.low - p["sl_atr_mult"] * atr_val)
            risk = entry - sl
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                side=SignalSide.BUY,
                entry=entry,
                sl=sl,
                risk=risk,
                reason=f"up-trend pullback into fib zone [{zone.low:.2f},{zone.high:.2f}]",
            )

        if trend.state == TrendState.DOWN:
            bearish = (
                last["close"] < last["open"]
                and upper_wick >= body
                and last["close"] < prev["close"]
            )
            if not bearish:
                return None
            entry = float(last["close"])
            sl = float(zone.high + p["sl_atr_mult"] * atr_val)
            risk = sl - entry
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                side=SignalSide.SELL,
                entry=entry,
                sl=sl,
                risk=risk,
                reason=f"down-trend pullback into fib zone [{zone.low:.2f},{zone.high:.2f}]",
            )

        return None
