"""ICT/SMC order-block retest scalper.

ICT/SMC mechanical entry:
  1. Detect a Break of Market Structure (BOS): a closed bar takes
     out the most recent confirmed swing high (bullish BOS) or
     swing low (bearish BOS).
  2. Identify the Order Block (OB): the last opposite-direction
     candle BEFORE the BOS impulse. For a bullish BOS, the OB is
     the last bearish candle before the impulse leg.
  3. Wait for price to RETEST the OB zone (the OB candle's range).
  4. Enter on a rejection at the retest with bias-aligned candle.

This is fundamentally different from `bos_retest_scalper`:
  - bos_retest_scalper retests the BROKEN swing-high LEVEL (single price).
  - order_block_retest retests the LAST OPPOSITE CANDLE'S RANGE
    (a price ZONE, not a single level) — the institutional fingerprint.

Setup expires after `setup_ttl_bars` if not retested.

SL: opposite extreme of the OB candle + ATR buffer.
TP: 2-leg, TP1 at +1R + BE on runner, TP2 at the next structural high
    or +3R (whichever closer for safety).
"""
from __future__ import annotations

from datetime import time as dtime, timezone
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import atr
from ..indicators.swings import SwingPoint, SwingSeries
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@dataclass
class _OrderBlock:
    side: SignalSide          # signal direction this OB enables
    iloc: int                 # M1 bar index of the OB candle
    ob_high: float            # OB candle high
    ob_low: float             # OB candle low
    bos_iloc: int             # M1 bar index where BOS confirmed
    bos_level: float          # the swing level that was broken
    expires_iloc: int         # last bar this OB can be retested


