"""ATR squeeze breakout — fire when M15 ATR drops to 25th percentile.

Hypothesis: low-volatility periods precede directional moves
(volatility clustering). When M15 ATR drops to the bottom 25% of
its rolling distribution, the next directional break of the
recent range has positive expectancy.

Mechanic:
  - Compute M15 ATR percentile rank over rolling 100-bar window.
  - When current ATR <= 25th percentile, "squeeze" is active.
  - During squeeze, watch for M1 close > 20-bar high (long) or
    < 20-bar low (short).
  - Single TP at fixed RR.
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from .base import BaseStrategy, Signal, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class AtrSqueezeBreakout(BaseStrategy):
    name = "atr_squeeze_breakout"

    def __init__(
        self,
        atr_period: int = 14,
        squeeze_lookback: int = 100,
        squeeze_pct: float = 25.0,
        range_lookback: int = 20,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.5,
        tp_rr: float = 2.5,
        cooldown_bars: int = 30,
        max_trades_per_day: int = 3,
        htf: str = "M15",
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period, squeeze_lookback=squeeze_lookback,
            squeeze_pct=squeeze_pct, range_lookback=range_lookback,
            sl_atr_buf=sl_atr_buf, max_sl_atr=max_sl_atr,
            tp_rr=tp_rr, cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
            htf=htf, session=session,
        )
        self.min_history = min_history or max(squeeze_lookback * 2, atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._mtf: MTFContext | None = None
        self._htf_atr: np.ndarray | None = None
        self._htf_atr_pct_rank: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        htf_atr = atr(htf_df, period=int(p["atr_period"])).to_numpy(copy=True)
        # Rolling percentile rank
        N = int(p["squeeze_lookback"])
        rank = np.full(len(htf_atr), np.nan)
        for i in range(N, len(htf_atr)):
            window = htf_atr[i-N:i]
            window = window[~np.isnan(window)]
            if len(window) > 0:
                rank[i] = (window <= htf_atr[i]).mean() * 100.0
        self._htf_atr = htf_atr
        self._htf_atr_pct_rank = rank

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._mtf is None:
            return None
        if n - self._last_signal_iloc < int(p["cooldown_bars"]): return None
        ts = history.index[-1]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None: ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_utc = ts_dt.astimezone(timezone.utc)
        sess = p.get("session")
        if sess and not check_session(ts_utc.time(), sess): return None
        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key
            self._day_trades = 0
        if self._day_trades >= int(p["max_trades_per_day"]): return None
        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0: return None
        # Check HTF squeeze
        pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
        if pos is None or pos >= len(self._htf_atr_pct_rank): return None
        rank = float(self._htf_atr_pct_rank[pos])
        if not np.isfinite(rank) or rank > float(p["squeeze_pct"]): return None
        # Range
        rl = int(p["range_lookback"])
        if i < rl: return None
        recent = history.iloc[-rl-1:-1]
        rh = float(recent["high"].max())
        rlow = float(recent["low"].min())
        last = history.iloc[-1]
        h, l, c, o = float(last["high"]), float(last["low"]), float(last["close"]), float(last["open"])
        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val
        # LONG breakout
        if c > rh and c > o:
            entry = c
            sl_struct = rlow - sl_buf
            sl_cap = entry - max_sl
            sl = max(sl_struct, sl_cap)
            risk = entry - sl
            if risk <= 0: return None
            tp = entry + float(p["tp_rr"]) * risk
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(side=SignalSide.BUY, entry=None, stop_loss=sl, take_profit=float(tp),
                          reason=f"squeeze-break long pct={rank:.0f}")
        # SHORT breakout
        if c < rlow and c < o:
            entry = c
            sl_struct = rh + sl_buf
            sl_cap = entry + max_sl
            sl = min(sl_struct, sl_cap)
            risk = sl - entry
            if risk <= 0: return None
            tp = entry - float(p["tp_rr"]) * risk
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(side=SignalSide.SELL, entry=None, stop_loss=sl, take_profit=float(tp),
                          reason=f"squeeze-break short pct={rank:.0f}")
        return None
