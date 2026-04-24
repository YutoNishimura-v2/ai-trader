"""BOS-retest scalper (user's original strategy 1, structural version).

The user's description: "rising highs and rising lows = strong
uptrend; I enter on pullbacks to fib 38.2-50". The prior
`trend_pullback_scalper` used an EMA proxy for "trend"; this one
uses the textbook Smart-Money / ICT definition:

- **Structural uptrend**: at least 2 higher swing highs and 2
  higher swing lows, confirmed by the fractal detector.
- **Break of Structure (BOS)**: a bar closes above the most recent
  confirmed swing high (mirror for shorts). The BOS level is the
  broken swing high.
- **Retest entry**: once a BOS is armed, we wait for price to pull
  back to the broken level (within ``retest_tolerance_atr`` × ATR)
  AND print a bullish rejection candle. Entry on that close.
- **Structural SL**: just below the last HL minus an ATR buffer.
  This is "logical" in the user's sense — the trade is wrong if
  the market makes a lower low.
- **CHoCH (Change of Character) invalidation**: if price breaks
  below the last HL *before* we enter, the setup is dead.
- **Two-leg execution**: TP1 at 1R with break-even on the runner;
  TP2 stretched (3R or more). Winners are allowed to run.

Optional session filter (plan v3 open-item; London+NY default).

Prepare hook caches ATR and a SwingSeries over the full frame.
All strictly causal — SwingSeries.tail respects the look-ahead
contract automatically (swings at iloc i are only visible at
bar i + k).
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Any

import numpy as np
import pandas as pd

from ..indicators import atr
from ..indicators.swings import SwingPoint, SwingSeries
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class BosRetestScalper(BaseStrategy):
    name = "bos_retest_scalper"

    def __init__(
        self,
        swing_lookback: int = 10,
        min_legs: int = 2,
        atr_period: int = 14,
        retest_tolerance_atr: float = 0.5,
        sl_atr_buffer: float = 0.3,
        tp1_rr: float = 1.0,
        tp2_rr: float = 3.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 5,
        setup_ttl_bars: int = 60,
        session: str = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            swing_lookback=swing_lookback,
            min_legs=min_legs,
            atr_period=atr_period,
            retest_tolerance_atr=retest_tolerance_atr,
            sl_atr_buffer=sl_atr_buffer,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            setup_ttl_bars=setup_ttl_bars,
            session=session,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(swing_lookback * 4, atr_period * 3, 100)
        # Caches
        self._atr: pd.Series | None = None
        self._swings: SwingSeries | None = None
        # Armed setups (one per side).
        self._long_setup: dict[str, Any] | None = None
        self._short_setup: dict[str, Any] | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        self._swings = SwingSeries(df, lookback=p["swing_lookback"])

    # ------------------------------------------------------------------
    def _recent_structural_trend(self, n: int) -> tuple[
        str,
        list[SwingPoint],
        list[SwingPoint],
    ]:
        """Classify trend from the last few *confirmed* swings.

        Returns (state, highs, lows) where state is 'up' | 'down' |
        'range'. Confirmation cutoff = n - k (no lookahead).
        """
        p = self.params
        k = p["swing_lookback"] // 2
        min_legs = int(p["min_legs"])
        assert self._swings is not None
        swings = self._swings.tail(
            end_iloc_exclusive=max(0, n - k),
            max_count=min_legs * 2 + 4,
        )
        highs = [s for s in swings if s.kind == "high"][-min_legs:]
        lows = [s for s in swings if s.kind == "low"][-min_legs:]
        if len(highs) < min_legs or len(lows) < min_legs:
            return "range", highs, lows
        rising_highs = all(highs[i].price < highs[i + 1].price for i in range(len(highs) - 1))
        rising_lows = all(lows[i].price < lows[i + 1].price for i in range(len(lows) - 1))
        falling_highs = all(highs[i].price > highs[i + 1].price for i in range(len(highs) - 1))
        falling_lows = all(lows[i].price > lows[i + 1].price for i in range(len(lows) - 1))
        if rising_highs and rising_lows:
            return "up", highs, lows
        if falling_highs and falling_lows:
            return "down", highs, lows
        return "range", highs, lows

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
            SignalLeg(weight=w1, take_profit=float(tp1),
                      move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    # ------------------------------------------------------------------
    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if self._atr is None or self._swings is None:
            return None

        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        # Session gate.
        ts = history.index[-1]
        t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
        if not check_session(t, p["session"]):
            return None

        # ---- Evaluate structural trend & BOS arming ----
        state, highs, lows = self._recent_structural_trend(n)
        last = history.iloc[-1]
        prev = history.iloc[-2]
        c = float(last["close"]); o = float(last["open"])
        hi = float(last["high"]); lo = float(last["low"])
        body = abs(c - o)
        upper_wick = hi - max(c, o)
        lower_wick = min(c, o) - lo

        # 1) Arm BOS setups on the bar AFTER the break.
        if state == "up" and self._long_setup is None:
            last_hh = highs[-1].price
            last_hl = lows[-1].price
            # Prior bar closed above last HH → BOS.
            if float(prev["close"]) > last_hh and last_hl < last_hh:
                self._long_setup = {
                    "bos_level": float(last_hh),
                    "last_hl": float(last_hl),
                    "atr_at_bos": atr_val,
                    "bars_alive": 0,
                }
        elif state == "down" and self._short_setup is None:
            last_ll = lows[-1].price
            last_lh = highs[-1].price
            if float(prev["close"]) < last_ll and last_lh > last_ll:
                self._short_setup = {
                    "bos_level": float(last_ll),
                    "last_lh": float(last_lh),
                    "atr_at_bos": atr_val,
                    "bars_alive": 0,
                }

        # 2) Age + CHoCH invalidation.
        if self._long_setup is not None:
            self._long_setup["bars_alive"] += 1
            # CHoCH: price breaks below the last HL before we entered.
            if float(last["low"]) < self._long_setup["last_hl"]:
                self._long_setup = None
            elif self._long_setup["bars_alive"] > p["setup_ttl_bars"]:
                self._long_setup = None
        if self._short_setup is not None:
            self._short_setup["bars_alive"] += 1
            if float(last["high"]) > self._short_setup["last_lh"]:
                self._short_setup = None
            elif self._short_setup["bars_alive"] > p["setup_ttl_bars"]:
                self._short_setup = None

        # Cooldown after a signal.
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        # 3) Retest entry.
        if self._long_setup is not None:
            bos = self._long_setup["bos_level"]
            tol = p["retest_tolerance_atr"] * self._long_setup["atr_at_bos"]
            in_retest = bos - tol <= lo <= bos + tol
            bullish_rej = (
                c > o
                and lower_wick >= body
                and c > float(prev["close"])
                and c > bos  # reclaimed above the broken high
            )
            if in_retest and bullish_rej:
                entry = c
                sl = self._long_setup["last_hl"] - p["sl_atr_buffer"] * self._long_setup["atr_at_bos"]
                risk = entry - sl
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.BUY, entry, sl, risk,
                        reason=f"BOS-retest long bos={bos:.2f} hl={self._long_setup['last_hl']:.2f}",
                    )
                    self._long_setup = None
                    return sig

        if self._short_setup is not None:
            bos = self._short_setup["bos_level"]
            tol = p["retest_tolerance_atr"] * self._short_setup["atr_at_bos"]
            in_retest = bos - tol <= hi <= bos + tol
            bearish_rej = (
                c < o
                and upper_wick >= body
                and c < float(prev["close"])
                and c < bos
            )
            if in_retest and bearish_rej:
                entry = c
                sl = self._short_setup["last_lh"] + p["sl_atr_buffer"] * self._short_setup["atr_at_bos"]
                risk = sl - entry
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.SELL, entry, sl, risk,
                        reason=f"BOS-retest short bos={bos:.2f} lh={self._short_setup['last_lh']:.2f}",
                    )
                    self._short_setup = None
                    return sig

        return None
