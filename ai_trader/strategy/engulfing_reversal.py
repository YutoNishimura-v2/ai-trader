"""Engulfing-pattern reversal on M15.

Source: TradingView "Strongest Reversal Candlestick Patterns For
Gold & Forex" + multiple ICT/SMC educators.

A bullish engulfing candle (current green body fully engulfs the
prior red body) at oversold/support is a high-probability reversal.
We add an ATR / range filter and a swing/Bollinger context filter.

Algorithm (long; mirror for short):

1. Resample to M15. For each closed M15 bar, check engulfing:
   - prior bar is bearish (close < open)
   - current bar is bullish (close > open)
   - current open <= prior close
   - current close >= prior open
   - current body size >= min_body_atr * ATR
2. Optional context: bar low is at or below a recent swing low
   (within touch_dollar) — i.e. an oversold "trap" engulfing.
3. SL: low of the engulfing bar - sl_buffer.
4. TP1: +1R, TP2: structural (recent M15 swing high) or +2.5R.
5. HTF filter: M30 EMA20 trend (close > EMA → allow longs).
6. Cooldown 4 M15 bars.
"""
from __future__ import annotations

from datetime import timezone
import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _ema(arr, period):
    a = 2.0 / (period + 1.0)
    out = np.empty_like(arr, dtype=float)
    if len(arr) == 0:
        return out
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = a * arr[i] + (1 - a) * out[i - 1]
    return out


