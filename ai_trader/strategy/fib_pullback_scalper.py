"""Fib pullback scalper — user's discretionary recipe (iter9).

The user describes their approach as:
  "If highs/lows are rising it's a strong uptrend. I use Fibonacci
   to pull back to 38.2 or 50 before entering. I set a logical SL
   wide enough to endure some floating loss, then move it to break-
   even on the first profit lock-in. I take ~20 trades/day."

This implementation differs from the existing `trend_pullback_fib`
(M5 swing + tight ATR SL) and `trend_pullback_scalper` (M1 EMA
slope + fib + ATR SL) in three deliberate ways:

1. Trend detection on a HIGHER timeframe (M15 SwingSeries) but
   trades on M1. The user "looks at M15 to confirm trend, then
   uses M1 for the entry." We mirror that via MTFContext.

2. The SL is structurally wide: max(zone-low - sl_atr_buf*ATR,
   $sl_min_usd fixed). At $5 SL on 0.05 lot, that's the user's
   typical setup. Aggressive break-even on TP1 fill prevents the
   wide SL from being a real loss most of the time.

3. Trade-management is the focus: TP1 at a SHORT 0.5R (gets
   filled often, moves SL to BE), TP2 at a STRETCHED 4R+ (lets
   the runner ride a real trend). Aggressive BE on the runner is
   the mechanism that makes a wide SL safe.

Entry trigger:
  - M15 swing trend = UP (≥2 HH and ≥2 HL on M15 SwingSeries)
    OR DOWN (≥2 LH and ≥2 LL).
  - On M1, the most recent impulse leg's fib retracement zone
    (38.2-61.8% by default) is touched by the current bar's wick.
  - The current M1 bar is a rejection candle in trend direction
    (close back beyond zone with body+wick discipline).
  - Cooldown of `cooldown_bars` since the last signal.

Stop & TP:
  - SL = wider of (zone boundary ± sl_atr_buf*ATR) and a fixed
    $sl_min_usd absolute distance (default $3.0). Capped by
    `max_sl_atr` to bound disasters.
  - TP1 at +tp1_rr*R with `move_sl_to_on_fill = entry` (BE).
  - TP2 at +tp2_rr*R.
  - Both legs share initial SL.

This strategy is the closest mechanical translation of the
user's recipe; if it doesn't have edge here it tells us the
discretionary edge depends on trade-management decisions a
purely-mechanical bot cannot replicate.
"""
from __future__ import annotations

