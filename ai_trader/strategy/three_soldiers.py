"""Three white soldiers / three black crows continuation on M15.

Source: mql5 Trader's Blog "XAUUSD H4 Three White Candles & Three
Black Crows + EMA 50/200 + RSI 35/65". We adapt to M15 base (more
trades) but keep H1 EMA50/EMA200 trend filter.

Algorithm:

1. M15 base. For each closed M15 candle, check if the last 3 bars
   form three white soldiers (bullish) or three black crows
   (bearish):
   - 3 consecutive bullish (close > open) candles
   - each opens within prior body
   - each closes higher than prior
   - body sizes >= bar_min_body_atr * ATR (skip dojis)
2. HTF trend filter (H1 EMA50 vs EMA200): trade only with HTF trend.
3. Entry: market BUY/SELL on the M1 bar that follows the third
   confirmed M15 bar. SL: low of FIRST candle (long).
4. TP: structural — recent M15 swing high; or RR-based.
5. Cooldown: 4 M15 bars.
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
    if len(arr) == 0: return out
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
class ThreeSoldiers(BaseStrategy):
    name = "three_soldiers"

    def __init__(
        self,
        m15_atr_period: int = 14,
        bar_min_body_atr: float = 0.30,    # skip dojis
        max_sl_dollar: float = 8.0,
        sl_buffer_dollar: float = 0.50,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        cooldown_m15_bars: int = 4,
        session: str | None = "london_or_ny",
        weekdays: list[int] | tuple[int, ...] | None = None,
        max_trades_per_day: int = 4,
        # HTF filter: H1 EMA fast > EMA slow ⇒ allow longs only.
        htf: str | None = "H1",
        htf_ema_fast: int = 50,
        htf_ema_slow: int = 200,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            m15_atr_period=m15_atr_period,
            bar_min_body_atr=bar_min_body_atr,
            max_sl_dollar=max_sl_dollar,
            sl_buffer_dollar=sl_buffer_dollar,
            tp1_rr=tp1_rr, tp2_rr=tp2_rr, leg1_weight=leg1_weight,
            cooldown_m15_bars=cooldown_m15_bars,
            session=session,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            max_trades_per_day=max_trades_per_day,
            htf=htf, htf_ema_fast=htf_ema_fast, htf_ema_slow=htf_ema_slow,
        )
        self.min_history = min_history or (htf_ema_slow * 60 + 60)
        self._mtf = None
        self._m15_o = self._m15_h = self._m15_l = self._m15_c = None
        self._m15_atr = None
        self._htf_fast = self._htf_slow = None
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
        self._m15_atr = _atr_local(m15, int(self.params["m15_atr_period"]))
        if self.params.get("htf"):
            htf_df = self._mtf.frame(self.params["htf"])
            cl = htf_df["close"].to_numpy(dtype=float, copy=True)
            self._htf_fast = _ema(cl, int(self.params["htf_ema_fast"]))
            self._htf_slow = _ema(cl, int(self.params["htf_ema_slow"]))

    def _check_three_white(self, idx):
        if idx < 2: return False
        for i in (idx-2, idx-1, idx):
            if self._m15_c[i] <= self._m15_o[i]: return False
        # Each opens within prior body, closes higher.
        for i in (idx-1, idx):
            if not (self._m15_o[i] >= self._m15_o[i-1] and self._m15_o[i] <= self._m15_c[i-1]):
                return False
            if not (self._m15_c[i] > self._m15_c[i-1]):
                return False
        # Body size filter (use ATR @ idx).
        atr_v = self._m15_atr[idx] if idx < len(self._m15_atr) else float("nan")
        if not np.isfinite(atr_v): return False
        thr = float(self.params["bar_min_body_atr"]) * atr_v
        for i in (idx-2, idx-1, idx):
            if (self._m15_c[i] - self._m15_o[i]) < thr: return False
        return True

    def _check_three_black(self, idx):
        if idx < 2: return False
        for i in (idx-2, idx-1, idx):
            if self._m15_c[i] >= self._m15_o[i]: return False
        for i in (idx-1, idx):
            if not (self._m15_o[i] <= self._m15_o[i-1] and self._m15_o[i] >= self._m15_c[i-1]):
                return False
            if not (self._m15_c[i] < self._m15_c[i-1]):
                return False
        atr_v = self._m15_atr[idx] if idx < len(self._m15_atr) else float("nan")
        if not np.isfinite(atr_v): return False
        thr = float(self.params["bar_min_body_atr"]) * atr_v
        for i in (idx-2, idx-1, idx):
            if (self._m15_o[i] - self._m15_c[i]) < thr: return False
        return True

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
        if self._day_trades >= int(p["max_trades_per_day"]): return None

        idx = self._mtf.last_closed_idx("M15", ts_utc)
        if idx is None or idx < 5: return None
        if idx == self._handled_idx: return None
        if idx - self._last_signal_idx < int(p["cooldown_m15_bars"]): return None

        # HTF trend filter
        htf_long = htf_short = True
        if p.get("htf") and self._htf_fast is not None and self._htf_slow is not None:
            idx_h = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if idx_h is None or idx_h < int(p["htf_ema_slow"]):
                self._handled_idx = idx
                return None
            f = self._htf_fast[idx_h]; s = self._htf_slow[idx_h]
            if not (np.isfinite(f) and np.isfinite(s)):
                self._handled_idx = idx
                return None
            htf_long = f > s
            htf_short = f < s

        entry = float(history["close"].iloc[-1])
        sl_buf = float(p["sl_buffer_dollar"])
        max_sl = float(p["max_sl_dollar"])
        self._handled_idx = idx

        if htf_long and self._check_three_white(idx):
            # SL = low of FIRST candle.
            structural = float(self._m15_l[idx-2]) - sl_buf
            cap = entry - max_sl
            sl = max(structural, cap)
            risk = entry - sl
            if risk <= 0: return None
            self._day_trades += 1
            self._last_signal_idx = idx
            return self._build_signal(SignalSide.BUY, entry, sl, risk,
                                      reason=f"3-white-soldiers M15 idx={idx}")
        if htf_short and self._check_three_black(idx):
            structural = float(self._m15_h[idx-2]) + sl_buf
            cap = entry + max_sl
            sl = min(structural, cap)
            risk = sl - entry
            if risk <= 0: return None
            self._day_trades += 1
            self._last_signal_idx = idx
            return self._build_signal(SignalSide.SELL, entry, sl, risk,
                                      reason=f"3-black-crows M15 idx={idx}")
        return None