def _atr_local(df, period):
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    n = len(h)
    tr = np.empty(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    out = np.empty(n, dtype=float)
    out[:period] = np.nan
    if n > period:
        out[period] = tr[1:period+1].mean()
        for i in range(period + 1, n):
            out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out


@register_strategy
class EngulfingReversal(BaseStrategy):
    name = "engulfing_reversal"

    def __init__(
        self,
        atr_period: int = 14,
        min_body_atr: float = 0.6,         # current body >= 0.6 * ATR
        require_oversold: bool = True,     # bar must be near recent low
        oversold_lookback: int = 12,
        sl_buffer_dollar: float = 0.5,
        max_sl_dollar: float = 8.0,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        cooldown_m15_bars: int = 4,
        session: str | None = "london_or_ny",
        weekdays: list[int] | tuple[int, ...] | None = None,
        max_trades_per_day: int = 4,
        # HTF filter: skip if HTF EMA20 trend is opposite our reversal.
        # (For BUY engulfing, we still allow even if HTF says down — it's
        #  a counter-trend reversal play. Set htf to disable filter.)
        htf: str | None = None,
        htf_ema_period: int = 50,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period, min_body_atr=min_body_atr,
            require_oversold=require_oversold,
            oversold_lookback=oversold_lookback,
            sl_buffer_dollar=sl_buffer_dollar,
            max_sl_dollar=max_sl_dollar,
            tp1_rr=tp1_rr, tp2_rr=tp2_rr, leg1_weight=leg1_weight,
            cooldown_m15_bars=cooldown_m15_bars, session=session,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            max_trades_per_day=max_trades_per_day,
            htf=htf, htf_ema_period=htf_ema_period,
        )
        self.min_history = min_history or (atr_period + oversold_lookback + 5) * 15
        self._mtf = None
        self._m15_o = self._m15_h = self._m15_l = self._m15_c = None
        self._m15_atr = None
        self._htf_ema = self._htf_close = None
        self._last_signal_idx = -10**9
        self._handled_idx = -1
        self._day_key = None
        self._day_trades = 0

    def prepare(self, df):
        tfs = ["M15"]
        if self.params.get("htf"):
            tfs.append(self.params["htf"])
        self._mtf = MTFContext(base=df, timeframes=tfs)
        m15 = self._mtf.frame("M15")
        self._m15_o = m15["open"].to_numpy(dtype=float, copy=True)
        self._m15_h = m15["high"].to_numpy(dtype=float, copy=True)
        self._m15_l = m15["low"].to_numpy(dtype=float, copy=True)
        self._m15_c = m15["close"].to_numpy(dtype=float, copy=True)
        self._m15_atr = _atr_local(m15, int(self.params["atr_period"]))
        if self.params.get("htf"):
            htf_df = self._mtf.frame(self.params["htf"])
            self._htf_close = htf_df["close"].to_numpy(dtype=float, copy=True)
            self._htf_ema = _ema(self._htf_close, int(self.params["htf_ema_period"]))

    def _build_signal(self, side, entry, sl, risk, reason):
        p = self.params
        if side == SignalSide.BUY:
            tp1 = entry + p["tp1_rr"] * risk
            tp2 = entry + p["tp2_rr"] * risk
        else:
            tp1 = entry - p["tp1_rr"] * risk
            tp2 = entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        if w1 >= 0.999:
            legs = (SignalLeg(weight=1.0, take_profit=float(tp1), tag="tp1"),)
        else:
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history):
        p = self.params
        n = len(history)
        if n < self.min_history or self._mtf is None or self._m15_c is None:
            return None
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        if (sess := p.get("session")) and not check_session(ts_utc.time(), sess):
            return None
        if (wds := p.get("weekdays")) is not None and ts_utc.weekday() not in wds:
            return None
        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key; self._day_trades = 0
        if self._day_trades >= int(p["max_trades_per_day"]):
            return None
        idx = self._mtf.last_closed_idx("M15", ts_utc)
        if idx is None or idx < int(p["atr_period"]) + int(p["oversold_lookback"]) + 1:
            return None
        if idx == self._handled_idx:
            return None
        if idx - self._last_signal_idx < int(p["cooldown_m15_bars"]):
            return None
        atr_v = self._m15_atr[idx] if idx < len(self._m15_atr) else float("nan")
        if not np.isfinite(atr_v):
            return None
        self._handled_idx = idx

        # Bullish engulfing
        prev_o, prev_c = self._m15_o[idx-1], self._m15_c[idx-1]
        cur_o, cur_c, cur_l, cur_h = self._m15_o[idx], self._m15_c[idx], self._m15_l[idx], self._m15_h[idx]
        body_thr = float(p["min_body_atr"]) * atr_v
        bull_engulf = (
            prev_c < prev_o and cur_c > cur_o
            and cur_o <= prev_c and cur_c >= prev_o
            and (cur_c - cur_o) >= body_thr
        )
        bear_engulf = (
            prev_c > prev_o and cur_c < cur_o
            and cur_o >= prev_c and cur_c <= prev_o
            and (cur_o - cur_c) >= body_thr
        )

        # Oversold/overbought context
        if p["require_oversold"]:
            sb = int(p["oversold_lookback"])
            seg_l = self._m15_l[max(0, idx-sb):idx]
            seg_h = self._m15_h[max(0, idx-sb):idx]
            recent_low = float(np.min(seg_l)) if len(seg_l) else cur_l
            recent_high = float(np.max(seg_h)) if len(seg_h) else cur_h
            # Long needs cur_l near recent_low
            long_ctx = cur_l <= recent_low + 0.5 * atr_v
            short_ctx = cur_h >= recent_high - 0.5 * atr_v
        else:
            long_ctx = short_ctx = True

        # Optional HTF filter
        htf_long_ok = htf_short_ok = True
        if p.get("htf") and self._htf_ema is not None and self._htf_close is not None:
            idx_h = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if idx_h is None or idx_h < int(p["htf_ema_period"]):
                return None
            htf_bias_up = self._htf_close[idx_h] > self._htf_ema[idx_h]
            # Reversal play: allow against the HTF trend (bullish engulf
            # in down-trend = the classic capitulation buy). We instead
            # require HTF to be NEUTRAL or WITH the reversal — block if
            # HTF is strongly opposite. Simple rule: just require HTF
            # in same direction as the reversal entry to be conservative.
            htf_long_ok = htf_bias_up
            htf_short_ok = not htf_bias_up

        entry = float(history["close"].iloc[-1])
        sl_buf = float(p["sl_buffer_dollar"])
        max_sl = float(p["max_sl_dollar"])

        if bull_engulf and long_ctx and htf_long_ok:
            structural = float(cur_l) - sl_buf
            cap = entry - max_sl
            sl = max(structural, cap)
            risk = entry - sl
            if risk <= 0:
                return None
            self._day_trades += 1
            self._last_signal_idx = idx
            return self._build_signal(SignalSide.BUY, entry, sl, risk,
                                      reason=f"bull-engulfing M15 idx={idx}")
        if bear_engulf and short_ctx and htf_short_ok:
            structural = float(cur_h) + sl_buf
            cap = entry + max_sl
            sl = min(structural, cap)
            risk = sl - entry
            if risk <= 0:
                return None
            self._day_trades += 1
            self._last_signal_idx = idx
            return self._build_signal(SignalSide.SELL, entry, sl, risk,
                                      reason=f"bear-engulfing M15 idx={idx}")
        return None
