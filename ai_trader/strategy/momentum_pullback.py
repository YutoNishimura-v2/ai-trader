"""Impulse pullback continuation scalper for XAUUSD.

Discretionary gold scalping often starts with a displacement candle:
price expands fast, pauses, then gives a shallow pullback into the
38.2-61.8% zone before continuing. This strategy makes that pattern
causal and testable:

1. Detect an impulse bar whose body is >= ``impulse_body_atr`` * ATR
   and whose close is near the impulse extreme.
2. Arm a setup in the impulse direction with a fib pullback zone.
3. Enter only when price trades into that zone and closes back in the
   impulse direction.
4. Two legs: TP1 moves the runner to break-even, TP2 is RR-based.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class MomentumPullback(BaseStrategy):
    name = "momentum_pullback"

    def __init__(
        self,
        atr_period: int = 14,
        impulse_body_atr: float = 1.0,
        impulse_close_frac: float = 0.25,
        fib_min: float = 0.382,
        fib_max: float = 0.618,
        sl_atr_buffer: float = 0.25,
        max_sl_atr: float = 2.0,
        tp1_rr: float = 0.6,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        setup_ttl_bars: int = 20,
        cooldown_bars: int = 5,
        session: str = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period,
            impulse_body_atr=impulse_body_atr,
            impulse_close_frac=impulse_close_frac,
            fib_min=fib_min,
            fib_max=fib_max,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            setup_ttl_bars=setup_ttl_bars,
            cooldown_bars=cooldown_bars,
            session=session,
        )
        self.min_history = min_history or max(atr_period * 3, 80)
        self._atr: pd.Series | None = None
        self._setup: Optional[dict] = None
        self._last_signal_iloc = -(10**9)

    def prepare(self, df: pd.DataFrame) -> None:
        self._atr = atr(df, period=self.params["atr_period"])
        self._setup = None
        self._last_signal_iloc = -(10**9)

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, reason: str
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

    def _maybe_arm(self, n: int, last, atr_val: float) -> None:
        p = self.params
        o = float(last["open"]); h = float(last["high"])
        l = float(last["low"]); c = float(last["close"])
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        if body < p["impulse_body_atr"] * atr_val:
            return
        # If a live setup is already armed, do not let ordinary
        # pullback candles overwrite it. Only a substantially larger
        # displacement is allowed to replace the active impulse.
        if self._setup is not None and body < 1.5 * float(self._setup["atr"]):
            return
        # Bull impulse closes in the top X% of its range.
        if c > o and (h - c) / rng <= p["impulse_close_frac"]:
            pull_min = h - p["fib_max"] * (h - l)
            pull_max = h - p["fib_min"] * (h - l)
            self._setup = {
                "side": SignalSide.BUY, "low": l, "high": h,
                "zone_low": pull_min, "zone_high": pull_max,
                "atr": atr_val, "born": n,
            }
        elif c < o and (c - l) / rng <= p["impulse_close_frac"]:
            pull_min = l + p["fib_min"] * (h - l)
            pull_max = l + p["fib_max"] * (h - l)
            self._setup = {
                "side": SignalSide.SELL, "low": l, "high": h,
                "zone_low": pull_min, "zone_high": pull_max,
                "atr": atr_val, "born": n,
            }

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr is None:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        ts = history.index[-1]
        t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
        if p["session"] != "always" and not check_session(t, p["session"]):
            return None
        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last

        # Existing setup gets first chance to trigger; a same-bar new
        # impulse may arm only for future bars.
        if self._setup is not None:
            s = self._setup
            if n - int(s["born"]) > p["setup_ttl_bars"]:
                self._setup = None
            else:
                o = float(last["open"]); h = float(last["high"])
                l = float(last["low"]); c = float(last["close"])
                side = s["side"]
                touched = l <= s["zone_high"] and h >= s["zone_low"]
                if side == SignalSide.BUY and touched and c > o and c > float(prev["close"]):
                    entry = c
                    structural_sl = float(s["low"]) - p["sl_atr_buffer"] * float(s["atr"])
                    capped_sl = entry - p["max_sl_atr"] * atr_val
                    sl = max(structural_sl, capped_sl)
                    risk = entry - sl
                    if risk > 0:
                        self._setup = None
                        self._last_signal_iloc = n
                        return self._build_signal(
                            SignalSide.BUY, entry, sl, risk,
                            reason=f"momentum-pullback long zone={s['zone_low']:.2f}-{s['zone_high']:.2f}",
                        )
                if side == SignalSide.SELL and touched and c < o and c < float(prev["close"]):
                    entry = c
                    structural_sl = float(s["high"]) + p["sl_atr_buffer"] * float(s["atr"])
                    capped_sl = entry + p["max_sl_atr"] * atr_val
                    sl = min(structural_sl, capped_sl)
                    risk = sl - entry
                    if risk > 0:
                        self._setup = None
                        self._last_signal_iloc = n
                        return self._build_signal(
                            SignalSide.SELL, entry, sl, risk,
                            reason=f"momentum-pullback short zone={s['zone_low']:.2f}-{s['zone_high']:.2f}",
                        )

        self._maybe_arm(n, last, atr_val)
        return None
