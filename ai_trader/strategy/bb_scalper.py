"""Bollinger Band mean-reversion scalper (M1-oriented).

Classic scalping setup: price tags the outer Bollinger band, shows
a rejection candle back toward the middle, we enter with a tight
ATR-scaled SL and a TP at the middle band (or an R-multiple).

Satisfies plan v3 §A.4 (pullback-only): entering the reversal from
a band-tag *is* a pullback from an extreme. We are not chasing
extension.

Why this is designed for scalping, not swing:

- Trade frequency scales with volatility and N; at M1 with bb_n=20
  tags happen several times per hour on XAUUSD.
- TP is at the middle band (default), which is typically ~1 * std
  away: designed for many small wins, not a few big ones.
- SL is ATR-based and tight (default 0.5 * ATR past the band), so
  risk per trade stays well inside the lot-cap + risk-% budget
  even at small balances.

Caches (prepare hook):
- ATR(period)
- Rolling mean + std over bb_n bars → band upper/middle/lower
  All strictly causal: value at iloc i uses only bars <= i.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class BBScalper(BaseStrategy):
    name = "bb_scalper"

    def __init__(
        self,
        bb_n: int = 20,
        bb_k: float = 2.0,
        atr_period: int = 14,
        sl_atr_mult: float = 0.5,
        tp_target: str = "middle",   # "middle" or "rr"
        tp_rr: float = 1.0,
        require_rejection: bool = True,
        cooldown_bars: int = 2,
        use_two_legs: bool = False,
        tp1_rr: float = 0.6,
        leg1_weight: float = 0.5,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            bb_n=bb_n,
            bb_k=bb_k,
            atr_period=atr_period,
            sl_atr_mult=sl_atr_mult,
            tp_target=tp_target,
            tp_rr=tp_rr,
            require_rejection=require_rejection,
            cooldown_bars=cooldown_bars,
            use_two_legs=use_two_legs,
            tp1_rr=tp1_rr,
            leg1_weight=leg1_weight,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(bb_n * 3, atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._mid: np.ndarray | None = None
        self._up: np.ndarray | None = None
        self._lo: np.ndarray | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        close = df["close"].to_numpy(dtype=float)
        n = len(close)
        mid = np.full(n, np.nan)
        up = np.full(n, np.nan)
        lo = np.full(n, np.nan)
        window = p["bb_n"]
        if n >= window:
            from numpy.lib.stride_tricks import sliding_window_view
            # Trailing window: value at i uses close[i-window+1..i].
            # sliding_window_view gives windows starting at i; we want
            # the window ENDING at i, i.e. row (i-window+1) of the view.
            view = sliding_window_view(close, window_shape=window)
            # view has shape (n - window + 1, window); view[j] = close[j..j+window-1]
            # so value at i = view[i - window + 1]; valid for i >= window - 1.
            mean = view.mean(axis=1)
            std = view.std(axis=1, ddof=0)
            start = window - 1
            mid[start:] = mean
            up[start:] = mean + p["bb_k"] * std
            lo[start:] = mean - p["bb_k"] * std
        self._mid = mid
        self._up = up
        self._lo = lo

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        tp: float,
        reason: str,
    ) -> Signal:
        p = self.params
        if not p.get("use_two_legs"):
            return Signal(side=side, entry=None, stop_loss=sl, take_profit=float(tp), reason=reason)
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        # Caches expected for scalping; slow path is prohibitive.
        if self._mid is None or self._atr_cache is None:
            return None
        idx = n - 1
        if idx >= len(self._mid) or not np.isfinite(self._mid[idx]):
            return None

        atr_val = float(self._atr_cache.iloc[idx])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        mid = float(self._mid[idx])
        up = float(self._up[idx])
        lo = float(self._lo[idx])

        last = history.iloc[-1]
        prev = history.iloc[-2] if n >= 2 else last

        o = float(last["open"]); h = float(last["high"])
        l = float(last["low"]); c = float(last["close"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        # LONG: low tagged / pierced lower band; rejection back up.
        if l <= lo:
            if p["require_rejection"]:
                rej = (
                    c > o
                    and lower_wick >= max(body, 1e-9)
                    and c > float(prev["close"])
                )
                if not rej:
                    return None
            entry = c
            sl = lo - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            if p["tp_target"] == "middle":
                tp = mid
            else:
                tp = entry + p["tp_rr"] * risk
            # TP must be on the correct side of entry.
            if tp <= entry:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"bb-tag long lo={lo:.2f} mid={mid:.2f}",
            )

        # SHORT: high tagged / pierced upper band; rejection back down.
        if h >= up:
            if p["require_rejection"]:
                rej = (
                    c < o
                    and upper_wick >= max(body, 1e-9)
                    and c < float(prev["close"])
                )
                if not rej:
                    return None
            entry = c
            sl = up + p["sl_atr_mult"] * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            if p["tp_target"] == "middle":
                tp = mid
            else:
                tp = entry - p["tp_rr"] * risk
            if tp >= entry:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"bb-tag short up={up:.2f} mid={mid:.2f}",
            )

        return None
