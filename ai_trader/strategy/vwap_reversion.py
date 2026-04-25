"""VWAP-deviation mean-reversion scalper.

Different from ``bb_scalper`` in three structurally meaningful
ways:

1. **Volume-weighted, not equal-weighted**: VWAP weights each
   tick by volume, so the centroid is where actual liquidity
   transacted — a real institutional benchmark.
2. **Session-anchored**: VWAP resets at 00:00 UTC each day. The
   first hour after reset uses small samples, so the strategy
   waits N bars before arming.
3. **HTF bias gate**: only fade extremes that are ALSO countertrend
   on H1 (don't fight a strong daily trend). Optional.

Entry:
- Long when bar low pierces VWAP - dev_mult * sigma AND a
  bullish rejection candle prints.
- Mirror for short.
- Optional HTF bias filter: skip longs when H1 EMA-fast is well
  below H1 EMA-slow, etc.

SL: just past the band; TP: back to VWAP (or R-multiple).
2-leg with break-even.

prepare() caches per-bar VWAP, the running squared-deviation
sum, and HTF context if enabled.
"""
from __future__ import annotations

from datetime import time as dtime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _session_vwap_and_dev(df: pd.DataFrame, warmup_bars: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Compute per-bar session VWAP and rolling stdev of price-from-VWAP.

    Session resets at 00:00 UTC each day. Returns two NaN-padded
    arrays of length len(df). First `warmup_bars` of each session
    are NaN.
    """
    n = len(df)
    if n == 0:
        return np.full(0, np.nan), np.full(0, np.nan)
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    days = idx.tz_convert("UTC").normalize().to_numpy()
    typical = ((df["high"] + df["low"] + df["close"]) / 3.0).to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    # Floors to prevent divide-by-zero (shouldn't happen on real data).
    vol = np.where(vol > 0, vol, 1e-9)

    cum_pv = np.zeros(n)
    cum_v = np.zeros(n)
    cum_p2v = np.zeros(n)
    vwap = np.full(n, np.nan)
    dev = np.full(n, np.nan)

    cur_day = None
    for i in range(n):
        if days[i] != cur_day:
            cur_day = days[i]
            running_pv = 0.0
            running_v = 0.0
            running_p2v = 0.0
            session_count = 0
        running_pv += typical[i] * vol[i]
        running_v += vol[i]
        running_p2v += (typical[i] ** 2) * vol[i]
        cum_pv[i] = running_pv
        cum_v[i] = running_v
        cum_p2v[i] = running_p2v
        session_count += 1
        if session_count >= warmup_bars and running_v > 0:
            v = running_pv / running_v
            vwap[i] = v
            # variance = E[p^2] - (E[p])^2, weighted
            mean_p2 = running_p2v / running_v
            var = max(mean_p2 - v * v, 0.0)
            dev[i] = np.sqrt(var)
    return vwap, dev


@register_strategy
class VWAPReversion(BaseStrategy):
    name = "vwap_reversion"

    def __init__(
        self,
        dev_mult: float = 2.0,
        warmup_bars: int = 30,
        sl_atr_mult: float = 0.5,
        tp_target: str = "vwap",   # "vwap" or "rr"
        tp_rr: float = 1.0,
        require_rejection: bool = True,
        atr_period: int = 14,
        cooldown_bars: int = 3,
        use_two_legs: bool = True,
        tp1_rr: float = 0.6,
        leg1_weight: float = 0.5,
        htf_filter: str = "none",   # "none" | "H1" | "M15"
        htf_ema_fast: int = 20,
        htf_ema_slow: int = 50,
        session: str = "always",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            dev_mult=dev_mult,
            warmup_bars=warmup_bars,
            sl_atr_mult=sl_atr_mult,
            tp_target=tp_target,
            tp_rr=tp_rr,
            require_rejection=require_rejection,
            atr_period=atr_period,
            cooldown_bars=cooldown_bars,
            use_two_legs=use_two_legs,
            tp1_rr=tp1_rr,
            leg1_weight=leg1_weight,
            htf_filter=htf_filter,
            htf_ema_fast=htf_ema_fast,
            htf_ema_slow=htf_ema_slow,
            session=session,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        self._vwap: np.ndarray | None = None
        self._dev: np.ndarray | None = None
        self._mtf: MTFContext | None = None
        self._htf_ema_fast: np.ndarray | None = None
        self._htf_ema_slow: np.ndarray | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        self._vwap, self._dev = _session_vwap_and_dev(df, warmup_bars=p["warmup_bars"])
        if p["htf_filter"] != "none":
            self._mtf = MTFContext(base=df, timeframes=[p["htf_filter"]])
            htf_df = self._mtf.frame(p["htf_filter"])
            close = htf_df["close"].to_numpy(dtype=float)
            self._htf_ema_fast = _ema(close, p["htf_ema_fast"])
            self._htf_ema_slow = _ema(close, p["htf_ema_slow"])

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, tp: float, reason: str,
    ) -> Signal:
        p = self.params
        if not p["use_two_legs"]:
            return Signal(side=side, entry=None, stop_loss=sl, take_profit=float(tp), reason=reason)
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def _htf_state(self, ts) -> Optional[str]:
        """Return 'up' / 'down' / 'flat' from HTF EMA. None if no HTF
        bar has closed yet."""
        p = self.params
        if self._mtf is None or self._htf_ema_fast is None:
            return "flat"
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        pos = self._mtf.last_closed_idx(p["htf_filter"], ts_dt)
        if pos is None or pos >= len(self._htf_ema_fast):
            return None
        f = self._htf_ema_fast[pos]; s = self._htf_ema_slow[pos]
        if not np.isfinite(f) or not np.isfinite(s):
            return "flat"
        if f > s:
            return "up"
        if f < s:
            return "down"
        return "flat"

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        if self._vwap is None or self._dev is None or self._atr is None:
            return None
        i = n - 1
        v = self._vwap[i]; d = self._dev[i]
        if not np.isfinite(v) or not np.isfinite(d) or d <= 0:
            return None
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        if p["session"] != "always":
            ts = history.index[-1]
            t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
            if not check_session(t, p["session"]):
                return None

        upper = v + p["dev_mult"] * d
        lower = v - p["dev_mult"] * d

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last
        o = float(last["open"]); h = float(last["high"])
        l = float(last["low"]); c = float(last["close"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        # HTF filter: skip a long if HTF is strongly down.
        htf_state = self._htf_state(history.index[-1]) if p["htf_filter"] != "none" else "flat"
        if htf_state is None:
            return None  # HTF not yet established

        # LONG: low pierced lower band.
        if l <= lower and (htf_state != "down"):
            if p["require_rejection"]:
                rej = c > o and lower_wick >= max(body, 1e-9) and c > float(prev["close"])
                if not rej:
                    return None
            entry = c
            sl = lower - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            tp = v if p["tp_target"] == "vwap" else entry + p["tp_rr"] * risk
            if tp <= entry:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"VWAP long lo={lower:.2f} v={v:.2f} htf={htf_state}",
            )
        if h >= upper and (htf_state != "up"):
            if p["require_rejection"]:
                rej = c < o and upper_wick >= max(body, 1e-9) and c < float(prev["close"])
                if not rej:
                    return None
            entry = c
            sl = upper + p["sl_atr_mult"] * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            tp = v if p["tp_target"] == "vwap" else entry - p["tp_rr"] * risk
            if tp >= entry:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"VWAP short up={upper:.2f} v={v:.2f} htf={htf_state}",
            )
        return None


def _ema(x: np.ndarray, period: int) -> np.ndarray:
    if period <= 1:
        return x.copy()
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out
