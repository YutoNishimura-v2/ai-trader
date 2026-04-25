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

from ..data.mtf import MTFContext
from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _adx_series_for_pivot(df: pd.DataFrame, period: int) -> np.ndarray:
    """Causal ADX. Local helper — duplicates pattern from session_sweep_reclaim
    to avoid cross-strategy import."""
    high = df["high"]; low = df["low"]; close = df["close"]
    up = high.diff(); dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)


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
        # Optional HTF ADX gate (skip strong-trend regimes for the
        # mean-reversion-style bounce). adx_max=None disables.
        htf: str | None = None,
        adx_period: int = 14,
        adx_max: float | None = None,
        # "daily" (default) — pivots from prior UTC day OHLC.
        # "weekly"          — pivots from prior calendar week OHLC.
        pivot_period: str = "daily",
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
            htf=htf,
            adx_period=adx_period,
            adx_max=adx_max,
            pivot_period=pivot_period,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        # Per-bar pivot levels precomputed in prepare().
        self._pivots: pd.DataFrame | None = None
        self._mtf: MTFContext | None = None
        self._htf_adx: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0
        # Pivots already touched today, by side+level (avoid re-firing on the same touch).
        self._touched: set[str] = set()

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])

        # Pivot period: "daily" (prior UTC day) or "weekly" (prior cal week).
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        df_utc = df.copy()
        df_utc.index = idx

        period = p.get("pivot_period", "daily")
        if period == "weekly":
            # Group by Mon-anchored ISO week (Mon..Sun)
            buckets = idx.to_period("W-SUN").to_timestamp().tz_localize("UTC")
        else:
            buckets = idx.normalize()
        agg = df_utc.groupby(buckets).agg(
            bk_open=("open", "first"),
            bk_high=("high", "max"),
            bk_low=("low", "min"),
            bk_close=("close", "last"),
        )
        # Shift by one bucket so each row has the PRIOR bucket's OHLC
        # (causal — no peek at current bucket's close).
        prev = agg.shift(1)
        prev["P"] = (prev["bk_high"] + prev["bk_low"] + prev["bk_close"]) / 3.0
        prev["R1"] = 2 * prev["P"] - prev["bk_low"]
        prev["S1"] = 2 * prev["P"] - prev["bk_high"]
        prev["R2"] = prev["P"] + (prev["bk_high"] - prev["bk_low"])
        prev["S2"] = prev["P"] - (prev["bk_high"] - prev["bk_low"])
        # Map back onto each M1 bar via the bar's bucket-key.
        per_bar = prev.loc[buckets, ["P", "R1", "S1", "R2", "S2"]].set_axis(idx)
        self._pivots = per_bar

        # Optional HTF ADX gate.
        if p.get("htf") and p.get("adx_max") is not None:
            self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
            htf_df = self._mtf.frame(p["htf"])
            self._htf_adx = _adx_series_for_pivot(htf_df, int(p["adx_period"]))
        else:
            self._mtf = None
            self._htf_adx = None

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

        # HTF ADX gate (skip strong-trend regimes where pivot bounces fail).
        if self._mtf is not None and self._htf_adx is not None:
            pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if pos is None or pos >= len(self._htf_adx):
                return None
            adx_val = float(self._htf_adx[pos])
            if not np.isfinite(adx_val):
                return None
            if adx_val > float(p["adx_max"]):
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
