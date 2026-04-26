"""Pin bar (wick rejection) reversal on M15.

Source: TradingView "Wick Rejection Pro (XAUUSD Optimized)",
"Pin Bar Reversal Strategy", and gold-scalping educational guides.

A pin bar / hammer is a candle with:
  - small body (relative to total range)
  - very long wick on one side
  - small or absent wick on the opposite side

For a bullish pin (hammer):
  - lower_wick >= 2 × body
  - upper_wick <= 0.4 × body
  - close > open OR close near top of body
We trade the next bar in the direction of the rejection wick.

Algorithm:

1. Resample to M15. Compute ATR(14).
2. Detect bullish/bearish pin on the just-closed M15 bar.
3. ATR floor on the body so we ignore micro-bars.
4. Pin must be at a recent extreme (low for bull, high for bear)
   to fire — otherwise it's a continuation pin not reversal.
5. Optional HTF EMA filter (M30 EMA20 close > EMA → allow longs).
6. Entry market BUY/SELL (engine fills next M1 open).
7. SL: pin's wick extreme - sl_buffer.
8. TP1 +1R BE; TP2 +2.5R or recent swing.
9. Cooldown: 4 M15 bars.
"""
from __future__ import annotations

from datetime import timezone
import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


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


def _ema(arr, period):
    a = 2.0 / (period + 1.0)
    out = np.empty_like(arr, dtype=float)
    if len(arr) == 0:
        return out
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = a * arr[i] + (1 - a) * out[i - 1]
    return out


@register_strategy
class PinBarReversal(BaseStrategy):
    name = "pin_bar_reversal"

    def __init__(
        self,
        atr_period: int = 14,
        wick_to_body: float = 2.0,        # long wick must be ≥ 2× body
        opp_wick_to_body: float = 0.4,    # opposite wick must be ≤ 0.4× body
        body_min_atr: float = 0.10,       # skip dojis (body too tiny)
        body_max_atr: float = 0.7,        # require true pin (body small)
        extreme_lookback: int = 12,       # pin must be at recent extreme
        sl_buffer_dollar: float = 0.5,
        max_sl_dollar: float = 8.0,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        cooldown_m15_bars: int = 4,
        session: str | None = "london_or_ny",
        weekdays: list[int] | tuple[int, ...] | None = None,
        max_trades_per_day: int = 4,
        # Optional HTF trend filter (skip pins against HTF trend).
        htf: str | None = None,
        htf_ema_period: int = 50,
        # If True, allow counter-trend pins (reversal play). If False,
        # require pin to be WITH HTF trend.
        allow_counter_trend: bool = True,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period, wick_to_body=wick_to_body,
            opp_wick_to_body=opp_wick_to_body,
            body_min_atr=body_min_atr, body_max_atr=body_max_atr,
            extreme_lookback=extreme_lookback,
            sl_buffer_dollar=sl_buffer_dollar,
            max_sl_dollar=max_sl_dollar,
            tp1_rr=tp1_rr, tp2_rr=tp2_rr, leg1_weight=leg1_weight,
            cooldown_m15_bars=cooldown_m15_bars,
            session=session,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            max_trades_per_day=max_trades_per_day,
            htf=htf, htf_ema_period=htf_ema_period,
            allow_counter_trend=bool(allow_counter_trend),
        )
        self.min_history = min_history or (max(atr_period, extreme_lookback) + 10) * 15
        self._mtf = None
        self._m15_o = self._m15_h = self._m15_l = self._m15_c = None
        self._m15_atr = None
        self._htf_close = self._htf_ema = None
        self._last_signal_idx = -10**9
        self._handled_idx = -1
        self._day_key = None
        self._day_trades = 0

    def prepare(self, df):
        tfs = ["M15"]
        if self.params.get("htf"): tfs.append(self.params["htf"])
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
        if idx is None or idx < int(p["atr_period"]) + int(p["extreme_lookback"]) + 1:
            return None
        if idx == self._handled_idx:
            return None
        if idx - self._last_signal_idx < int(p["cooldown_m15_bars"]):
            return None
        atr_v = self._m15_atr[idx] if idx < len(self._m15_atr) else float("nan")
        if not np.isfinite(atr_v) or atr_v <= 0:
            return None
        self._handled_idx = idx

        o = self._m15_o[idx]; h = self._m15_h[idx]; l = self._m15_l[idx]; c = self._m15_c[idx]
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        rng = h - l
        if body <= 0 or rng <= 0:
            return None
        # Body-size filter
        body_min = float(p["body_min_atr"]) * atr_v
        body_max = float(p["body_max_atr"]) * atr_v
        if not (body_min <= body <= body_max):
            return None

        wt = float(p["wick_to_body"])
        opp = float(p["opp_wick_to_body"])

        bull_pin = (lower_wick >= wt * body) and (upper_wick <= opp * body)
        bear_pin = (upper_wick >= wt * body) and (lower_wick <= opp * body)

        # Recent-extreme filter
        sb = int(p["extreme_lookback"])
        seg_l = self._m15_l[max(0, idx - sb): idx]
        seg_h = self._m15_h[max(0, idx - sb): idx]
        if len(seg_l) == 0:
            return None
        recent_low = float(np.min(seg_l))
        recent_high = float(np.max(seg_h))
        bull_ctx = l <= recent_low + 0.5 * atr_v
        bear_ctx = h >= recent_high - 0.5 * atr_v

        # HTF filter
        htf_long_ok = htf_short_ok = True
        if p.get("htf") and self._htf_ema is not None and self._htf_close is not None:
            idx_h = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if idx_h is None or idx_h < int(p["htf_ema_period"]):
                return None
            htf_up = self._htf_close[idx_h] > self._htf_ema[idx_h]
            if p["allow_counter_trend"]:
                htf_long_ok = htf_short_ok = True
            else:
                htf_long_ok = htf_up
                htf_short_ok = not htf_up

        entry = float(history["close"].iloc[-1])
        sl_buf = float(p["sl_buffer_dollar"])
        max_sl = float(p["max_sl_dollar"])

        if bull_pin and bull_ctx and htf_long_ok:
            structural = float(l) - sl_buf
            cap = entry - max_sl
            sl = max(structural, cap)
            risk = entry - sl
            if risk <= 0:
                return None
            self._day_trades += 1; self._last_signal_idx = idx
            return self._build_signal(SignalSide.BUY, entry, sl, risk,
                                      reason=f"bull-pin M15 idx={idx}")
        if bear_pin and bear_ctx and htf_short_ok:
            structural = float(h) + sl_buf
            cap = entry + max_sl
            sl = min(structural, cap)
            risk = sl - entry
            if risk <= 0:
                return None
            self._day_trades += 1; self._last_signal_idx = idx
            return self._build_signal(SignalSide.SELL, entry, sl, risk,
                                      reason=f"bear-pin M15 idx={idx}")
        return None
