"""Asian-range breakout (trend-day complement to ``session_sweep_reclaim``).

Every member of ``ensemble_ultimate`` is mean-reverting / fade-style
(news_fade, friday_flush_fade, session_sweep_reclaim). That ensemble
bleeds in trend-heavy months (Jan -17.8 %, Mar -17.1 %) because its
counter-trend members get steamrolled while waiting for the reclaim
that never comes.

This strategy is the explicit COMPLEMENT: when the M15 trend is
strong AND aligned, trade the Asian-range BREAKOUT (not the reclaim).
The same Asian box (00:00-06:00 UTC) is reused so trade attribution
is symmetric with ``session_sweep_reclaim``.

Entry rules:
- Active window: 07:00-14:00 UTC (London + early NY) — same as the
  reclaim sister.
- The most-recent fully-closed M15 bar must show:
  - EMA bias aligned with the breakout side (fast > slow for long,
    fast < slow for short)
  - ADX >= ``min_trend_adx`` (default 22).
- Long: bar HIGH > range_hi + ``break_atr * ATR`` AND bar CLOSE
  > range_hi (closed beyond the level — not just a wick).
- Short: symmetric.
- One direction per day; max ``max_trades_per_day`` total.

SL: structural - one ATR_buffer below the breakout bar's low (long)
or above the high (short), capped by ``max_sl_atr``.

TP: two-leg. TP1 at ``+tp1_rr * R`` and moves the runner to break-
even. TP2 at the larger of ``+tp2_rr * R`` or
``+tp2_range_extension * (range_hi - range_lo)`` (Asian-range
extension target).

Why this might add edge:
- It is structurally uncorrelated with the existing ensemble: when
  M15 trend is up, sweep_reclaim is shorting the failed breakouts
  AGAINST the trend — and bleeding. asian_breakout fires LONG with
  the trend on the same bar. They cannot both fire in the same
  direction at the same time (the gates are mutually exclusive),
  so they don't double-up risk; they hedge.
- Asian-range breakouts on FX / metals are well-documented (the
  classic "Range Trader" / "London Open Breakout" archetypes).
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


def _adx_series(df: pd.DataFrame, period: int) -> np.ndarray:
    """Causal ADX on a HTF OHLC frame (same as session_sweep_reclaim)."""
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
class AsianBreakout(BaseStrategy):
    name = "asian_breakout"

    def __init__(
        self,
        range_start_hour: int = 0,
        range_end_hour: int = 6,
        trade_start_hour: int = 7,
        trade_end_hour: int = 14,
        atr_period: int = 14,
        min_range_atr: float = 0.6,
        break_atr: float = 0.20,
        sl_atr_buffer: float = 0.30,
        max_sl_atr: float = 2.0,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.0,
        tp2_range_extension: float = 0.5,
        leg1_weight: float = 0.5,
        max_trades_per_day: int = 2,
        # HTF trend gate (this is the structural OPPOSITE of session_sweep_reclaim).
        htf: str = "M15",
        htf_ema_fast: int = 20,
        htf_ema_slow: int = 50,
        htf_adx_period: int = 14,
        min_trend_adx: float = 22.0,
        cooldown_bars: int = 5,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            range_start_hour=range_start_hour,
            range_end_hour=range_end_hour,
            trade_start_hour=trade_start_hour,
            trade_end_hour=trade_end_hour,
            atr_period=atr_period,
            min_range_atr=min_range_atr,
            break_atr=break_atr,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            tp2_range_extension=tp2_range_extension,
            leg1_weight=leg1_weight,
            max_trades_per_day=max_trades_per_day,
            htf=htf,
            htf_ema_fast=htf_ema_fast,
            htf_ema_slow=htf_ema_slow,
            htf_adx_period=htf_adx_period,
            min_trend_adx=min_trend_adx,
            cooldown_bars=cooldown_bars,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        self._range_hi: np.ndarray | None = None
        self._range_lo: np.ndarray | None = None
        self._mtf: MTFContext | None = None
        self._htf_fast: np.ndarray | None = None
        self._htf_slow: np.ndarray | None = None
        self._htf_adx: np.ndarray | None = None
        self._day_key: Optional[str] = None
        self._day_trades: int = 0
        self._fired_long: bool = False
        self._fired_short: bool = False
        self._last_signal_iloc: int = -(10**9)

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        n = len(df)
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        idx_utc = idx.tz_convert("UTC")
        days = idx_utc.normalize().to_numpy()
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        rng_hi = np.full(n, np.nan)
        rng_lo = np.full(n, np.nan)
        start_min = p["range_start_hour"] * 60
        end_min = p["range_end_hour"] * 60
        for day in np.unique(days):
            pos = np.flatnonzero(days == day)
            if len(pos) == 0:
                continue
            day_idx = idx_utc[pos]
            mins = day_idx.hour * 60 + day_idx.minute
            in_range = (mins >= start_min) & (mins < end_min)
            after = mins >= end_min
            if not in_range.any():
                continue
            hi = highs[pos[in_range]].max()
            lo = lows[pos[in_range]].min()
            rng_hi[pos[after]] = hi
            rng_lo[pos[after]] = lo
        self._range_hi = rng_hi
        self._range_lo = rng_lo

        # HTF trend gate caches.
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        f = int(p["htf_ema_fast"])
        s = int(p["htf_ema_slow"])
        ef = htf_df["close"].ewm(span=f, adjust=False, min_periods=f).mean().to_numpy(copy=True)
        es = htf_df["close"].ewm(span=s, adjust=False, min_periods=s).mean().to_numpy(copy=True)
        self._htf_fast = ef
        self._htf_slow = es
        adx_arr = _adx_series(htf_df, int(p["htf_adx_period"]))
        # Ensure writable so tests can override for deterministic synthetic data.
        self._htf_adx = np.array(adx_arr, copy=True)

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        tp2: float,
        reason: str,
    ) -> Signal:
        p = self.params
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr is None or self._range_hi is None:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key
            self._day_trades = 0
            self._fired_long = False
            self._fired_short = False
        if self._day_trades >= p["max_trades_per_day"]:
            return None
        if not (p["trade_start_hour"] <= ts_utc.hour < p["trade_end_hour"]):
            return None

        hi = float(self._range_hi[i])
        lo = float(self._range_lo[i])
        if not np.isfinite(hi) or not np.isfinite(lo):
            return None
        rng = hi - lo
        if rng < p["min_range_atr"] * atr_val:
            return None

        # HTF trend bias + ADX strength.
        if self._mtf is None:
            return None
        pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
        if pos is None or self._htf_fast is None or self._htf_slow is None or self._htf_adx is None:
            return None
        if pos >= len(self._htf_fast) or pos >= len(self._htf_slow) or pos >= len(self._htf_adx):
            return None
        ef = float(self._htf_fast[pos])
        es = float(self._htf_slow[pos])
        adx_val = float(self._htf_adx[pos])
        if not (np.isfinite(ef) and np.isfinite(es) and np.isfinite(adx_val)):
            return None
        if adx_val < float(p["min_trend_adx"]):
            return None
        bias_up = ef > es
        bias_dn = ef < es

        last = history.iloc[-1]
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        break_buf = float(p["break_atr"]) * atr_val

        # Long breakout: above the Asian high + buffer, closed beyond, M15 bias UP.
        if bias_up and not self._fired_long and h > hi + break_buf and c > hi:
            entry = c
            structural_sl = l - p["sl_atr_buffer"] * atr_val
            capped_sl = entry - p["max_sl_atr"] * atr_val
            sl = max(structural_sl, capped_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            tp_rr = entry + p["tp2_rr"] * risk
            tp_ext = entry + p["tp2_range_extension"] * rng
            tp2 = max(tp_rr, tp_ext)
            self._day_trades += 1
            self._fired_long = True
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp2,
                reason=f"asian-break long hi={hi:.2f} adx={adx_val:.1f}",
            )

        # Short breakout: below the Asian low - buffer, closed beyond, M15 bias DOWN.
        if bias_dn and not self._fired_short and l < lo - break_buf and c < lo:
            entry = c
            structural_sl = h + p["sl_atr_buffer"] * atr_val
            capped_sl = entry + p["max_sl_atr"] * atr_val
            sl = min(structural_sl, capped_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            tp_rr = entry - p["tp2_rr"] * risk
            tp_ext = entry - p["tp2_range_extension"] * rng
            tp2 = min(tp_rr, tp_ext)
            self._day_trades += 1
            self._fired_short = True
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp2,
                reason=f"asian-break short lo={lo:.2f} adx={adx_val:.1f}",
            )

        return None
