"""Liquidity-sweep reversal scalper.

A "liquidity sweep" (a.k.a. stop hunt, sweep of liquidity) is when
price spikes briefly past a recent swing extreme — taking out the
stops that sit just beyond it — then quickly reverses. The
intuition: when smart money wants to drive price up, it first runs
the lows to fill its longs at the worst-possible-for-retail price.
In SMC/ICT vocabulary this is also CHoCH (change of character)
when it follows a fresh BOS.

This is genuinely different from BB and BOS:

- BB enters mean-reversion at band tags (volatility-extreme entries).
- BOS enters trend continuation at retest of broken structure.
- LIQUIDITY SWEEP enters reversal AT the failed extension, before
  any structure breaks.

Algorithm:

1. Find the rolling extreme over ``swing_window`` bars: high for
   long-stop hunt detection, low for short-stop hunt.
2. The current bar SWEEPS the high if its high > prior rolling
   high by more than ``min_sweep_atr`` × ATR. (Mirror for low.)
3. The sweep IS A SETUP if the same bar's CLOSE is back below
   that prior high (i.e. wick swept liquidity, body did not
   commit). Pure displacement bars aren't liquidity sweeps; they
   need to FAIL.
4. ENTRY: next bar prints a confirming reversal candle (close
   in the opposite direction of the sweep, decent body).
5. SL: just past the sweep extreme + ``sl_atr_buffer`` × ATR.
   This is structural — if the sweep is genuine and immediately
   broken, the trade thesis is wrong.
6. TP: 2-leg with TP1 at ``tp1_rr`` × risk and TP2 stretched.
7. Cooldown + setup TTL.

prepare() caches ATR + rolling extremes (causal: row i uses bars
[i-N..i-1]).
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
class LiquiditySweep(BaseStrategy):
    name = "liquidity_sweep"

    def __init__(
        self,
        swing_window: int = 30,
        atr_period: int = 14,
        min_sweep_atr: float = 0.3,
        sl_atr_buffer: float = 0.3,
        tp1_rr: float = 1.0,
        tp2_rr: float = 2.5,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 5,
        setup_ttl_bars: int = 3,
        require_close_back: bool = True,
        session: str = "always",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            swing_window=swing_window,
            atr_period=atr_period,
            min_sweep_atr=min_sweep_atr,
            sl_atr_buffer=sl_atr_buffer,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            setup_ttl_bars=setup_ttl_bars,
            require_close_back=require_close_back,
            session=session,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(swing_window * 2, atr_period * 3, 100)
        self._atr: pd.Series | None = None
        self._roll_hi: np.ndarray | None = None   # max(high) over [i-N..i-1]
        self._roll_lo: np.ndarray | None = None   # min(low) over [i-N..i-1]
        # Pending setups (only one per side at a time).
        self._sweep_high_setup: dict | None = None  # short setup, swept the highs
        self._sweep_low_setup: dict | None = None   # long setup, swept the lows

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr = atr(df, period=p["atr_period"])
        n = len(df)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        w = p["swing_window"]
        rh = np.full(n, np.nan)
        rl = np.full(n, np.nan)
        if n > w:
            from numpy.lib.stride_tricks import sliding_window_view
            view_h = sliding_window_view(highs, window_shape=w)
            view_l = sliding_window_view(lows, window_shape=w)
            # Window of rows [i-w .. i-1] = view row (i-w); valid for i >= w.
            rh[w:] = view_h[:-1].max(axis=1) if len(view_h) > 1 else np.nan
            rl[w:] = view_l[:-1].min(axis=1) if len(view_l) > 1 else np.nan
        self._roll_hi = rh
        self._roll_lo = rl

    def _build_signal(
        self, side: SignalSide, entry: float, sl: float, risk: float, reason: str,
    ) -> Signal:
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
        if self._atr is None or self._roll_hi is None or self._roll_lo is None:
            return None

        i = n - 1
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        # Session.
        if p["session"] != "always":
            ts = history.index[-1]
            t = ts.time() if hasattr(ts, "time") else dtime(0, 0)
            if not check_session(t, p["session"]):
                return None

        last = history.iloc[-1]
        prev = history.iloc[-2]
        c = float(last["close"]); o = float(last["open"])
        hi = float(last["high"]); lo = float(last["low"])
        body = abs(c - o)

        roll_hi_now = float(self._roll_hi[i])     # rolling high BEFORE this bar
        roll_lo_now = float(self._roll_lo[i])

        sweep_threshold = p["min_sweep_atr"] * atr_val

        # 1) DETECT SWEEP on the just-closed bar (we look at the
        #    previous bar's prior rolling extreme since prepare's
        #    rolling array represents [i-w..i-1] as of bar i).
        # Long setup (low was swept). "Close back" means the bar
        # closed in the upper half of its own range — i.e., the wick
        # went down, but buyers stepped back in. NOT a requirement
        # that close fully recover above the prior swept low (a real
        # sweep often closes still under the prior low; the reversal
        # is the next bar).
        bar_range = hi - lo
        if (
            np.isfinite(roll_lo_now)
            and lo < roll_lo_now - sweep_threshold
            and (
                not p["require_close_back"]
                or (bar_range > 0 and (c - lo) >= 0.5 * bar_range)
            )
            and self._sweep_low_setup is None
        ):
            self._sweep_low_setup = {
                "swept_low": float(roll_lo_now),
                "extreme": lo,
                "atr": atr_val,
                "bars_alive": 0,
            }
        # Short setup (high was swept). Mirror: close in lower half
        # of bar range = sellers reasserted control.
        if (
            np.isfinite(roll_hi_now)
            and hi > roll_hi_now + sweep_threshold
            and (
                not p["require_close_back"]
                or (bar_range > 0 and (hi - c) >= 0.5 * bar_range)
            )
            and self._sweep_high_setup is None
        ):
            self._sweep_high_setup = {
                "swept_high": float(roll_hi_now),
                "extreme": hi,
                "atr": atr_val,
                "bars_alive": 0,
            }

        # 2) AGE setups.
        for slot in ("_sweep_low_setup", "_sweep_high_setup"):
            s = getattr(self, slot)
            if s is not None:
                s["bars_alive"] += 1
                if s["bars_alive"] > p["setup_ttl_bars"]:
                    setattr(self, slot, None)

        # Cooldown.
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None

        # 3) ENTRY confirmation: next bar after the sweep prints a
        #    decent reversal body in the right direction.
        # LONG entry: after a low-sweep, current bar is bullish.
        if self._sweep_low_setup is not None and self._sweep_low_setup["bars_alive"] >= 1:
            bullish = c > o and (c - o) >= 0.3 * (hi - lo) and c > float(prev["close"])
            if bullish and lo > self._sweep_low_setup["extreme"]:
                # We've moved off the swept low; valid reversal.
                entry = c
                sl = self._sweep_low_setup["extreme"] - p["sl_atr_buffer"] * self._sweep_low_setup["atr"]
                risk = entry - sl
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.BUY, entry, sl, risk,
                        reason=f"liquidity sweep reversal long swept={self._sweep_low_setup['swept_low']:.2f}",
                    )
                    self._sweep_low_setup = None
                    return sig

        if self._sweep_high_setup is not None and self._sweep_high_setup["bars_alive"] >= 1:
            bearish = c < o and (o - c) >= 0.3 * (hi - lo) and c < float(prev["close"])
            if bearish and hi < self._sweep_high_setup["extreme"]:
                entry = c
                sl = self._sweep_high_setup["extreme"] + p["sl_atr_buffer"] * self._sweep_high_setup["atr"]
                risk = sl - entry
                if risk > 0:
                    self._last_signal_iloc = n
                    sig = self._build_signal(
                        SignalSide.SELL, entry, sl, risk,
                        reason=f"liquidity sweep reversal short swept={self._sweep_high_setup['swept_high']:.2f}",
                    )
                    self._sweep_high_setup = None
                    return sig

        return None
