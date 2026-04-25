"""Daily pivot-point bounce scalper for XAUUSD.

The classic floor-trader pivot levels:
  P  = (PrevH + PrevL + PrevC) / 3
  R1 = 2P - PrevL    S1 = 2P - PrevH
  R2 = P + (PrevH - PrevL)    S2 = P - (PrevH - PrevL)

Institutional desks publish & defend these levels; retail price
action often respects them as bounce targets. The mechanical
edge: when price touches a pivot level, MOST of the time it
reacts (≥1R move) before continuing through.

Strategy:
  - Compute P, S1, R1, S2, R2 from the PREVIOUS UTC trading day's
    OHLC (causal — uses only fully-closed prior days).
  - When the current M1 bar's wick touches S1/R1/S2/R2 + ATR buffer
    AND closes back inside (rejection), enter the bounce.
  - Long bounce on S1/S2; short bounce on R1/R2.
  - SL: a fixed ATR-buffer beyond the pivot.
  - 2-leg: TP1 = midway to next pivot or +1R, TP2 = next pivot or
    +2R (whichever is closer = adaptive).
  - Cooldown to prevent the same wick from firing repeatedly.

Why uncorrelated with existing strategies:
  - Reference levels are EXTERNAL (yesterday's OHLC), not a
    rolling-window feature. Different population of trades than
    fib pullbacks (impulse-based) or Asian-range sweeps
    (session-based).
"""
from __future__ import annotations

from datetime import timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class PivotBounce(BaseStrategy):
    name = "pivot_bounce"

    def __init__(
        self,
        atr_period: int = 14,
        touch_atr_buf: float = 0.10,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.0,
        tp1_rr: float = 0.8,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 30,
        session: str | None = "london_or_ny",
        use_s2r2: bool = True,
        max_trades_per_day: int = 4,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period,
            touch_atr_buf=touch_atr_buf,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            session=session,
            use_s2r2=use_s2r2,
            max_trades_per_day=max_trades_per_day,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        # Per-bar pivot levels precomputed in prepare().
        self._pivots: pd.DataFrame | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0
        # Pivots already touched today, by side+level (avoid re-firing on the same touch).
        self._touched: set[str] = set()

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])

        # Daily OHLC — using UTC calendar days. groupby on the
        # tz-aware index then forward-fill the *previous* day's
        # OHLC onto each bar.
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        df_utc = df.copy()
        df_utc.index = idx
        days = idx.normalize()
        agg = df_utc.groupby(days).agg(
            day_open=("open", "first"),
            day_high=("high", "max"),
            day_low=("low", "min"),
            day_close=("close", "last"),
        )
        # Shift by one day so each row has the PRIOR day's OHLC
        # (causal — no peek at today's close).
        prev = agg.shift(1)
        prev["P"] = (prev["day_high"] + prev["day_low"] + prev["day_close"]) / 3.0
        prev["R1"] = 2 * prev["P"] - prev["day_low"]
        prev["S1"] = 2 * prev["P"] - prev["day_high"]
        prev["R2"] = prev["P"] + (prev["day_high"] - prev["day_low"])
        prev["S2"] = prev["P"] - (prev["day_high"] - prev["day_low"])
        # Map back onto each M1 bar via the bar's day-key.
        per_bar = prev.loc[days, ["P", "R1", "S1", "R2", "S2"]].set_axis(idx)
        self._pivots = per_bar

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, reason: str,
    ) -> Signal:
        p = self.params
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        tp2 = entry + p["tp2_rr"] * risk if side == SignalSide.BUY else entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._pivots is None:
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
            self._touched = set()
        if self._day_trades >= int(p["max_trades_per_day"]):
            return None

        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        last = history.iloc[-1]
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        o = float(last["open"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        pv = self._pivots.iloc[i]
        if pv.isna().any():
            return None

        touch_buf = float(p["touch_atr_buf"]) * atr_val
        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val

        levels_buy = [("S1", float(pv["S1"]))]
        levels_sell = [("R1", float(pv["R1"]))]
        if p["use_s2r2"]:
            levels_buy.append(("S2", float(pv["S2"])))
            levels_sell.append(("R2", float(pv["R2"])))

        # Long bounce: low touched the support (or pierced) AND
        # close > support, with bullish rejection wick.
        for name, lvl in levels_buy:
            key = f"BUY-{name}"
            if key in self._touched:
                continue
            if l <= lvl + touch_buf and c > lvl:
                bullish = c > o and lower_wick >= body * 0.6
                if not bullish:
                    continue
                entry = c
                structural_sl = l - sl_buf
                cap_sl = entry - max_sl
                sl = max(structural_sl, cap_sl)
                risk = entry - sl
                if risk <= 0:
                    continue
                self._day_trades += 1
                self._touched.add(key)
                self._last_signal_iloc = n
                return self._build_signal(
                    SignalSide.BUY, entry, sl, risk,
                    reason=f"pivot-bounce long @{name}={lvl:.2f}",
                )

        for name, lvl in levels_sell:
            key = f"SELL-{name}"
            if key in self._touched:
                continue
            if h >= lvl - touch_buf and c < lvl:
                bearish = c < o and upper_wick >= body * 0.6
                if not bearish:
                    continue
                entry = c
                structural_sl = h + sl_buf
                cap_sl = entry + max_sl
                sl = min(structural_sl, cap_sl)
                risk = sl - entry
                if risk <= 0:
                    continue
                self._day_trades += 1
                self._touched.add(key)
                self._last_signal_iloc = n
                return self._build_signal(
                    SignalSide.SELL, entry, sl, risk,
                    reason=f"pivot-bounce short @{name}={lvl:.2f}",
                )

        return None
