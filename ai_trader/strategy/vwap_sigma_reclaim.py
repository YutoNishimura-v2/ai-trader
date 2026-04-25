"""Session-VWAP sigma-band reclaim scalper.

Computes the running session VWAP from a daily reset (07:00 UTC
London open by default) and 1.5σ / 2.0σ deviation bands.

Trades:
  - Long when price touches the lower band (-2σ) and closes back
    above the lower band on the same M1 bar (rejection wick).
  - Short when price touches the upper band (+2σ) and closes
    back below.
  - Optional regime gate: M15 ADX < adx_max so we only fade in
    chop / range conditions where mean-reversion has edge.

The original ``vwap_reversion`` used naive entry triggers and
falsified at val PF 0.93. This version is stricter:
  - PROPER session VWAP with daily reset (not full-history VWAP)
  - SIGMA bands computed from rolling deviation, not fixed-K ATR
  - Rejection-candle entry confirmation (not just band touch)
  - Optional ADX gate to skip trend regimes
  - Wider SL anchored to the wick extreme

This is uncorrelated with our other strategies: VWAP is a
volume-weighted reference; reclaim catches mean-reversion where
fib pullbacks catch trend-with-pullback and pivot bounces catch
external level reactions.
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


def _adx_series(df: pd.DataFrame, period: int) -> np.ndarray:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    a = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = (
        100
        * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / a
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / a
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)


@register_strategy
class VwapSigmaReclaim(BaseStrategy):
    name = "vwap_sigma_reclaim"

    def __init__(
        self,
        session_reset_hour: int = 7,            # London open
        sigma_mult: float = 2.0,
        atr_period: int = 14,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.5,
        tp1_rr: float = 0.6,
        tp2_rr: float = 1.5,                    # TP2 = back to VWAP
        leg1_weight: float = 0.6,
        cooldown_bars: int = 10,
        max_trades_per_day: int = 4,
        # Optional HTF ADX gate for chop-only.
        htf: str | None = "M15",
        adx_period: int = 14,
        adx_max: float | None = 25.0,
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            session_reset_hour=session_reset_hour,
            sigma_mult=sigma_mult,
            atr_period=atr_period,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
            htf=htf,
            adx_period=adx_period,
            adx_max=adx_max,
            session=session,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._vwap: np.ndarray | None = None
        self._upper: np.ndarray | None = None
        self._lower: np.ndarray | None = None
        self._mtf: MTFContext | None = None
        self._htf_adx: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])

        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        idx_utc = idx.tz_convert("UTC")

        # Determine session-day key: bars at hour < reset_hour belong to the
        # PREVIOUS session-day. We use a "session-day index" that increments
        # at session_reset_hour each day.
        reset_h = int(p["session_reset_hour"])
        # Subtract reset_h hours so the day-rollover happens at reset_h UTC.
        shifted = idx_utc - pd.Timedelta(hours=reset_h)
        session_day = shifted.normalize()
        sd_arr = session_day.to_numpy()
        n = len(df)

        # For each row, sum (price*vol) and vol since the start of the
        # session-day. price = typical = (H+L+C)/3.
        tp = (df["high"].to_numpy() + df["low"].to_numpy() + df["close"].to_numpy()) / 3.0
        v = df["volume"].to_numpy()
        # Make sure vol > 0 to avoid div by zero (synthetic data may have 1s).
        v = np.where(v > 0, v, 1.0)
        pv = tp * v

        # Cumulative within session-day:
        # We compute group-cumulative by detecting day-changes.
        change_idx = np.concatenate([[0], np.where(sd_arr[1:] != sd_arr[:-1])[0] + 1, [n]])
        vwap = np.full(n, np.nan)
        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)
        sigma_mult = float(p["sigma_mult"])
        for i in range(len(change_idx) - 1):
            s, e = int(change_idx[i]), int(change_idx[i + 1])
            cum_pv = np.cumsum(pv[s:e])
            cum_v = np.cumsum(v[s:e])
            w = cum_pv / cum_v
            # Cumulative variance from the running VWAP (volume-weighted)
            # E[X^2] - (E[X])^2. We approximate with running.
            sq_pv = np.cumsum(((tp[s:e] - w) ** 2) * v[s:e])
            var = sq_pv / cum_v
            sd = np.sqrt(np.maximum(var, 0.0))
            vwap[s:e] = w
            upper[s:e] = w + sigma_mult * sd
            lower[s:e] = w - sigma_mult * sd
        self._vwap = vwap
        self._upper = upper
        self._lower = lower

        # HTF ADX gate.
        if p.get("htf") and p.get("adx_max") is not None:
            self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
            htf_df = self._mtf.frame(p["htf"])
            self._htf_adx = _adx_series(htf_df, int(p["adx_period"]))
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
        if n < self.min_history or self._atr_cache is None:
            return None
        if self._vwap is None or self._upper is None or self._lower is None:
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

        vwap = float(self._vwap[i])
        upper = float(self._upper[i])
        lower = float(self._lower[i])
        if not (np.isfinite(vwap) and np.isfinite(upper) and np.isfinite(lower)):
            return None
        if upper <= vwap or lower >= vwap:
            return None  # bands not yet established

        # HTF ADX gate (chop only).
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

        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val

        # Long: low pierced lower band, close back above with bullish rejection.
        if l <= lower and c > lower and c > o and lower_wick >= body * 0.6:
            entry = c
            structural_sl = l - sl_buf
            cap_sl = entry - max_sl
            sl = max(structural_sl, cap_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            # TP2 anchored to VWAP if closer than tp2_rr * risk.
            tp2_rr = entry + float(p["tp2_rr"]) * risk
            tp2 = min(tp2_rr, vwap) if vwap > entry else tp2_rr
            tp1_rr = entry + float(p["tp1_rr"]) * risk
            w1 = float(p["leg1_weight"])
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1_rr), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.BUY, entry=None, stop_loss=sl, legs=legs,
                reason=f"vwap-sigma reclaim long lower={lower:.2f} vwap={vwap:.2f}",
            )

        # Short: high pierced upper band, close back below with bearish rejection.
        if h >= upper and c < upper and c < o and upper_wick >= body * 0.6:
            entry = c
            structural_sl = h + sl_buf
            cap_sl = entry + max_sl
            sl = min(structural_sl, cap_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            tp2_rr = entry - float(p["tp2_rr"]) * risk
            tp2 = max(tp2_rr, vwap) if vwap < entry else tp2_rr
            tp1_rr = entry - float(p["tp1_rr"]) * risk
            w1 = float(p["leg1_weight"])
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1_rr), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.SELL, entry=None, stop_loss=sl, legs=legs,
                reason=f"vwap-sigma reclaim short upper={upper:.2f} vwap={vwap:.2f}",
            )

        return None
