"""Strategy: Donchian channel break → retest → rejection entry.

Satisfies plan v3 §A.4 (pullback-only) while still trading volatility
breakouts: we don't enter on the break itself, we wait for price to
retrace to the broken level and print a rejection candle there.

Per bar:

1. Compute Donchian upper = max high over the last ``donchian_n``
   bars (prior to the current bar), lower = min low.
2. Arm a pending-long setup when the previous bar's high exceeds
   the previous Donchian upper. Record the broken level and the
   ATR at break time. Mirror for short.
3. While a setup is armed, watch for a retest: the current bar's
   low (long) enters the band ``[broken_level - retest_tol*ATR,
   broken_level + retest_tol*ATR]``.
4. If retest is confirmed AND the current bar is a bullish
   rejection candle (close > open, lower wick >= body, close >
   previous close for long), fire a BUY signal.
5. Invalidate the setup if price travels more than
   ``invalidation_atr`` * ATR beyond the broken level without
   retesting (price ran away) OR if it closes below the broken
   level by more than ``invalidation_atr`` * ATR (break failed).
6. Cooldown between signals to avoid spam.

Stop / target use the same logic as TrendPullbackFib:
- SL = broken_level ∓ sl_atr_mult * ATR
- TP = entry ± tp_rr * (|entry - SL|)
- Optional 2-leg mode with TP1 at tp1_rr and break-even on runner.

Caches:
- ATR over the full frame (causal)
- Donchian upper/lower series (causal; at bar i uses bars [i-N, i-1])
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


@register_strategy
class DonchianRetest(BaseStrategy):
    name = "donchian_retest"

    def __init__(
        self,
        donchian_n: int = 20,
        atr_period: int = 14,
        retest_tolerance_atr: float = 0.5,
        invalidation_atr: float = 2.0,
        sl_atr_mult: float = 1.0,
        tp_rr: float = 2.0,
        cooldown_bars: int = 6,
        setup_ttl_bars: int = 30,
        use_two_legs: bool = False,
        tp1_rr: float = 1.0,
        leg1_weight: float = 0.5,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            donchian_n=donchian_n,
            atr_period=atr_period,
            retest_tolerance_atr=retest_tolerance_atr,
            invalidation_atr=invalidation_atr,
            sl_atr_mult=sl_atr_mult,
            tp_rr=tp_rr,
            cooldown_bars=cooldown_bars,
            setup_ttl_bars=setup_ttl_bars,
            use_two_legs=use_two_legs,
            tp1_rr=tp1_rr,
            leg1_weight=leg1_weight,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(donchian_n * 3, atr_period * 3, 60)
        # Armed setup state (only one of each side at a time).
        self._long_setup: dict | None = None
        self._short_setup: dict | None = None
        # Caches populated by prepare().
        self._atr_cache: pd.Series | None = None
        self._donch_up: np.ndarray | None = None
        self._donch_lo: np.ndarray | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        n = len(df)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        window = p["donchian_n"]
        # Donchian value at iloc i uses bars [i-window, i-1].
        # Positions with < window prior bars get NaN.
        up = np.full(n, np.nan)
        lo = np.full(n, np.nan)
        if n > window:
            from numpy.lib.stride_tricks import sliding_window_view
            win_h = sliding_window_view(highs, window_shape=window)
            win_l = sliding_window_view(lows, window_shape=window)
            # Row i of sliding_window_view covers [i .. i+window-1];
            # we want the window [i-window .. i-1] which is row i-window.
            up[window:] = win_h[:-1].max(axis=1) if len(win_h) > 1 else np.nan
            lo[window:] = win_l[:-1].min(axis=1) if len(win_l) > 1 else np.nan
            # The above set rows [window .. n-1]; the first valid is at i=window.
        self._donch_up = up
        self._donch_lo = lo

    def _atr_at(self, history: pd.DataFrame, n: int) -> float:
        if self._atr_cache is not None and len(self._atr_cache) >= n:
            return float(self._atr_cache.iloc[n - 1])
        return float(atr(history.tail(max(self.params["atr_period"] * 3, 50)),
                         period=self.params["atr_period"]).iloc[-1])

    def _donch_at(self, n: int) -> tuple[float, float]:
        if self._donch_up is not None and self._donch_lo is not None and len(self._donch_up) >= n:
            return float(self._donch_up[n - 1]), float(self._donch_lo[n - 1])
        return float("nan"), float("nan")

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        reason: str,
    ) -> Signal:
        p = self.params
        tp_full = entry + p["tp_rr"] * risk if side == SignalSide.BUY else entry - p["tp_rr"] * risk
        if not p.get("use_two_legs"):
            return Signal(side=side, entry=None, stop_loss=sl, take_profit=float(tp_full), reason=reason)
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp_full), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None

        atr_val = self._atr_at(history, n)
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        donch_up, donch_lo = self._donch_at(n)
        if not np.isfinite(donch_up) or not np.isfinite(donch_lo):
            return None

        prev = history.iloc[-2]
        last = history.iloc[-1]

        # 1) Arm new setups on breaks of the PRIOR bar's Donchian
        #    (so the current bar is the post-break bar).
        prev_iloc = n - 2
        prev_donch_up, prev_donch_lo = self._donch_at(prev_iloc + 1)  # "at prev+1" = uses bars up to prev_iloc
        if np.isfinite(prev_donch_up) and prev["high"] > prev_donch_up and self._long_setup is None:
            self._long_setup = {
                "level": float(prev_donch_up),
                "atr_at_break": atr_val,
                "bars_alive": 0,
                "peak": float(prev["high"]),
            }
        if np.isfinite(prev_donch_lo) and prev["low"] < prev_donch_lo and self._short_setup is None:
            self._short_setup = {
                "level": float(prev_donch_lo),
                "atr_at_break": atr_val,
                "bars_alive": 0,
                "trough": float(prev["low"]),
            }

        # 2) Age / invalidate setups.
        if self._long_setup is not None:
            self._long_setup["bars_alive"] += 1
            self._long_setup["peak"] = max(self._long_setup["peak"], float(last["high"]))
            tol = p["retest_tolerance_atr"] * self._long_setup["atr_at_break"]
            inv = p["invalidation_atr"] * self._long_setup["atr_at_break"]
            lvl = self._long_setup["level"]
            # Failed break: closed back well below broken level.
            if last["close"] < lvl - inv:
                self._long_setup = None
            # Ran away: too far above level without retest.
            elif self._long_setup["peak"] > lvl + inv * 2.0 and last["low"] > lvl + tol:
                self._long_setup = None
            elif self._long_setup["bars_alive"] > p["setup_ttl_bars"]:
                self._long_setup = None
        if self._short_setup is not None:
            self._short_setup["bars_alive"] += 1
            self._short_setup["trough"] = min(self._short_setup["trough"], float(last["low"]))
            tol = p["retest_tolerance_atr"] * self._short_setup["atr_at_break"]
            inv = p["invalidation_atr"] * self._short_setup["atr_at_break"]
            lvl = self._short_setup["level"]
            if last["close"] > lvl + inv:
                self._short_setup = None
            elif self._short_setup["trough"] < lvl - inv * 2.0 and last["high"] < lvl - tol:
                self._short_setup = None
            elif self._short_setup["bars_alive"] > p["setup_ttl_bars"]:
                self._short_setup = None

        # 3) Cooldown + signal eval.
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        body = abs(last["close"] - last["open"])
        upper_wick = last["high"] - max(last["close"], last["open"])
        lower_wick = min(last["close"], last["open"]) - last["low"]

        # Long retest entry.
        if self._long_setup is not None:
            lvl = self._long_setup["level"]
            tol = p["retest_tolerance_atr"] * self._long_setup["atr_at_break"]
            in_retest = lvl - tol <= last["low"] <= lvl + tol
            bullish = (
                last["close"] > last["open"]
                and lower_wick >= body
                and last["close"] > prev["close"]
            )
            if in_retest and bullish:
                entry = float(last["close"])
                sl = float(lvl - p["sl_atr_mult"] * self._long_setup["atr_at_break"])
                risk = entry - sl
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.BUY, entry, sl, risk,
                        reason=f"donchian break+retest long @ {lvl:.2f}",
                    )
                    self._long_setup = None
                    return sig

        # Short retest entry.
        if self._short_setup is not None:
            lvl = self._short_setup["level"]
            tol = p["retest_tolerance_atr"] * self._short_setup["atr_at_break"]
            in_retest = lvl - tol <= last["high"] <= lvl + tol
            bearish = (
                last["close"] < last["open"]
                and upper_wick >= body
                and last["close"] < prev["close"]
            )
            if in_retest and bearish:
                entry = float(last["close"])
                sl = float(lvl + p["sl_atr_mult"] * self._short_setup["atr_at_break"])
                risk = sl - entry
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.SELL, entry, sl, risk,
                        reason=f"donchian break+retest short @ {lvl:.2f}",
                    )
                    self._short_setup = None
                    return sig

        return None
