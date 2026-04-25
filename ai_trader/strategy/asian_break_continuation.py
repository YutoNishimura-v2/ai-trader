"""Asian-range BREAK continuation strategy (opposite of session_sweep_reclaim).

Hypothesis: when London opens and CLEANLY breaks the Asian range
(close > range_high + ATR buffer for long), continuation has edge
for the first 30-60 min. This is the OPPOSITE side of
sweep_reclaim's bet — sweep_reclaim catches false breakouts that
reverse; this catches REAL breakouts that continue.

Mechanic:
  - Asian range: 00:00-06:00 UTC OHLC.
  - Window: London open 07:00-10:00 UTC only.
  - LONG: M1 close > Asian_high + break_atr*ATR with bullish bar.
  - SHORT: M1 close < Asian_low - break_atr*ATR with bearish bar.
  - SL: opposite range edge + buffer (capped by max_sl_atr).
  - TP: single TP at fixed RR (no TP1/TP2 split per user feedback).
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalSide
from .registry import register_strategy


@register_strategy
class AsianBreakContinuation(BaseStrategy):
    name = "asian_break_continuation"

    def __init__(
        self,
        range_start_hour: int = 0,
        range_end_hour: int = 6,
        trade_start_hour: int = 7,
        trade_end_hour: int = 10,
        atr_period: int = 14,
        min_range_atr: float = 0.5,
        break_atr: float = 0.20,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.5,
        tp_rr: float = 2.0,
        cooldown_bars: int = 30,
        max_trades_per_day: int = 1,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            range_start_hour=range_start_hour, range_end_hour=range_end_hour,
            trade_start_hour=trade_start_hour, trade_end_hour=trade_end_hour,
            atr_period=atr_period, min_range_atr=min_range_atr,
            break_atr=break_atr, sl_atr_buf=sl_atr_buf, max_sl_atr=max_sl_atr,
            tp_rr=tp_rr, cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._range_hi: np.ndarray | None = None
        self._range_lo: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0
        self._fired_long: bool = False
        self._fired_short: bool = False

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
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
            if len(pos) == 0: continue
            day_idx = idx_utc[pos]
            mins = day_idx.hour * 60 + day_idx.minute
            in_range = (mins >= start_min) & (mins < end_min)
            after = mins >= end_min
            if not in_range.any(): continue
            hi = highs[pos[in_range]].max()
            lo = lows[pos[in_range]].min()
            rng_hi[pos[after]] = hi
            rng_lo[pos[after]] = lo
        self._range_hi = rng_hi
        self._range_lo = rng_lo

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._range_hi is None:
            return None
        if n - self._last_signal_iloc < int(p["cooldown_bars"]):
            return None
        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0: return None
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
        if self._day_trades >= int(p["max_trades_per_day"]): return None
        if not (p["trade_start_hour"] <= ts_utc.hour < p["trade_end_hour"]): return None
        hi = float(self._range_hi[i])
        lo = float(self._range_lo[i])
        if not (np.isfinite(hi) and np.isfinite(lo)): return None
        if hi - lo < float(p["min_range_atr"]) * atr_val: return None
        last = history.iloc[-1]
        h, l, c, o = float(last["high"]), float(last["low"]), float(last["close"]), float(last["open"])
        break_buf = float(p["break_atr"]) * atr_val
        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val
        # LONG break: close > hi + break_buf, bullish.
        if not self._fired_long and c > hi + break_buf and c > o:
            entry = c
            sl_struct = lo - sl_buf
            sl_cap = entry - max_sl
            sl = max(sl_struct, sl_cap)
            risk = entry - sl
            if risk <= 0: return None
            tp = entry + float(p["tp_rr"]) * risk
            self._day_trades += 1
            self._fired_long = True
            self._last_signal_iloc = n
            return Signal(side=SignalSide.BUY, entry=None, stop_loss=sl, take_profit=float(tp),
                          reason=f"asian-break-cont long hi={hi:.2f}")
        # SHORT break: close < lo - break_buf, bearish.
        if not self._fired_short and c < lo - break_buf and c < o:
            entry = c
            sl_struct = hi + sl_buf
            sl_cap = entry + max_sl
            sl = min(sl_struct, sl_cap)
            risk = sl - entry
            if risk <= 0: return None
            tp = entry - float(p["tp_rr"]) * risk
            self._day_trades += 1
            self._fired_short = True
            self._last_signal_iloc = n
            return Signal(side=SignalSide.SELL, entry=None, stop_loss=sl, take_profit=float(tp),
                          reason=f"asian-break-cont short lo={lo:.2f}")
        return None
