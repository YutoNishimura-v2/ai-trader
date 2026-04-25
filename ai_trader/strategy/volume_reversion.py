"""Volume-confirmed mean-reversion scalper.

Same BB-tag-rejection setup as ``bb_scalper`` but with one extra
required confirmation: the rejection bar's tick volume must exceed
a multiple of the rolling-N average. The hypothesis (from the
literature on volume / price-action confluence): high-volume
rejections at extremes are exhaustion; low-volume rejections are
noise.

Adds genuine information beyond pure OHLC.

Caches (all causal, no lookahead):
- ATR (period)
- Bollinger middle / upper / lower (window N)
- Volume rolling mean (window vol_window)
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
class VolumeReversion(BaseStrategy):
    name = "volume_reversion"

    def __init__(
        self,
        bb_n: int = 60,
        bb_k: float = 2.5,
        atr_period: int = 14,
        sl_atr_mult: float = 0.5,
        tp_target: str = "middle",
        tp_rr: float = 1.0,
        require_rejection: bool = True,
        vol_window: int = 30,
        vol_mult: float = 1.5,
        cooldown_bars: int = 2,
        use_two_legs: bool = True,
        tp1_rr: float = 0.6,
        leg1_weight: float = 0.5,
        session: str = "always",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            bb_n=bb_n, bb_k=bb_k, atr_period=atr_period,
            sl_atr_mult=sl_atr_mult, tp_target=tp_target, tp_rr=tp_rr,
            require_rejection=require_rejection,
            vol_window=vol_window, vol_mult=vol_mult,
            cooldown_bars=cooldown_bars,
            use_two_legs=use_two_legs, tp1_rr=tp1_rr, leg1_weight=leg1_weight,
            session=session,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(bb_n * 3, atr_period * 3, vol_window * 3, 100)
        self._atr_cache: pd.Series | None = None
        self._mid: np.ndarray | None = None
        self._up: np.ndarray | None = None
        self._lo: np.ndarray | None = None
        self._vol_mean: np.ndarray | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        n = len(close)
        mid = np.full(n, np.nan)
        up = np.full(n, np.nan)
        lo = np.full(n, np.nan)
        vmean = np.full(n, np.nan)
        bbw = p["bb_n"]
        if n >= bbw:
            from numpy.lib.stride_tricks import sliding_window_view
            view = sliding_window_view(close, window_shape=bbw)
            mean = view.mean(axis=1)
            std = view.std(axis=1, ddof=0)
            start = bbw - 1
            mid[start:] = mean
            up[start:] = mean + p["bb_k"] * std
            lo[start:] = mean - p["bb_k"] * std
        vw = p["vol_window"]
        if n >= vw:
            from numpy.lib.stride_tricks import sliding_window_view
            v_view = sliding_window_view(vol, window_shape=vw)
            v_means = v_view.mean(axis=1)
            vmean[vw - 1:] = v_means
        self._mid, self._up, self._lo, self._vol_mean = mid, up, lo, vmean

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, tp: float, reason: str,
    ) -> Signal:
        p = self.params
        if not p["use_two_legs"]:
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
        if (self._mid is None or self._atr_cache is None
                or self._vol_mean is None):
            return None

        i = n - 1
        if not np.isfinite(self._mid[i]) or not np.isfinite(self._vol_mean[i]):
            return None

        if p["session"] != "always":
            ts = history.index[-1]
            t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
            if not check_session(t, p["session"]):
                return None

        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        mid = float(self._mid[i]); up = float(self._up[i]); lo = float(self._lo[i])
        vmean = float(self._vol_mean[i])
        if vmean <= 0:
            return None

        last = history.iloc[-1]; prev = history.iloc[-2] if n >= 2 else last
        o = float(last["open"]); h = float(last["high"])
        l = float(last["low"]); c = float(last["close"])
        v = float(last["volume"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        # The new gate.
        if v < p["vol_mult"] * vmean:
            return None

        # LONG.
        if l <= lo:
            if p["require_rejection"]:
                rej = c > o and lower_wick >= max(body, 1e-9) and c > float(prev["close"])
                if not rej:
                    return None
            entry = c
            sl = lo - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            tp = mid if p["tp_target"] == "middle" else entry + p["tp_rr"] * risk
            if tp <= entry:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk, tp,
                reason=f"vol-conf BB long lo={lo:.2f} v/vmean={v/vmean:.2f}",
            )

        # SHORT.
        if h >= up:
            if p["require_rejection"]:
                rej = c < o and upper_wick >= max(body, 1e-9) and c < float(prev["close"])
                if not rej:
                    return None
            entry = c
            sl = up + p["sl_atr_mult"] * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            tp = mid if p["tp_target"] == "middle" else entry - p["tp_rr"] * risk
            if tp >= entry:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk, tp,
                reason=f"vol-conf BB short up={up:.2f} v/vmean={v/vmean:.2f}",
            )

        return None
