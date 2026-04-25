"""Asian-range sweep-and-reclaim scalper for XAUUSD.

Gold often raids one side of the quiet Asian box around London / NY
liquidity, then snaps back through the level. This strategy trades the
reclaim, not the initial stop-hunt:

- Build the Asian range from 00:00-05:00 UTC.
- During the active window, detect a sweep beyond one edge by an
  ATR-scaled buffer.
- Enter reversal only if the same bar closes back inside the range
  with a rejection wick.
- SL goes beyond the sweep extreme, capped by ATR; TP1 moves runner to
  break-even and TP2 targets the opposite box edge or an R multiple.
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
    """Causal ADX on a HTF OHLC frame."""
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
class SessionSweepReclaim(BaseStrategy):
    name = "session_sweep_reclaim"

    def __init__(
        self,
        range_start_hour: int = 0,
        range_end_hour: int = 5,
        trade_start_hour: int = 7,
        trade_end_hour: int = 16,
        atr_period: int = 14,
        min_range_atr: float = 0.8,
        min_sweep_atr: float = 0.15,
        sl_atr_buffer: float = 0.25,
        max_sl_atr: float = 2.0,
        tp_mode: str = "opposite_edge",  # "opposite_edge" | "rr"
        tp1_rr: float = 0.6,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        max_trades_per_day: int = 1,
        # Optional HTF gating. Two independent filters:
        # 1) ``htf`` + ``htf_mode``: EMA-bias direction filter.
        # 2) ``adx_max`` + ``adx_period``: only fire when HTF ADX
        #    is below this ceiling (i.e. range / chop regime, where
        #    Asian-sweep reclaims have the strongest empirical
        #    edge). ``adx_max=None`` disables the ADX gate.
        htf: str | None = None,
        htf_ema_fast: int = 20,
        htf_ema_slow: int = 50,
        htf_mode: str = "off",  # "off" | "with" | "neutral_or_with" | "skip_counter_trend"
        adx_max: float | None = None,
        adx_period: int = 14,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            range_start_hour=range_start_hour,
            range_end_hour=range_end_hour,
            trade_start_hour=trade_start_hour,
            trade_end_hour=trade_end_hour,
            atr_period=atr_period,
            min_range_atr=min_range_atr,
            min_sweep_atr=min_sweep_atr,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            tp_mode=tp_mode,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            max_trades_per_day=max_trades_per_day,
            htf=htf,
            htf_ema_fast=htf_ema_fast,
            htf_ema_slow=htf_ema_slow,
            htf_mode=htf_mode,
            adx_max=adx_max,
            adx_period=adx_period,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr: pd.Series | None = None
        self._range_hi: np.ndarray | None = None
        self._range_lo: np.ndarray | None = None
        self._day_key: Optional[str] = None
        self._day_trades: int = 0
        self._long_swept: bool = False
        self._short_swept: bool = False
        self._mtf: MTFContext | None = None
        self._htf_bias: np.ndarray | None = None
        self._htf_adx: np.ndarray | None = None

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

        need_htf = (
            (p["htf"] and p["htf_mode"] != "off")
            or (p["htf"] and p["adx_max"] is not None)
        )
        if need_htf:
            self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
            htf_df = self._mtf.frame(p["htf"])
            if p["htf_mode"] != "off":
                f = int(p["htf_ema_fast"])
                s = int(p["htf_ema_slow"])
                ef = htf_df["close"].ewm(span=f, adjust=False, min_periods=f).mean().to_numpy()
                es = htf_df["close"].ewm(span=s, adjust=False, min_periods=s).mean().to_numpy()
                diff = ef - es
                bias = np.where(diff > 0, 1, np.where(diff < 0, -1, 0)).astype(np.int8)
                bias[~np.isfinite(es)] = 0
                self._htf_bias = bias
            else:
                self._htf_bias = None
            if p["adx_max"] is not None:
                self._htf_adx = _adx_series(htf_df, int(p["adx_period"]))
            else:
                self._htf_adx = None
        else:
            self._mtf = None
            self._htf_bias = None
            self._htf_adx = None

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, tp2: float, reason: str
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
        if n < self.min_history or self._atr is None or self._range_hi is None or self._range_lo is None:
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
            self._long_swept = False
            self._short_swept = False
        if self._day_trades >= p["max_trades_per_day"]:
            return None
        if not (p["trade_start_hour"] <= ts_utc.hour < p["trade_end_hour"]):
            return None
        hi = float(self._range_hi[i])
        lo = float(self._range_lo[i])
        if not np.isfinite(hi) or not np.isfinite(lo):
            return None
        if hi - lo < p["min_range_atr"] * atr_val:
            return None

        last = history.iloc[-1]
        o = float(last["open"]); h = float(last["high"])
        l = float(last["low"]); c = float(last["close"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        sweep = p["min_sweep_atr"] * atr_val

        if l < lo - sweep:
            self._long_swept = True
        if h > hi + sweep:
            self._short_swept = True

        # HTF gates: bias direction + ADX ceiling.
        bias = 0
        if self._mtf is not None:
            pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if pos is None:
                return None
            if self._htf_bias is not None:
                if pos >= len(self._htf_bias):
                    return None
                bias = int(self._htf_bias[pos])
            if self._htf_adx is not None and p["adx_max"] is not None:
                if pos >= len(self._htf_adx):
                    return None
                adx_val = float(self._htf_adx[pos])
                if not np.isfinite(adx_val):
                    return None
                if adx_val > float(p["adx_max"]):
                    return None

        def _allowed(side: SignalSide) -> bool:
            mode = p["htf_mode"]
            if mode == "off" or self._mtf is None:
                return True
            # "with": reclaims that agree with HTF trend (long-only in
            # up-bias, short-only in down-bias). Empirically this kills
            # the strategy because session_sweep_reclaim is fundamentally
            # a counter-trend mean-reversion. Kept for completeness.
            if mode == "with":
                return (side == SignalSide.BUY and bias > 0) or (side == SignalSide.SELL and bias < 0)
            # "neutral_or_with": same as with but also allow neutral.
            if mode == "neutral_or_with":
                return (side == SignalSide.BUY and bias >= 0) or (side == SignalSide.SELL and bias <= 0)
            # "skip_counter_trend": this is the empirically-useful mode.
            # The Jan/Feb drag came from short reclaims fading strong
            # M15 uptrends. Skip a reclaim that would fade an HTF
            # trend (i.e., short when bias > 0, or long when bias < 0).
            # Allow reclaims that agree with bias OR fire in neutral
            # regimes (where reclaims are highest-probability).
            if mode == "skip_counter_trend":
                if side == SignalSide.BUY and bias < 0:
                    return False
                if side == SignalSide.SELL and bias > 0:
                    return False
                return True
            return True

        # Sweep below Asian low, reclaim back inside → long.
        if self._long_swept and c > lo and c > o and _allowed(SignalSide.BUY):
            entry = c
            structural_sl = l - p["sl_atr_buffer"] * atr_val
            capped_sl = entry - p["max_sl_atr"] * atr_val
            sl = max(structural_sl, capped_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            tp2 = hi if p["tp_mode"] == "opposite_edge" else entry + p["tp2_rr"] * risk
            if tp2 <= entry:
                tp2 = entry + p["tp2_rr"] * risk
            self._day_trades += 1
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp2,
                reason=f"session-sweep-reclaim long lo={lo:.2f} hi={hi:.2f}",
            )

        # Sweep above Asian high, reclaim back inside → short.
        if self._short_swept and c < hi and c < o and _allowed(SignalSide.SELL):
            entry = c
            structural_sl = h + p["sl_atr_buffer"] * atr_val
            capped_sl = entry + p["max_sl_atr"] * atr_val
            sl = min(structural_sl, capped_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            tp2 = lo if p["tp_mode"] == "opposite_edge" else entry - p["tp2_rr"] * risk
            if tp2 >= entry:
                tp2 = entry - p["tp2_rr"] * risk
            self._day_trades += 1
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp2,
                reason=f"session-sweep-reclaim short lo={lo:.2f} hi={hi:.2f}",
            )
        return None
