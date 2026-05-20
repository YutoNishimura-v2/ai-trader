"""Wave-3–centric MTF scalper with ZigZag structure and RCI confluence.

Mechanical translation of the user's discretionary rules:

1. **HTF environment (Daily / H4 / H1 family — configurable):** ZigZag
   pivots must show *both* HH+HL (uptrend) or LH+LL (downtrend). If
   only one side updates, bias is flat — no trend trades.

2. **Elliott-style gating (approximate, causal):**
   - **Wave 5 / distribution trap:** after a run of higher highs, the
     first time the *latest* confirmed swing high is **not** the
     highest high among recent swing highs in the HTF tail, we treat
     the move as post‑peak (lower‑high / distribution) and **stop
     initiating** new trades until bias resets to flat.
   - **Wave 1 vs 3:** optional ``early_trend_bars`` caps entries to the
     first N M1 bars after bias leaves flat (proxy for "Wave 1 only").
     ``late_trend_only`` inverts that (proxy for "Wave 3 / extension
     only"). Default trades both except when post‑peak block fires.

3. **LTF execution (M1):** By default ZigZag micro‑structure must match
   HTF bias; ``m1_bias_mode=not_opposed`` allows M1 to be flat while HTF
   trends (pullback / better‑price proxy). Optional ``rci_gate_enabled`` /
   ``sr_gate_enabled`` disable the RCI or pivot‑distance filters for
   ablations. Price must interact with the 38.2–61.8% retracement band of
   the active M1 impulse leg, print a rejection candle, pass optional
   **RCI reversal readiness**, and sit near a recent HTF pivot (S/R
   confluence).

4. **Risk / invalidation:** initial SL is the **tighter** of (a) just
   beyond the M1 structural pivot beyond the entry‑bar extreme
   (pattern‑failure proxy) and (b) the ``sl_fib_from_extreme`` Fib
   retracement from the impulse extreme back toward the origin (default
   0.236 per the user's "shallow invalidation" discipline — tune via
   YAML). This encodes "cut without attachment" when the micro thesis
   breaks.

Strictly causal: HTF pivots use ``confirmed_up_to(htf_iloc + 1)``; M1
pivots use the last fully closed M1 index only.
"""
from __future__ import annotations

from datetime import time as dtime, timezone

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from ..indicators.fib import fib_retracement_zone
from ..indicators.rci import rci
from ..indicators.zigzag import ZigZagPivot, ZigZagSeries
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _htf_zigzag_bias(pivots: list[ZigZagPivot]) -> str:
    tail = pivots[-6:]
    highs = [p for p in tail if p.kind == "high"]
    lows = [p for p in tail if p.kind == "low"]
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


def _w5_distribution_block(pivots: list[ZigZagPivot], bias: str) -> bool:
    """True when the latest swing high is a lower high vs a prior peak."""
    tail = pivots[-10:]
    highs = [p for p in tail if p.kind == "high"]
    if bias == "up":
        if len(highs) < 2:
            return False
        peak_prior = max(h.price for h in highs[:-1])
        return highs[-1].price < peak_prior
    if bias == "down":
        lows = [p for p in tail if p.kind == "low"]
        if len(lows) < 2:
            return False
        trough_prior = min(lo.price for lo in lows[:-1])
        return lows[-1].price > trough_prior
    return False


def _m1_impulse_for_fib(
    pivots: list[ZigZagPivot], bias: str,
) -> tuple[float, float] | None:
    """Return (impulse_low, impulse_high) for the active M1 leg."""
    tail = pivots[-8:]
    highs = [p for p in tail if p.kind == "high"]
    lows = [p for p in tail if p.kind == "low"]
    if bias == "up":
        if not highs or not lows:
            return None
        imp_hi = highs[-1].price
        # last low before that high in tail ordering
        imp_lo = None
        for p in reversed(tail):
            if p.kind == "low" and p.iloc < highs[-1].iloc:
                imp_lo = p.price
                break
        if imp_lo is None:
            imp_lo = lows[-1].price
        if imp_hi <= imp_lo:
            return None
        return float(imp_lo), float(imp_hi)
    if bias == "down":
        if not highs or not lows:
            return None
        imp_lo = lows[-1].price
        imp_hi = None
        for p in reversed(tail):
            if p.kind == "high" and p.iloc < lows[-1].iloc:
                imp_hi = p.price
                break
        if imp_hi is None:
            imp_hi = highs[-1].price
        if imp_hi <= imp_lo:
            return None
        return float(imp_lo), float(imp_hi)
    return None


def _fib_invalidation_sl(
    impulse_lo: float, impulse_hi: float, *, bias: str, level: float,
) -> float:
    """Single price beyond which the micro impulse thesis is void."""
    if not (0.0 < level < 1.0):
        raise ValueError("level must be in (0,1)")
    span = impulse_hi - impulse_lo
    if bias == "up":
        # shallow retracement from the top toward origin
        return float(impulse_hi - level * span)
    return float(impulse_lo + level * span)


