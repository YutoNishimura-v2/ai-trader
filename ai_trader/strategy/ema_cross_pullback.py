"""EMA9/21 cross + pullback continuation on M5.

Source: TradingView "EMA Pullback Pro EA v14 (XAUUSD)" + multiple
gold-scalping guides — the classic dual-EMA cross filtered by RSI > 50
(or < 50 for shorts).

Algorithm (long; mirror for short):

1. Resample to M5. Compute EMA(9) and EMA(21) on M5 closes.
2. **Cross detection**: EMA9 > EMA21 (bullish cross active state).
3. **Confirmation**: prior N M5 closes were also above EMA21 (trend
   established, not a noise tick).
4. **Pullback trigger**: current M5 LOW touches EMA9 (within
   touch_dollar) AND closes back above EMA9.
5. **RSI filter**: 14-period RSI > 50 (long) or < 50 (short).
6. **Optional HTF EMA200 trend filter** (close > EMA200 for longs).
7. SL: recent M5 swing low - sl_buffer.
8. TP1 +1R BE; TP2 +2.5R or structural swing high.
9. Cooldown: 6 M5 bars between trades.
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


def _rsi(arr, period=14):
    n = len(arr)
    out = np.full(n, np.nan, dtype=float)
    if n < period + 1: return out
    delta = np.diff(arr)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
    out[period] = 100 - 100/(1+rs)
    for i in range(period+1, n):
        avg_gain = (avg_gain*(period-1) + gain[i-1]) / period
        avg_loss = (avg_loss*(period-1) + loss[i-1]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
        out[i] = 100 - 100/(1+rs)
    return out


@register_strategy
class EmaCrossPullback(BaseStrategy):
    name = "ema_cross_pullback"

    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 21,
        rsi_period: int = 14,
        rsi_long_min: float = 50.0,
        rsi_short_max: float = 50.0,
        confirm_bars: int = 2,         # consecutive M5 closes on side
        touch_dollar: float = 0.50,    # M5 wick touches EMA9 within $X
        sl_buffer_dollar: float = 1.0,
        tp_buffer_dollar: float = 0.5,
        swing_lookback_bars: int = 12,
        max_sl_dollar: float = 6.0,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        cooldown_m5_bars: int = 6,
        session: str | None = "london_or_ny",
        weekdays: list[int] | tuple[int, ...] | None = None,
        max_trades_per_day: int = 6,
        htf: str | None = None,             # e.g. "H1"
        htf_ema_period: int = 200,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            ema_fast=ema_fast, ema_slow=ema_slow,
            rsi_period=rsi_period, rsi_long_min=rsi_long_min,
            rsi_short_max=rsi_short_max,
            confirm_bars=confirm_bars, touch_dollar=touch_dollar,
            sl_buffer_dollar=sl_buffer_dollar,
            tp_buffer_dollar=tp_buffer_dollar,
            swing_lookback_bars=swing_lookback_bars,
            max_sl_dollar=max_sl_dollar,
            tp1_rr=tp1_rr, tp2_rr=tp2_rr, leg1_weight=leg1_weight,
            cooldown_m5_bars=cooldown_m5_bars, session=session,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            max_trades_per_day=max_trades_per_day,
            htf=htf, htf_ema_period=htf_ema_period,
        )
        self.min_history = min_history or (max(ema_slow, rsi_period, swing_lookback_bars) + 5) * 5
        self._mtf = None
        self._m5_o = self._m5_h = self._m5_l = self._m5_c = None
        self._ema_f = self._ema_s = None
        self._rsi = None
        self._htf_close = self._htf_ema = None
        self._last_signal_idx = -10**9
        self._handled_idx = -1
        self._day_key = None
        self._day_trades = 0

    def prepare(self, df):
        tfs = ["M5"]
        if self.params.get("htf"): tfs.append(self.params["htf"])
        self._mtf = MTFContext(base=df, timeframes=tfs)
        m5 = self._mtf.frame("M5")
        self._m5_o = m5["open"].to_numpy(dtype=float, copy=True)
        self._m5_h = m5["high"].to_numpy(dtype=float, copy=True)
        self._m5_l = m5["low"].to_numpy(dtype=float, copy=True)
        self._m5_c = m5["close"].to_numpy(dtype=float, copy=True)
        self._ema_f = _ema(self._m5_c, int(self.params["ema_fast"]))
        self._ema_s = _ema(self._m5_c, int(self.params["ema_slow"]))
        self._rsi = _rsi(self._m5_c, int(self.params["rsi_period"]))
        if self.params.get("htf"):
            htf_df = self._mtf.frame(self.params["htf"])
            self._htf_close = htf_df["close"].to_numpy(dtype=float, copy=True)
            self._htf_ema = _ema(self._htf_close, int(self.params["htf_ema_period"]))

    def _build_signal(self, side, entry, sl, tp_struct, risk, reason):
        p = self.params
        if side == SignalSide.BUY:
            tp1 = entry + p["tp1_rr"] * risk
            tp2 = max(tp_struct, entry + p["tp2_rr"] * risk)
        else:
            tp1 = entry - p["tp1_rr"] * risk
            tp2 = min(tp_struct, entry - p["tp2_rr"] * risk)
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
        if n < self.min_history or self._mtf is None or self._ema_f is None:
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
        idx = self._mtf.last_closed_idx("M5", ts_utc)
        min_idx = max(int(p["ema_slow"]), int(p["rsi_period"]), int(p["confirm_bars"])) + int(p["swing_lookback_bars"])
        if idx is None or idx < min_idx:
            return None
        if idx == self._handled_idx: return None
        if idx - self._last_signal_idx < int(p["cooldown_m5_bars"]): return None
        self._handled_idx = idx

        ef = self._ema_f[idx]; es = self._ema_s[idx]
        cl = self._m5_c[idx]; hi = self._m5_h[idx]; lo = self._m5_l[idx]
        if not (np.isfinite(ef) and np.isfinite(es)): return None
        rsi_v = self._rsi[idx]
        if not np.isfinite(rsi_v): return None

        cb = int(p["confirm_bars"])
        # Trend state
        bull_state = ef > es and bool(np.all(self._m5_c[idx-cb+1:idx+1] > self._ema_s[idx-cb+1:idx+1]))
        bear_state = ef < es and bool(np.all(self._m5_c[idx-cb+1:idx+1] < self._ema_s[idx-cb+1:idx+1]))

        # Pullback
        touch = float(p["touch_dollar"])
        long_pull = lo <= ef + touch and cl > ef
        short_pull = hi >= ef - touch and cl < ef

        # HTF filter
        htf_long_ok = htf_short_ok = True
        if p.get("htf") and self._htf_ema is not None and self._htf_close is not None:
            idx_h = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if idx_h is None or idx_h < int(p["htf_ema_period"]):
                return None
            htf_long_ok = self._htf_close[idx_h] > self._htf_ema[idx_h]
            htf_short_ok = self._htf_close[idx_h] < self._htf_ema[idx_h]

        entry = float(history["close"].iloc[-1])
        sb = int(p["swing_lookback_bars"])
        seg_l = self._m5_l[max(0, idx-sb):idx]
        seg_h = self._m5_h[max(0, idx-sb):idx]
        if len(seg_l) == 0: return None
        swing_low = float(np.min(seg_l)); swing_high = float(np.max(seg_h))
        sl_buf = float(p["sl_buffer_dollar"])
        tp_buf = float(p["tp_buffer_dollar"])
        max_sl = float(p["max_sl_dollar"])

        if bull_state and long_pull and rsi_v > float(p["rsi_long_min"]) and htf_long_ok:
            structural = swing_low - sl_buf
            cap = entry - max_sl
            sl = max(structural, cap)
            risk = entry - sl
            if risk <= 0: return None
            tp_struct = swing_high - tp_buf
            if tp_struct <= entry: return None
            self._day_trades += 1; self._last_signal_idx = idx
            return self._build_signal(SignalSide.BUY, entry, sl, tp_struct, risk,
                                      reason=f"ema9/21 long pullback rsi={rsi_v:.1f}")
        if bear_state and short_pull and rsi_v < float(p["rsi_short_max"]) and htf_short_ok:
            structural = swing_high + sl_buf
            cap = entry + max_sl
            sl = min(structural, cap)
            risk = sl - entry
            if risk <= 0: return None
            tp_struct = swing_low + tp_buf
            if tp_struct >= entry: return None
            self._day_trades += 1; self._last_signal_idx = idx
            return self._build_signal(SignalSide.SELL, entry, sl, tp_struct, risk,
                                      reason=f"ema9/21 short pullback rsi={rsi_v:.1f}")
        return None
