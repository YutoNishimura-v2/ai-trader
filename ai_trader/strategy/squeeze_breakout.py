"""Volatility-compression breakout scalper for XAUUSD.

Gold often alternates between dead compression and violent expansion.
This strategy searches for a Bollinger/Keltner squeeze, then enters
only after price closes beyond the compression box during an active
liquidity session. The design is deliberately simple and causal:

1. Bollinger Bands inside Keltner Channels = compression.
2. A recent squeeze must have occurred in the last N bars.
3. Current bar closes beyond the recent compression high/low by an
   ATR buffer.
4. Optional volume spike confirms participation.
5. Two legs: TP1 moves the runner to break-even; TP2 targets a fixed
   R multiple.
"""
from __future__ import annotations

from datetime import time as dtime

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class SqueezeBreakout(BaseStrategy):
    name = "squeeze_breakout"

    def __init__(
        self,
        bb_n: int = 20,
        bb_k: float = 2.0,
        kc_atr_mult: float = 1.5,
        atr_period: int = 14,
        squeeze_lookback: int = 20,
        box_lookback: int = 20,
        break_atr: float = 0.2,
        sl_atr_buffer: float = 0.3,
        max_sl_atr: float = 2.0,
        require_volume_spike: bool = False,
        volume_lookback: int = 20,
        volume_mult: float = 1.5,
        session: str = "london_or_ny",
        cooldown_bars: int = 10,
        tp1_rr: float = 0.6,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            bb_n=bb_n,
            bb_k=bb_k,
            kc_atr_mult=kc_atr_mult,
            atr_period=atr_period,
            squeeze_lookback=squeeze_lookback,
            box_lookback=box_lookback,
            break_atr=break_atr,
            sl_atr_buffer=sl_atr_buffer,
            max_sl_atr=max_sl_atr,
            require_volume_spike=require_volume_spike,
            volume_lookback=volume_lookback,
            volume_mult=volume_mult,
            session=session,
            cooldown_bars=cooldown_bars,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
        )
        self.min_history = min_history or max(bb_n * 3, atr_period * 3, box_lookback + 5, 80)
        self._atr: pd.Series | None = None
        self._mid: np.ndarray | None = None
        self._bb_up: np.ndarray | None = None
        self._bb_lo: np.ndarray | None = None
        self._kc_up: np.ndarray | None = None
        self._kc_lo: np.ndarray | None = None
        self._squeeze: np.ndarray | None = None
        self._vol_ma: np.ndarray | None = None
        self._last_signal_iloc = -(10**9)

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        close = df["close"].to_numpy(dtype=float)
        n = len(close)
        mid = np.full(n, np.nan)
        up = np.full(n, np.nan)
        lo = np.full(n, np.nan)
        window = int(p["bb_n"])
        if n >= window:
            from numpy.lib.stride_tricks import sliding_window_view

            view = sliding_window_view(close, window_shape=window)
            mean = view.mean(axis=1)
            std = view.std(axis=1, ddof=0)
            start = window - 1
            mid[start:] = mean
            up[start:] = mean + p["bb_k"] * std
            lo[start:] = mean - p["bb_k"] * std
        atr_arr = self._atr.to_numpy(dtype=float)
        kc_up = mid + p["kc_atr_mult"] * atr_arr
        kc_lo = mid - p["kc_atr_mult"] * atr_arr
        self._mid = mid
        self._bb_up = up
        self._bb_lo = lo
        self._kc_up = kc_up
        self._kc_lo = kc_lo
        self._squeeze = (up < kc_up) & (lo > kc_lo)

        vol = df["volume"].to_numpy(dtype=float)
        self._vol_ma = pd.Series(vol, index=df.index).rolling(
            int(p["volume_lookback"]), min_periods=int(p["volume_lookback"])
        ).mean().to_numpy(dtype=float)

    def _build_signal(self, side: SignalSide, entry: float, sl: float, risk: float, reason: str) -> Signal:
        p = self.params
        if side == SignalSide.BUY:
            tp1 = entry + p["tp1_rr"] * risk
            tp2 = entry + p["tp2_rr"] * risk
        else:
            tp1 = entry - p["tp1_rr"] * risk
            tp2 = entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        if self._atr is None or self._squeeze is None or self._vol_ma is None:
            return None
        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        session = p.get("session", "always")
        if session != "always":
            ts = history.index[-1]
            t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
            if not check_session(t, session):
                return None

        sq_start = max(0, i - int(p["squeeze_lookback"]))
        if not np.nan_to_num(self._squeeze[sq_start:i], nan=False).any():
            return None

        box_n = int(p["box_lookback"])
        box = history.iloc[max(0, n - box_n - 1): n - 1]
        if len(box) < max(5, box_n // 2):
            return None
        box_hi = float(box["high"].max())
        box_lo = float(box["low"].min())

        last = history.iloc[-1]
        o = float(last["open"])
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        break_buf = p["break_atr"] * atr_val

        if p["require_volume_spike"]:
            vma = float(self._vol_ma[i])
            vol = float(last.get("volume", 0.0))
            if not np.isfinite(vma) or vma <= 0 or vol < p["volume_mult"] * vma:
                return None

        if c > box_hi + break_buf and c > o:
            entry = c
            structural_sl = box_lo - p["sl_atr_buffer"] * atr_val
            capped_sl = entry - p["max_sl_atr"] * atr_val
            sl = max(structural_sl, capped_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk,
                reason=f"squeeze-breakout long box={box_lo:.2f}-{box_hi:.2f}",
            )

        if c < box_lo - break_buf and c < o:
            entry = c
            structural_sl = box_hi + p["sl_atr_buffer"] * atr_val
            capped_sl = entry + p["max_sl_atr"] * atr_val
            sl = min(structural_sl, capped_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk,
                reason=f"squeeze-breakout short box={box_lo:.2f}-{box_hi:.2f}",
            )
        return None