@register_strategy
class WaveStructureMTF(BaseStrategy):
    name = "wave_structure_mtf"

    def __init__(
        self,
        htf: str = "H1",
        htf_zigzag_threshold_atr: float = 1.25,
        htf_zigzag_atr_period: int = 14,
        m1_zigzag_threshold_atr: float = 0.85,
        m1_zigzag_atr_period: int = 14,
        atr_period_m1: int = 14,
        fib_entry_min: float = 0.382,
        fib_entry_max: float = 0.500,
        sl_fib_from_extreme: float = 0.236,
        sl_atr_buffer: float = 0.15,
        sr_touch_atr: float = 0.75,
        rci_period: int = 14,
        rci_long_min_prev: float = -15.0,
        rci_short_max_prev: float = 15.0,
        tp1_rr: float = 0.85,
        tp2_rr: float = 2.6,
        leg1_weight: float = 0.55,
        cooldown_bars: int = 8,
        session: str = "london_or_ny",
        early_trend_bars: int | None = None,
        late_trend_only: bool = False,
        rci_gate_enabled: bool = True,
        sr_gate_enabled: bool = True,
        m1_bias_mode: str = "match",
        min_history: int | None = None,
    ) -> None:
        mbm = str(m1_bias_mode).lower().strip()
        if mbm not in ("match", "not_opposed"):
            raise ValueError("m1_bias_mode must be 'match' or 'not_opposed'")
        super().__init__(
            htf=htf,
            htf_zigzag_threshold_atr=htf_zigzag_threshold_atr,
            htf_zigzag_atr_period=htf_zigzag_atr_period,
            m1_zigzag_threshold_atr=m1_zigzag_threshold_atr,
            m1_zigzag_atr_period=m1_zigzag_atr_period,
            atr_period_m1=atr_period_m1,
            fib_entry_min=fib_entry_min,
            fib_entry_max=fib_entry_max,
            sl_fib_from_extreme=sl_fib_from_extreme,
            sl_atr_buffer=sl_atr_buffer,
            sr_touch_atr=sr_touch_atr,
            rci_period=rci_period,
            rci_long_min_prev=rci_long_min_prev,
            rci_short_max_prev=rci_short_max_prev,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            session=session,
            early_trend_bars=early_trend_bars,
            late_trend_only=late_trend_only,
            rci_gate_enabled=bool(rci_gate_enabled),
            sr_gate_enabled=bool(sr_gate_enabled),
            m1_bias_mode=mbm,
        )
        self.min_history = min_history or max(500, atr_period_m1 * 40)
        self._last_signal_iloc: int = -(10**9)
        self._atr_m1: pd.Series | None = None
        self._rci: pd.Series | None = None
        self._mtf: MTFContext | None = None
        self._zz_htf: ZigZagSeries | None = None
        self._zz_m1: ZigZagSeries | None = None
        self._prev_bias: str = "flat"
        self._bias_flip_m1_iloc: int | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_m1 = atr(df, period=int(p["atr_period_m1"]))
        self._rci = rci(df["close"], period=int(p["rci_period"]))
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        if len(htf_df) >= int(p["htf_zigzag_atr_period"]) * 2:
            self._zz_htf = ZigZagSeries(
                htf_df.drop(columns=["close_time"], errors="ignore"),
                threshold_atr=float(p["htf_zigzag_threshold_atr"]),
                atr_period=int(p["htf_zigzag_atr_period"]),
            )
        else:
            self._zz_htf = None
        self._zz_m1 = ZigZagSeries(
            df,
            threshold_atr=float(p["m1_zigzag_threshold_atr"]),
            atr_period=int(p["m1_zigzag_atr_period"]),
        )

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, reason: str,
    ) -> Signal:
        p = self.params
        if side == SignalSide.BUY:
            tp1 = entry + float(p["tp1_rr"]) * risk
            tp2 = entry + float(p["tp2_rr"]) * risk
        else:
            tp1 = entry - float(p["tp1_rr"]) * risk
            tp2 = entry - float(p["tp2_rr"]) * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1),
                      move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def _nearest_htf_sr_distance(
        self, pivots: list[ZigZagPivot], bias: str, price: float,
    ) -> float:
        tail = pivots[-8:]
        if bias == "up":
            lows = [p.price for p in tail if p.kind == "low"]
            highs = [p.price for p in tail if p.kind == "high"]
            refs = lows + highs
        else:
            lows = [p.price for p in tail if p.kind == "low"]
            highs = [p.price for p in tail if p.kind == "high"]
            refs = highs + lows
        if not refs:
            return 0.0
        return min(abs(price - r) for r in refs)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if (
            self._atr_m1 is None or self._rci is None or self._mtf is None
            or self._zz_m1 is None or self._zz_htf is None
        ):
            return None

        i = n - 1
        atr_m1 = float(self._atr_m1.iloc[i])
        if not np.isfinite(atr_m1) or atr_m1 <= 0:
            return None

        if p["session"] != "always":
            ts0 = history.index[-1]
            t = ts0.time() if hasattr(ts0, "time") else dtime(0, 0)
            if not check_session(t, p["session"]):
                return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)

        htf_iloc = self._mtf.last_closed_idx(p["htf"], ts_dt)
        if htf_iloc is None:
            return None

        htf_pivots = self._zz_htf.confirmed_up_to(htf_iloc + 1)
        bias = _htf_zigzag_bias(htf_pivots)
        if bias == "flat":
            self._prev_bias = "flat"
            self._bias_flip_m1_iloc = None
            return None

        if self._prev_bias == "flat" and bias != "flat":
            self._bias_flip_m1_iloc = i
        self._prev_bias = bias

        if _w5_distribution_block(htf_pivots, bias):
            return None

        early = p.get("early_trend_bars")
        late_only = bool(p.get("late_trend_only"))
        if early is not None and self._bias_flip_m1_iloc is not None:
            bars_since = i - int(self._bias_flip_m1_iloc)
            if late_only:
                if bars_since < int(early):
                    return None
            else:
                if bars_since > int(early):
                    return None

        m1_pivots = self._zz_m1.confirmed_up_to(i + 1)
        m1_bias = _htf_zigzag_bias(m1_pivots)
        mbm = str(p.get("m1_bias_mode") or "match").lower().strip()
        if mbm == "match":
            if m1_bias != bias:
                return None
        elif mbm == "not_opposed":
            if bias == "up" and m1_bias == "down":
                return None
            if bias == "down" and m1_bias == "up":
                return None
        else:
            return None

        impulse = _m1_impulse_for_fib(m1_pivots, bias)
        if impulse is None:
            return None
        imp_lo, imp_hi = impulse
        zone = fib_retracement_zone(
            imp_lo, imp_hi,
            level_min=float(p["fib_entry_min"]),
            level_max=float(p["fib_entry_max"]),
        )

        last = history.iloc[-1]
        prev = history.iloc[-2]
        hi = float(last["high"])
        lo = float(last["low"])
        c = float(last["close"])
        o = float(last["open"])
        body = abs(c - o)
        upper_wick = hi - max(c, o)
        lower_wick = min(c, o) - lo

        in_zone = (zone.low <= lo <= zone.high) or (zone.low <= hi <= zone.high)
        if not in_zone:
            return None

        rci_now = float(self._rci.iloc[i])
        rci_prev = float(self._rci.iloc[i - 1])
        if not np.isfinite(rci_now) or not np.isfinite(rci_prev):
            return None

        sr_tol = float(p["sr_touch_atr"]) * atr_m1
        sr_on = bool(p.get("sr_gate_enabled", True))
        rci_on = bool(p.get("rci_gate_enabled", True))
        if n - self._last_signal_iloc < int(p["cooldown_bars"]):
            return None

        fib_sl_px = _fib_invalidation_sl(
            imp_lo, imp_hi, bias=bias, level=float(p["sl_fib_from_extreme"]),
        )
        buf = float(p["sl_atr_buffer"]) * atr_m1

        if bias == "up":
            if sr_on and self._nearest_htf_sr_distance(htf_pivots, bias, c) > sr_tol:
                return None
            if rci_on and not (rci_prev <= float(p["rci_long_min_prev"]) and rci_now > rci_prev):
                return None
            bullish = c > o and lower_wick >= body * 0.75 and c > float(prev["close"])
            if not bullish:
                return None
            entry = c
            pattern_sl = min(lo, float(prev["low"])) - buf
            fib_cand = fib_sl_px - buf
            sl_cands = [pattern_sl]
            if fib_cand < entry:
                sl_cands.append(fib_cand)
            sl = max(sl_cands)
            risk = entry - sl
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk,
                reason=f"wave_mtf {p['htf']} up | RCI rev | fib[{zone.low:.1f},{zone.high:.1f}]",
            )

        if bias == "down":
            if sr_on and self._nearest_htf_sr_distance(htf_pivots, bias, c) > sr_tol:
                return None
            if rci_on and not (rci_prev >= float(p["rci_short_max_prev"]) and rci_now < rci_prev):
                return None
            bearish = c < o and upper_wick >= body * 0.75 and c < float(prev["close"])
            if not bearish:
                return None
            entry = c
            pattern_sl = max(hi, float(prev["high"])) + buf
            fib_cand = fib_sl_px + buf
            sl_cands = [pattern_sl]
            if fib_cand > entry:
                sl_cands.append(fib_cand)
            sl = min(sl_cands)
            risk = sl - entry
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk,
                reason=f"wave_mtf {p['htf']} down | RCI rev | fib[{zone.low:.1f},{zone.high:.1f}]",
            )

        return None
