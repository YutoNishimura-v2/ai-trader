"""Keltner-channel breakout (continuation) strategy on M15.

Source: bestmt4ea Keltner EA tutorial + ForexCycle + TradingView
"Tutorial on How to Use Keltner Channels". Different from our
existing `keltner_mean_reversion` strategy: this one BUYS breakouts
above the upper band (with EMA slope agreement), rather than
fading them.

Algorithm:

1. Resample to M15. Compute EMA20 (mid line) and ATR(14).
2. Upper = EMA20 + atr_mult × ATR; Lower = EMA20 - atr_mult × ATR.
3. **Long breakout**:
   - M15 close > upper + min_break_atr × ATR (real breakout, not wick)
   - EMA20 sloping up (current EMA > EMA `slope_lookback` bars ago)
4. **Short breakout** = mirror.
5. SL: behind mid (EMA20) or 0.8× ATR — whichever further.
6. TP1 +1R BE, TP2 +2× ATR or RR-based.
7. London/NY session, optional weekday filter.
8. Cooldown 4 M15 bars.
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
class KeltnerBreakout(BaseStrategy):
    name = "keltner_breakout"

    def __init__(
        self,
        ema_period: int = 20,
        atr_period: int = 14,
        atr_mult: float = 2.0,
        min_break_atr: float = 0.15,    # close beyond band by N * ATR
        slope_lookback: int = 5,        # EMA slope check window
        sl_atr_mult: float = 0.8,
        sl_buffer_dollar: float = 0.5,
        max_sl_dollar: float = 8.0,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        cooldown_m15_bars: int = 4,
        session: str | None = "london_or_ny",
        weekdays: list[int] | tuple[int, ...] | None = None,
        max_trades_per_day: int = 4,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            ema_period=ema_period, atr_period=atr_period, atr_mult=atr_mult,
            min_break_atr=min_break_atr, slope_lookback=slope_lookback,
            sl_atr_mult=sl_atr_mult, sl_buffer_dollar=sl_buffer_dollar,
            max_sl_dollar=max_sl_dollar, tp1_rr=tp1_rr, tp2_rr=tp2_rr,
            leg1_weight=leg1_weight, cooldown_m15_bars=cooldown_m15_bars,
            session=session,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            max_trades_per_day=max_trades_per_day,
        )
        self.min_history = min_history or (max(ema_period, atr_period, slope_lookback) + 10) * 15
        self._mtf = None
        self._ema = None; self._atr = None
        self._m15_c = None
        self._last_signal_idx = -10**9
        self._handled_idx = -1
        self._day_key = None
        self._day_trades = 0

    def prepare(self, df):
        self._mtf = MTFContext(base=df, timeframes=["M15"])
        m15 = self._mtf.frame("M15")
        cl = m15["close"].to_numpy(dtype=float, copy=True)
        self._ema = _ema(cl, int(self.params["ema_period"]))
        self._atr = _atr_local(m15, int(self.params["atr_period"]))
        self._m15_c = cl

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
        if n < self.min_history or self._mtf is None or self._ema is None:
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
        if idx is None or idx < int(p["ema_period"]) + int(p["slope_lookback"]) + 5:
            return None
        if idx == self._handled_idx:
            return None
        if idx - self._last_signal_idx < int(p["cooldown_m15_bars"]):
            return None
        atr_v = self._atr[idx] if idx < len(self._atr) else float("nan")
        if not np.isfinite(atr_v) or atr_v <= 0:
            return None
        ema_v = self._ema[idx]; cl = self._m15_c[idx]
        if not (np.isfinite(ema_v) and np.isfinite(cl)):
            return None
        upper = ema_v + float(p["atr_mult"]) * atr_v
        lower = ema_v - float(p["atr_mult"]) * atr_v
        sl_atr = float(p["sl_atr_mult"]) * atr_v
        sl_buf = float(p["sl_buffer_dollar"])
        max_sl = float(p["max_sl_dollar"])
        slope_lb = int(p["slope_lookback"])
        slope = ema_v - self._ema[idx - slope_lb]
        break_thr = float(p["min_break_atr"]) * atr_v

        entry = float(history["close"].iloc[-1])
        self._handled_idx = idx
        # Long breakout
        if cl > upper + break_thr and slope > 0:
            structural = ema_v
            cap = entry - max_sl
            sl = max(structural - sl_buf, cap)
            sl = min(sl, entry - sl_atr)  # SL must be at least sl_atr below entry
            risk = entry - sl
            if risk <= 0:
                return None
            self._day_trades += 1; self._last_signal_idx = idx
            return self._build_signal(SignalSide.BUY, entry, sl, risk,
                                      reason=f"keltner-breakout long M15 idx={idx}")
        # Short breakout
        if cl < lower - break_thr and slope < 0:
            structural = ema_v
            cap = entry + max_sl
            sl = min(structural + sl_buf, cap)
            sl = max(sl, entry + sl_atr)
            risk = sl - entry
            if risk <= 0:
                return None
            self._day_trades += 1; self._last_signal_idx = idx
            return self._build_signal(SignalSide.SELL, entry, sl, risk,
                                      reason=f"keltner-breakout short M15 idx={idx}")
        return None
