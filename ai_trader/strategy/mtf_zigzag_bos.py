"""Multi-timeframe ZigZag-bias BOS-retest scalper.

User direction (2026-04-25): M1 alone is too noisy for structural
reasoning. Combine with higher-TF ZigZag for trend bias.

Recipe:

1. **HTF bias** (default M5): build a ZigZagSeries on the resampled
   higher TF. Bias is UP when the last two confirmed pivots are
   HH+HL (rising), DOWN when LH+LL (falling), NEUTRAL otherwise.
   We trade only WITH the bias.
2. **HTF level** (the most recent confirmed HTF swing high for
   longs / low for shorts): this is the structural reference.
3. **M1 entry**: on the M1 timeframe, wait for price to retest the
   HTF level (within ``retest_atr`` * ATR_M1) AND print a M1
   rejection candle in the bias direction.
4. **SL**: just past the HTF level on the wrong side, +
   ``sl_atr_buffer`` * ATR_M1.
5. **TP**: 2-leg with TP1 at ``tp1_rr`` * risk and TP2 stretched.

The HTF bar is queried via ``MTFContext.last_closed`` which never
peeks into a still-forming HTF bar — strict no-lookahead.

Optional session filter (default 'always').
"""
from __future__ import annotations

from datetime import time as dtime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from ..indicators.zigzag import ZigZagPivot, ZigZagSeries
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class MTFZigZagBOS(BaseStrategy):
    name = "mtf_zigzag_bos"

    def __init__(
        self,
        htf: str = "M5",
        zigzag_threshold_atr: float = 1.5,
        zigzag_atr_period: int = 14,
        atr_period_m1: int = 14,
        retest_tolerance_atr: float = 0.5,
        sl_atr_buffer: float = 0.3,
        tp1_rr: float = 1.0,
        tp2_rr: float = 3.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 5,
        setup_ttl_bars: int = 60,
        session: str = "always",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            htf=htf,
            zigzag_threshold_atr=zigzag_threshold_atr,
            zigzag_atr_period=zigzag_atr_period,
            atr_period_m1=atr_period_m1,
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
        self.min_history = min_history or max(atr_period_m1 * 4, 200)
        self._atr_m1: pd.Series | None = None
        self._mtf: MTFContext | None = None
        self._zz: ZigZagSeries | None = None
        # Armed setups, one per direction.
        self._long_setup: Optional[dict] = None
        self._short_setup: Optional[dict] = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_m1 = atr(df, period=p["atr_period_m1"])
        # Build MTF context for the configured HTF.
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        # Build ZigZag on the HTF frame.
        htf_df = self._mtf.frame(p["htf"])
        if len(htf_df) < p["zigzag_atr_period"] * 2:
            self._zz = None
        else:
            self._zz = ZigZagSeries(
                htf_df.drop(columns=["close_time"], errors="ignore"),
                threshold_atr=p["zigzag_threshold_atr"],
                atr_period=p["zigzag_atr_period"],
            )

    def _htf_bias(self, htf_iloc_excl: int) -> tuple[str, list[ZigZagPivot]]:
        """Return ('up'|'down'|'flat', confirmed pivots tail).

        Uses ZigZag tail (last ~6 pivots) to classify; needs at
        least 2 highs and 2 lows in alternation.
        """
        if self._zz is None:
            return "flat", []
        pivots = self._zz.confirmed_up_to(htf_iloc_excl)
        # Take last 6 (3 highs, 3 lows).
        tail = pivots[-6:]
        highs = [p for p in tail if p.kind == "high"]
        lows = [p for p in tail if p.kind == "low"]
        if len(highs) < 2 or len(lows) < 2:
            return "flat", tail
        rising_h = highs[-1].price > highs[-2].price
        rising_l = lows[-1].price > lows[-2].price
        falling_h = highs[-1].price < highs[-2].price
        falling_l = lows[-1].price < lows[-2].price
        if rising_h and rising_l:
            return "up", tail
        if falling_h and falling_l:
            return "down", tail
        return "flat", tail

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

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if self._atr_m1 is None or self._mtf is None or self._zz is None:
            return None

        i = n - 1
        atr_m1 = float(self._atr_m1.iloc[i])
        if not np.isfinite(atr_m1) or atr_m1 <= 0:
            return None

        if p["session"] != "always":
            ts = history.index[-1]
            t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
            if not check_session(t, p["session"]):
                return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)

        # Find the most recent CLOSED HTF bar at this M1 time.
        htf_iloc = self._mtf.last_closed_idx(p["htf"], ts_dt)
        if htf_iloc is None:
            return None
        # Use confirmed ZigZag pivots up to (but not including) the
        # next-after-htf_iloc bar. Pivots whose confirm_iloc <=
        # htf_iloc are visible.
        bias, tail = self._htf_bias(htf_iloc + 1)

        last = history.iloc[-1]; prev = history.iloc[-2]
        c = float(last["close"]); o = float(last["open"])
        hi = float(last["high"]); lo = float(last["low"])
        body = abs(c - o)
        upper_wick = hi - max(c, o)
        lower_wick = min(c, o) - lo

        # Arm long setup: bias up, last confirmed pivot is a high
        # (the HTF made a new HH); the level is that pivot's price.
        if bias == "up":
            highs = [pp for pp in tail if pp.kind == "high"]
            lows = [pp for pp in tail if pp.kind == "low"]
            if highs and lows and self._long_setup is None:
                last_hh = highs[-1].price
                last_hl = lows[-1].price
                # Only arm if the prior bar's close exceeded the HH
                # (BOS confirmation on M1 of an HTF level).
                if float(prev["close"]) > last_hh > last_hl:
                    self._long_setup = {
                        "level": float(last_hh),
                        "invalidation": float(last_hl),
                        "atr": atr_m1,
                        "bars_alive": 0,
                    }
        elif bias == "down":
            highs = [pp for pp in tail if pp.kind == "high"]
            lows = [pp for pp in tail if pp.kind == "low"]
            if highs and lows and self._short_setup is None:
                last_lh = highs[-1].price
                last_ll = lows[-1].price
                if float(prev["close"]) < last_ll < last_lh:
                    self._short_setup = {
                        "level": float(last_ll),
                        "invalidation": float(last_lh),
                        "atr": atr_m1,
                        "bars_alive": 0,
                    }

        # Age + invalidate.
        if self._long_setup is not None:
            self._long_setup["bars_alive"] += 1
            if lo < self._long_setup["invalidation"]:
                self._long_setup = None
            elif self._long_setup["bars_alive"] > p["setup_ttl_bars"]:
                self._long_setup = None
        if self._short_setup is not None:
            self._short_setup["bars_alive"] += 1
            if hi > self._short_setup["invalidation"]:
                self._short_setup = None
            elif self._short_setup["bars_alive"] > p["setup_ttl_bars"]:
                self._short_setup = None

        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        # Entry: retest + rejection.
        if self._long_setup is not None:
            lvl = self._long_setup["level"]
            tol = p["retest_tolerance_atr"] * self._long_setup["atr"]
            in_retest = lvl - tol <= lo <= lvl + tol
            bullish = (
                c > o and lower_wick >= body
                and c > float(prev["close"]) and c > lvl
            )
            if in_retest and bullish:
                entry = c
                sl = self._long_setup["invalidation"] - p["sl_atr_buffer"] * self._long_setup["atr"]
                risk = entry - sl
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.BUY, entry, sl, risk,
                        reason=f"MTF({p['htf']}) up-bias BOS-retest long lvl={lvl:.2f}",
                    )
                    self._long_setup = None
                    return sig

        if self._short_setup is not None:
            lvl = self._short_setup["level"]
            tol = p["retest_tolerance_atr"] * self._short_setup["atr"]
            in_retest = lvl - tol <= hi <= lvl + tol
            bearish = (
                c < o and upper_wick >= body
                and c < float(prev["close"]) and c < lvl
            )
            if in_retest and bearish:
                entry = c
                sl = self._short_setup["invalidation"] + p["sl_atr_buffer"] * self._short_setup["atr"]
                risk = sl - entry
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.SELL, entry, sl, risk,
                        reason=f"MTF({p['htf']}) down-bias BOS-retest short lvl={lvl:.2f}",
                    )
                    self._short_setup = None
                    return sig

        return None
