"""Turn-of-month effect strategy for XAUUSD.

Well-documented institutional behavior: portfolio rebalancing flows
into commodities/gold occur on the last 2-3 trading days of the
month and first 2-3 of the new month. Gold typically catches a
directional bid/offer in this window depending on the prevailing
month's trend.

Mechanic: in the turn-of-month window (last N days + first N days),
trade the M15 EMA bias direction with a single TP (per user
2026-04-25: "TP1 alone would be enough").

Entry trigger:
  - Window: last `before_days` of month OR first `after_days` of month
  - M15 EMA(20) > EMA(50) for long, < for short
  - M1 bar closes back above EMA(20) after a pullback
  - SL: structural (recent swing low/high + buffer)
  - TP: single TP at fixed RR (no TP1/TP2 split)
"""
from __future__ import annotations

from datetime import timezone
from calendar import monthrange

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from .base import BaseStrategy, Signal, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class TurnOfMonth(BaseStrategy):
    name = "turn_of_month"

    def __init__(
        self,
        before_days: int = 3,
        after_days: int = 2,
        atr_period: int = 14,
        sl_atr_buf: float = 0.5,
        max_sl_atr: float = 3.0,
        tp_rr: float = 2.0,
        cooldown_bars: int = 60,
        max_trades_per_day: int = 2,
        htf: str = "M15",
        htf_fast_ema: int = 20,
        htf_slow_ema: int = 50,
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            before_days=before_days, after_days=after_days,
            atr_period=atr_period, sl_atr_buf=sl_atr_buf, max_sl_atr=max_sl_atr,
            tp_rr=tp_rr, cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
            htf=htf, htf_fast_ema=htf_fast_ema, htf_slow_ema=htf_slow_ema,
            session=session,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._mtf: MTFContext | None = None
        self._htf_fast: np.ndarray | None = None
        self._htf_slow: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        f, s = int(p["htf_fast_ema"]), int(p["htf_slow_ema"])
        self._htf_fast = htf_df["close"].ewm(span=f, adjust=False, min_periods=f).mean().to_numpy(copy=True)
        self._htf_slow = htf_df["close"].ewm(span=s, adjust=False, min_periods=s).mean().to_numpy(copy=True)

    def _in_tom_window(self, ts) -> bool:
        p = self.params
        last_day = monthrange(ts.year, ts.month)[1]
        if ts.day >= last_day - int(p["before_days"]) + 1:
            return True
        if ts.day <= int(p["after_days"]):
            return True
        return False

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._mtf is None:
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
        if not self._in_tom_window(ts_utc):
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

        pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
        if pos is None or pos >= len(self._htf_fast) or pos >= len(self._htf_slow):
            return None
        ef = float(self._htf_fast[pos])
        es = float(self._htf_slow[pos])
        if not (np.isfinite(ef) and np.isfinite(es)):
            return None

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last
        h, l, c, o = float(last["high"]), float(last["low"]), float(last["close"]), float(last["open"])

        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val

        # LONG: HTF up bias + bullish M1 candle.
        if ef > es and c > o and c > float(prev["close"]):
            entry = c
            sl_struct = float(min(l, float(prev["low"]))) - sl_buf
            sl_cap = entry - max_sl
            sl = max(sl_struct, sl_cap)
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + float(p["tp_rr"]) * risk
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.BUY, entry=None, stop_loss=sl, take_profit=float(tp),
                reason=f"ToM long ef={ef:.2f} es={es:.2f}",
            )

        # SHORT: HTF down bias + bearish M1 candle.
        if ef < es and c < o and c < float(prev["close"]):
            entry = c
            sl_struct = float(max(h, float(prev["high"]))) + sl_buf
            sl_cap = entry + max_sl
            sl = min(sl_struct, sl_cap)
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - float(p["tp_rr"]) * risk
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.SELL, entry=None, stop_loss=sl, take_profit=float(tp),
                reason=f"ToM short ef={ef:.2f} es={es:.2f}",
            )

        return None