from datetime import time as dtime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from ..indicators.fib import fib_retracement_zone
from ..indicators.swings import SwingSeries
from ..indicators.trend import TrendState, classify_trend
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class FibPullbackScalper(BaseStrategy):
    name = "fib_pullback_scalper"

    def __init__(
        self,
        # MTF trend detection
        htf: str = "M15",
        htf_swing_lookback: int = 5,
        htf_min_trend_legs: int = 2,
        # Fib zone (on the M15 impulse leg)
        fib_entry_min: float = 0.382,
        fib_entry_max: float = 0.618,
        # SL: max of (zone boundary + ATR buffer) and a fixed $-amount
        atr_period: int = 14,
        sl_atr_buf: float = 0.5,
        sl_min_usd: float = 3.0,
        max_sl_atr: float = 4.0,
        # 2-leg execution
        tp1_rr: float = 0.5,
        tp2_rr: float = 4.0,
        leg1_weight: float = 0.6,
        cooldown_bars: int = 3,
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            htf=htf,
            htf_swing_lookback=htf_swing_lookback,
            htf_min_trend_legs=htf_min_trend_legs,
            fib_entry_min=fib_entry_min,
            fib_entry_max=fib_entry_max,
            atr_period=atr_period,
            sl_atr_buf=sl_atr_buf,
            sl_min_usd=sl_min_usd,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            session=session,
        )
        self.min_history = min_history or max(htf_swing_lookback * 30, atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._mtf: MTFContext | None = None
        self._htf_swings: SwingSeries | None = None
        self._last_signal_iloc: int = -(10**9)

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        self._htf_swings = SwingSeries(htf_df, lookback=int(p["htf_swing_lookback"]))

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        reason: str,
    ) -> Signal:
        p = self.params
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        tp2 = entry + p["tp2_rr"] * risk if side == SignalSide.BUY else entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(
                weight=w1,
                take_profit=float(tp1),
                move_sl_to_on_fill=float(entry),
                tag="tp1",
            ),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._mtf is None or self._htf_swings is None:
            return None
        if n - self._last_signal_iloc < int(p["cooldown_bars"]):
            return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        sess = p.get("session")
        if sess and not check_session(ts_utc.time(), sess):
            return None

        # HTF trend on the most recent fully-closed M15 bar.
        htf = p["htf"]
        htf_pos = self._mtf.last_closed_idx(htf, ts_utc)
        if htf_pos is None or htf_pos < 2 * int(p["htf_min_trend_legs"]) + 2:
            return None
        # Read swings up to and including htf_pos. SwingSeries.tail
        # already trims by its internal confirmation lag.
        needed = max(2 * int(p["htf_min_trend_legs"]) + 2, 6)
        # SwingSeries confirms a swing `lookback` bars after the extreme.
        # The safe end-iloc-exclusive on the HTF series is htf_pos + 1
        # (fully-closed bar at htf_pos visible).
        htf_swings = self._htf_swings.tail(
            end_iloc_exclusive=htf_pos + 1,
            max_count=needed,
        )
        if len(htf_swings) < 2 * int(p["htf_min_trend_legs"]):
            return None

        trend = classify_trend(htf_swings, min_legs=int(p["htf_min_trend_legs"]))
        if trend.state == TrendState.RANGE:
            return None
        if trend.impulse_start is None or trend.impulse_end is None:
            return None

        zone = fib_retracement_zone(
            impulse_low=trend.impulse_start.price,
            impulse_high=trend.impulse_end.price,
            level_min=float(p["fib_entry_min"]),
            level_max=float(p["fib_entry_max"]),
        )

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last
        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        in_zone = (zone.low <= float(last["low"]) <= zone.high) or (zone.low <= float(last["high"]) <= zone.high)
        if not in_zone:
            return None

        body = abs(float(last["close"]) - float(last["open"]))
        upper_wick = float(last["high"]) - max(float(last["close"]), float(last["open"]))
        lower_wick = min(float(last["close"]), float(last["open"])) - float(last["low"])

        if trend.state == TrendState.UP:
            bullish = (
                float(last["close"]) > float(last["open"])
                and lower_wick >= body * 0.8
                and float(last["close"]) > float(prev["close"])
            )
            if not bullish:
                return None
            entry = float(last["close"])
            structural_sl = zone.low - float(p["sl_atr_buf"]) * atr_val
            min_sl = entry - float(p["sl_min_usd"])
            sl = min(structural_sl, min_sl)
            # Cap SL by max_sl_atr ATR away from entry.
            cap_sl = entry - float(p["max_sl_atr"]) * atr_val
            sl = max(sl, cap_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk,
                reason=f"htf-up fib pullback zone=[{zone.low:.2f},{zone.high:.2f}] sl_dist={risk:.2f}",
            )

        if trend.state == TrendState.DOWN:
            bearish = (
                float(last["close"]) < float(last["open"])
                and upper_wick >= body * 0.8
                and float(last["close"]) < float(prev["close"])
            )
            if not bearish:
                return None
            entry = float(last["close"])
            structural_sl = zone.high + float(p["sl_atr_buf"]) * atr_val
            min_sl = entry + float(p["sl_min_usd"])
            sl = max(structural_sl, min_sl)
            cap_sl = entry + float(p["max_sl_atr"]) * atr_val
            sl = min(sl, cap_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk,
                reason=f"htf-down fib pullback zone=[{zone.low:.2f},{zone.high:.2f}] sl_dist={risk:.2f}",
            )

        return None
