"""ZigZag (ATR-threshold) on M5 + M1 Fibonacci pullback alignment.

User recipe (2026):

1. **5m ZigZag** — same causal machinery as ``ZigZagSeries`` (ATR ×
   ``threshold_atr`` reversal filter). TradingView *ZigZag++* uses
   similar adaptive depth; we document parity as *threshold-style*
   ZigZag, not a Pine clone.

2. **Trend** — last two swing highs and two swing lows from confirmed
   pivots: **HH+HL** ⇒ uptrend, **LH+LL** ⇒ downtrend (same structural
   rule as ``mtf_zigzag_bos``).

3. **5m pullback** — Fib retracement of the active leg (last swing low →
   last swing high in uptrend; mirror in downtrend) at **38.2%, 50%,
   61.8%**. Price is *near* a level if within ``fib_touch_atr × ATR(M5)``
   of the union band around those three prices.

4. **1m confirmation** — same ZigZag + trend + fib cluster on **M1**;
   bias must **match** M5. Entry when the current M1 bar overlaps the
   M1 cluster and prints a rejection candle (same style as
   ``fib_pullback_scalper``).

Strictly **no lookahead**: M5 pivots use ``confirmed_up_to(htf_idx+1)``
where ``htf_idx`` is ``MTFContext.last_closed_idx("M5", t)``. M1 pivots
use ``confirmed_up_to(n)`` on the full M1 series at the current bar
index.
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from ..indicators.zigzag import ZigZagPivot, ZigZagSeries
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session

_FIB_RATIOS = (0.382, 0.5, 0.618)


def _fib_retrace_prices(impulse_lo: float, impulse_hi: float, *, up_leg: bool) -> list[float]:
    """Absolute prices for 38.2 / 50 / 61.8% retracement of the impulse."""
    span = impulse_hi - impulse_lo
    if span <= 0 or not np.isfinite(span):
        return []
    out: list[float] = []
    for r in _FIB_RATIOS:
        if up_leg:
            # Retrace down from the high toward the low.
            out.append(impulse_hi - r * span)
        else:
            # Retrace up from the low toward the high.
            out.append(impulse_lo + r * span)
    return out


def _cluster_bounds(levels: list[float], tol: float) -> tuple[float, float] | None:
    if not levels or tol <= 0 or not np.isfinite(tol):
        return None
    xs = [x for x in levels if np.isfinite(x)]
    if not xs:
        return None
    lo = min(xs) - tol
    hi = max(xs) + tol
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _bar_overlaps_cluster(
    hi: float, lo: float, c_lo: float, c_hi: float,
) -> bool:
    return not (hi < c_lo or lo > c_hi)


def _zigzag_bias(pivots: list[ZigZagPivot]) -> str:
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "flat"
    rising_h = highs[-1].price > highs[-2].price
    rising_l = lows[-1].price > lows[-2].price
    falling_h = highs[-1].price < highs[-2].price
    falling_l = lows[-1].price < lows[-2].price
    if rising_h and rising_l:
        return "up"
    if falling_h and falling_l:
        return "down"
    return "flat"


def _impulse_endpoints(pivots: list[ZigZagPivot], bias: str) -> tuple[float, float] | None:
    """Return (impulse_low, impulse_high) for the active leg."""
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if not highs or not lows:
        return None
    if bias == "up":
        return float(lows[-1].price), float(highs[-1].price)
    if bias == "down":
        return float(lows[-1].price), float(highs[-1].price)
    return None


@register_strategy
class ZigZagFibMTF(BaseStrategy):
    name = "zigzag_fib_mtf"

    def __init__(
        self,
        htf: str = "M5",
        zigzag_threshold_atr: float = 1.5,
        zigzag_atr_period: int = 14,
        atr_period_m1: int = 14,
        fib_touch_atr: float = 0.35,
        sl_atr_buffer: float = 0.35,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.55,
        cooldown_bars: int = 8,
        session: str = "always",
        require_m1_bias_match: bool = True,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            htf=htf,
            zigzag_threshold_atr=zigzag_threshold_atr,
            zigzag_atr_period=zigzag_atr_period,
            atr_period_m1=atr_period_m1,
            fib_touch_atr=fib_touch_atr,
            sl_atr_buffer=sl_atr_buffer,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            session=session,
            require_m1_bias_match=require_m1_bias_match,
        )
        self._last_signal_iloc = -(10**9)
        self.min_history = min_history or max(300, zigzag_atr_period * 20, atr_period_m1 * 15)
        self._mtf: MTFContext | None = None
        self._zz_m5: ZigZagSeries | None = None
        self._zz_m1: ZigZagSeries | None = None
        self._atr_m1: pd.Series | None = None
        self._atr_m5: pd.Series | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"]).drop(columns=["close_time"], errors="ignore")
        if len(htf_df) >= p["zigzag_atr_period"] * 2:
            self._zz_m5 = ZigZagSeries(
                htf_df,
                threshold_atr=p["zigzag_threshold_atr"],
                atr_period=p["zigzag_atr_period"],
            )
        else:
            self._zz_m5 = None
        self._zz_m1 = ZigZagSeries(
            df,
            threshold_atr=p["zigzag_threshold_atr"],
            atr_period=p["zigzag_atr_period"],
        )
        self._atr_m1 = atr(df, period=p["atr_period_m1"])
        self._atr_m5 = atr(htf_df, period=p["zigzag_atr_period"])

    def _atr_m5_at(self, htf_idx: int) -> float | None:
        if self._atr_m5 is None:
            return None
        if htf_idx < 0 or htf_idx >= len(self._atr_m5):
            return None
        v = float(self._atr_m5.iloc[htf_idx])
        return v if np.isfinite(v) and v > 0 else None

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
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if self._mtf is None or self._zz_m5 is None or self._zz_m1 is None or self._atr_m1 is None:
            return None

        if n - self._last_signal_iloc < int(p["cooldown_bars"]):
            return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)

        if p["session"] != "always":
            if not check_session(ts_utc.time(), p["session"]):
                return None

        htf = p["htf"]
        htf_idx = self._mtf.last_closed_idx(htf, ts_utc)
        if htf_idx is None or htf_idx < 4:
            return None

        piv5 = self._zz_m5.confirmed_up_to(htf_idx + 1)
        bias5 = _zigzag_bias(piv5[-8:])
        if bias5 == "flat":
            return None
        end5 = _impulse_endpoints(piv5[-8:], bias5)
        if end5 is None:
            return None
        imp_lo5, imp_hi5 = end5
        up5 = bias5 == "up"
        levels5 = _fib_retrace_prices(imp_lo5, imp_hi5, up_leg=up5)
        atr5 = self._atr_m5_at(htf_idx)
        if atr5 is None:
            return None
        tol5 = float(p["fib_touch_atr"]) * atr5
        b5 = _cluster_bounds(levels5, tol5)
        if b5 is None:
            return None
        c_lo5, c_hi5 = b5
        h5 = self._mtf.last_closed(htf, ts_utc)
        if h5 is None:
            return None
        m5_touch = _bar_overlaps_cluster(
            float(h5["high"]), float(h5["low"]), c_lo5, c_hi5,
        )
        if not m5_touch:
            return None

        piv1 = self._zz_m1.confirmed_up_to(n)
        bias1 = _zigzag_bias(piv1[-10:])
        if p["require_m1_bias_match"] and bias1 != bias5:
            return None
        if bias1 == "flat":
            return None
        end1 = _impulse_endpoints(piv1[-10:], bias1)
        if end1 is None:
            return None
        imp_lo1, imp_hi1 = end1
        up1 = bias1 == "up"
        levels1 = _fib_retrace_prices(imp_lo1, imp_hi1, up_leg=up1)
        i = n - 1
        atr1 = float(self._atr_m1.iloc[i])
        if not np.isfinite(atr1) or atr1 <= 0:
            return None
        tol1 = float(p["fib_touch_atr"]) * atr1
        b1 = _cluster_bounds(levels1, tol1)
        if b1 is None:
            return None
        c_lo1, c_hi1 = b1

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last
        hi1 = float(last["high"])
        lo1 = float(last["low"])
        if not _bar_overlaps_cluster(hi1, lo1, c_lo1, c_hi1):
            return None

        body = abs(float(last["close"]) - float(last["open"]))
        upper_wick = hi1 - max(float(last["close"]), float(last["open"]))
        lower_wick = min(float(last["close"]), float(last["open"])) - lo1

        if bias5 == "up":
            bullish = (
                float(last["close"]) > float(last["open"])
                and lower_wick >= body * 0.75
                and float(last["close"]) > float(prev["close"])
            )
            if not bullish:
                return None
            entry = float(last["close"])
            sl = imp_lo1 - float(p["sl_atr_buffer"]) * atr1
            risk = entry - sl
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk,
                reason=f"ZZFib M5↑ M1↑ zone5=[{c_lo5:.2f},{c_hi5:.2f}] zone1=[{c_lo1:.2f},{c_hi1:.2f}]",
            )

        bearish = (
            float(last["close"]) < float(last["open"])
            and upper_wick >= body * 0.75
            and float(last["close"]) < float(prev["close"])
        )
        if not bearish:
            return None
        entry = float(last["close"])
        sl = imp_hi1 + float(p["sl_atr_buffer"]) * atr1
        risk = sl - entry
        if risk <= 0:
            return None
        self._last_signal_iloc = n
        return self._build_signal(
            SignalSide.SELL, entry, sl, risk,
            reason=f"ZZFib M5↓ M1↓ zone5=[{c_lo5:.2f},{c_hi5:.2f}] zone1=[{c_lo1:.2f},{c_hi1:.2f}]",
        )
