"""Heikin-Ashi color-flip + EMA trend strategy on M15.

Source: TradingView "Trade Gold with Heikin Ashi and AO"
(28k% over 2 yrs, 78% WR — likely overfit but the mechanic is
well-documented). Conservative version: HA color flip + above
HTF EMA200 trend filter, M15 base.

Algorithm:

1. Resample to M15 (causal). Compute Heikin-Ashi candles:
   ha_close = (o+h+l+c)/4
   ha_open  = (prev ha_open + prev ha_close)/2
   ha_high  = max(h, ha_open, ha_close)
   ha_low   = min(l, ha_open, ha_close)
2. Compute EMA200 on M15 close.
3. Long entry: HA candle just turned GREEN (ha_close > ha_open
   AND prior ha_close <= prior ha_open) AND M15 close > EMA200.
4. Short: mirror.
5. Stop = recent M15 swing low - sl_buffer (long).
6. TP = ATR-based (M15) or structural swing.
7. One trade per HA flip; cooldown_m15 bars.
"""
from __future__ import annotations

from datetime import timezone
import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(arr, dtype=float)
    if len(arr) == 0: return out
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _heikin_ashi(ohlc: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    o = ohlc["open"].to_numpy(dtype=float)
    h = ohlc["high"].to_numpy(dtype=float)
    l = ohlc["low"].to_numpy(dtype=float)
    c = ohlc["close"].to_numpy(dtype=float)
    n = len(o)
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty(n, dtype=float)
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, n):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum.reduce([h, ha_o, ha_c])
    ha_l = np.minimum.reduce([l, ha_o, ha_c])
    return ha_o, ha_h, ha_l, ha_c


@register_strategy
class HeikinAshiTrend(BaseStrategy):
    name = "heikin_ashi_trend"

    def __init__(
        self,
        ema_period: int = 200,
        atr_period: int = 14,
        sl_buffer_dollar: float = 1.0,
        max_sl_dollar: float = 8.0,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        cooldown_m15_bars: int = 3,
        session: str | None = "london_or_ny",
        weekdays: list[int] | tuple[int, ...] | None = None,
        max_trades_per_day: int = 4,
        # Optional second condition: require N consecutive HA candles same color
        confirm_ha_bars: int = 1,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            ema_period=ema_period, atr_period=atr_period,
            sl_buffer_dollar=sl_buffer_dollar, max_sl_dollar=max_sl_dollar,
            tp1_rr=tp1_rr, tp2_rr=tp2_rr, leg1_weight=leg1_weight,
            cooldown_m15_bars=cooldown_m15_bars, session=session,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            max_trades_per_day=max_trades_per_day,
            confirm_ha_bars=confirm_ha_bars,
        )
        self.min_history = min_history or (ema_period + 20) * 15
        self._mtf = None
        self._ha_o = self._ha_h = self._ha_l = self._ha_c = None
        self._ema = None
        self._m15_low = self._m15_high = None
        self._last_signal_idx = -10**9
        self._handled_idx = -1
        self._day_key = None
        self._day_trades = 0

    def prepare(self, df: pd.DataFrame) -> None:
        self._mtf = MTFContext(base=df, timeframes=["M15"])
        m15 = self._mtf.frame("M15")
        self._ha_o, self._ha_h, self._ha_l, self._ha_c = _heikin_ashi(m15)
        self._ema = _ema(m15["close"].to_numpy(dtype=float), int(self.params["ema_period"]))
        self._m15_low = m15["low"].to_numpy(dtype=float, copy=True)
        self._m15_high = m15["high"].to_numpy(dtype=float, copy=True)

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
        if idx is None or idx < int(p["ema_period"]) + 5:
            return None
        if idx == self._handled_idx:
            return None
        if idx - self._last_signal_idx < int(p["cooldown_m15_bars"]):
            return None

        # Color flip detection
        cb = int(p["confirm_ha_bars"])
        # current HA color
        cur_green = self._ha_c[idx] > self._ha_o[idx]
        cur_red = self._ha_c[idx] < self._ha_o[idx]
        # prior HA opposite color (for flip), and N confirms same color (latest only ≥ 1)
        flip_green = cur_green and (self._ha_c[idx-1] <= self._ha_o[idx-1])
        flip_red = cur_red and (self._ha_c[idx-1] >= self._ha_o[idx-1])
        # N-bar confirm: all of last cb candles same color (when cb>1)
        if cb > 1:
            cols = (self._ha_c[idx-cb+1:idx+1] - self._ha_o[idx-cb+1:idx+1])
            same_green = bool(np.all(cols > 0))
            same_red   = bool(np.all(cols < 0))
            flip_green = flip_green and same_green
            flip_red = flip_red and same_red

        ema_v = self._ema[idx]
        m15_close = float(self._mtf.frame("M15")["close"].iloc[idx])
        if not np.isfinite(ema_v):
            return None
        long_trend = m15_close > ema_v
        short_trend = m15_close < ema_v

        entry = float(history["close"].iloc[-1])
        sb = 8
        seg_l = self._m15_low[max(0, idx - sb): idx]
        seg_h = self._m15_high[max(0, idx - sb): idx]
        if len(seg_l) == 0:
            return None
        swing_low = float(np.min(seg_l))
        swing_high = float(np.max(seg_h))
        sl_buf = float(p["sl_buffer_dollar"])
        max_sl = float(p["max_sl_dollar"])
        self._handled_idx = idx

        if flip_green and long_trend:
            structural = swing_low - sl_buf
            cap = entry - max_sl
            sl = max(structural, cap)
            risk = entry - sl
            if risk <= 0: return None
            self._day_trades += 1
            self._last_signal_idx = idx
            return self._build_signal(SignalSide.BUY, entry, sl, risk,
                                      reason=f"HA flip green M15 ema200={ema_v:.2f}")
        if flip_red and short_trend:
            structural = swing_high + sl_buf
            cap = entry + max_sl
            sl = min(structural, cap)
            risk = sl - entry
            if risk <= 0: return None
            self._day_trades += 1
            self._last_signal_idx = idx
            return self._build_signal(SignalSide.SELL, entry, sl, risk,
                                      reason=f"HA flip red M15 ema200={ema_v:.2f}")
        return None