@register_strategy
class OrderBlockRetest(BaseStrategy):
    name = "order_block_retest"

    def __init__(
        self,
        swing_lookback: int = 8,
        atr_period: int = 14,
        retest_tolerance_atr: float = 0.20,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.5,
        tp1_rr: float = 1.0,
        tp2_rr: float = 3.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 6,
        setup_ttl_bars: int = 60,
        max_trades_per_day: int = 5,
        min_displacement_atr: float = 1.0,   # require BOS impulse > N×ATR (filter wicky breaks)
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            swing_lookback=swing_lookback,
            atr_period=atr_period,
            retest_tolerance_atr=retest_tolerance_atr,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            setup_ttl_bars=setup_ttl_bars,
            max_trades_per_day=max_trades_per_day,
            min_displacement_atr=min_displacement_atr,
            session=session,
        )
        self.min_history = min_history or max(swing_lookback * 4, atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._swings_cache: SwingSeries | None = None
        self._active_obs: list[_OrderBlock] = []
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        self._swings_cache = SwingSeries(df, lookback=int(p["swing_lookback"]))

    def _detect_new_ob(self, history: pd.DataFrame, swings: list[SwingPoint], i: int, atr_val: float) -> _OrderBlock | None:
        """If bar i confirms a new BOS, identify the OB and return it.

        Bullish BOS: bar i closes above the most recent swing high (before i).
        Bearish BOS: bar i closes below the most recent swing low (before i).
        """
        p = self.params
        if not swings:
            return None
        last = history.iloc[i]
        c = float(last["close"])

        # Find most recent swing high & swing low STRICTLY BEFORE bar i.
        recent_high = None
        recent_low = None
        for s in reversed(swings):
            if s.iloc >= i:
                continue
            if s.kind == "high" and recent_high is None:
                recent_high = s
            if s.kind == "low" and recent_low is None:
                recent_low = s
            if recent_high is not None and recent_low is not None:
                break

        # Bullish BOS
        if recent_high is not None and c > recent_high.price:
            # Impulse displacement check
            disp = c - recent_high.price
            if disp < float(p["min_displacement_atr"]) * atr_val:
                return None
            # Find the last BEARISH (close < open) candle before bar i AND
            # at or after recent_high.iloc (so it's part of the impulse setup).
            ob_iloc = None
            for j in range(i - 1, max(recent_high.iloc - 1, 0), -1):
                bj = history.iloc[j]
                if float(bj["close"]) < float(bj["open"]):
                    ob_iloc = j
                    break
            if ob_iloc is None:
                return None
            ob_bar = history.iloc[ob_iloc]
            return _OrderBlock(
                side=SignalSide.BUY,
                iloc=ob_iloc,
                ob_high=float(ob_bar["high"]),
                ob_low=float(ob_bar["low"]),
                bos_iloc=i,
                bos_level=float(recent_high.price),
                expires_iloc=i + int(p["setup_ttl_bars"]),
            )

        # Bearish BOS
        if recent_low is not None and c < recent_low.price:
            disp = recent_low.price - c
            if disp < float(p["min_displacement_atr"]) * atr_val:
                return None
            ob_iloc = None
            for j in range(i - 1, max(recent_low.iloc - 1, 0), -1):
                bj = history.iloc[j]
                if float(bj["close"]) > float(bj["open"]):
                    ob_iloc = j
                    break
            if ob_iloc is None:
                return None
            ob_bar = history.iloc[ob_iloc]
            return _OrderBlock(
                side=SignalSide.SELL,
                iloc=ob_iloc,
                ob_high=float(ob_bar["high"]),
                ob_low=float(ob_bar["low"]),
                bos_iloc=i,
                bos_level=float(recent_low.price),
                expires_iloc=i + int(p["setup_ttl_bars"]),
            )
        return None

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._swings_cache is None:
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

        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key
            self._day_trades = 0
        if self._day_trades >= int(p["max_trades_per_day"]):
            return None

        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        # Pull recent confirmed swings (causal — confirm lag respected by SwingSeries).
        k = int(p["swing_lookback"]) // 2
        end = max(0, n - k)
        swings_list = self._swings_cache.tail(end_iloc_exclusive=end, max_count=20)

        # Step 1: detect new OB on this bar.
        new_ob = self._detect_new_ob(history, swings_list, i, atr_val)
        if new_ob is not None:
            self._active_obs.append(new_ob)

        # Step 2: prune expired OBs.
        self._active_obs = [ob for ob in self._active_obs if ob.expires_iloc >= i]

        # Step 3: check retest of any active OB.
        last = history.iloc[-1]
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        o = float(last["open"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        tol = float(p["retest_tolerance_atr"]) * atr_val
        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val

        for ob in list(self._active_obs):
            if ob.side == SignalSide.BUY:
                # Retest if low touched OB zone (with tolerance).
                if l <= ob.ob_high + tol and l >= ob.ob_low - tol:
                    bullish = c > o and lower_wick >= body * 0.5
                    if not bullish:
                        continue
                    entry = c
                    structural_sl = ob.ob_low - sl_buf
                    cap_sl = entry - max_sl
                    sl = max(structural_sl, cap_sl)
                    risk = entry - sl
                    if risk <= 0:
                        continue
                    tp1 = entry + float(p["tp1_rr"]) * risk
                    tp2 = entry + float(p["tp2_rr"]) * risk
                    w1 = float(p["leg1_weight"])
                    legs = (
                        SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                        SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
                    )
                    self._day_trades += 1
                    self._last_signal_iloc = n
                    self._active_obs.remove(ob)
                    return Signal(
                        side=SignalSide.BUY, entry=None, stop_loss=sl, legs=legs,
                        reason=f"OB-retest long zone=[{ob.ob_low:.2f},{ob.ob_high:.2f}]",
                    )
            else:  # SELL
                if h >= ob.ob_low - tol and h <= ob.ob_high + tol:
                    bearish = c < o and upper_wick >= body * 0.5
                    if not bearish:
                        continue
                    entry = c
                    structural_sl = ob.ob_high + sl_buf
                    cap_sl = entry + max_sl
                    sl = min(structural_sl, cap_sl)
                    risk = sl - entry
                    if risk <= 0:
                        continue
                    tp1 = entry - float(p["tp1_rr"]) * risk
                    tp2 = entry - float(p["tp2_rr"]) * risk
                    w1 = float(p["leg1_weight"])
                    legs = (
                        SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                        SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
                    )
                    self._day_trades += 1
                    self._last_signal_iloc = n
                    self._active_obs.remove(ob)
                    return Signal(
                        side=SignalSide.SELL, entry=None, stop_loss=sl, legs=legs,
                        reason=f"OB-retest short zone=[{ob.ob_low:.2f},{ob.ob_high:.2f}]",
                    )

        return None
