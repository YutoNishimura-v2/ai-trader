"""Trend-pullback scalper (user's original strategy 1, M1-scaled).

Discretionary recipe (user's own words, paraphrased):
  "If highs/lows are rising it's a strong uptrend. I use Fibonacci
   to pull back to 38.2 or 50 before entering. I set a logical SL.
   For TP, I don't get greedy — place it at recent highs/lows, or
   close manually if the price action looks suspicious."

Systematic translation for M1 scalping:

Three confirmations, all required:

1. **Trend alignment.** Higher-TF EMA slope (default: EMA(slow_ema)
   on M1 acting as an M5-equivalent filter) and EMA(fast_ema) above
   EMA(slow_ema) for longs, mirror for shorts. The slope requirement
   (last N bars monotonically above a tolerance) rejects the chop
   where mean-reversion scalpers thrive and trend scalpers die.

2. **Fib pullback zone.** Recent impulse leg = rolling max/min over
   the last ``impulse_lookback`` bars on the correct side. The fib
   zone spans ``fib_min`` .. ``fib_max`` retracement of that leg.
   Price must have entered the zone (low touches it for long).

3. **Rejection trigger.** Current bar is a rejection candle in
   the trend direction:
     - long: close > open, lower wick ≥ body, close > prev close
     - short: close < open, upper wick ≥ body, close < prev close

Execution:

- Stop: just past the zone boundary, ± ``sl_atr_mult`` × ATR.
  "Logical" stop rather than fixed-distance — respects structure.
- Default 2-leg: TP1 at ``tp1_rr`` × risk with break-even on runner;
  TP2 at ``tp2_rr`` × risk (stretched). This lets winners actually
  run, addressing the "BB caps profit at 1R" limitation.
- Cooldown between signals so we don't spam on the same leg.

Prepare hook precomputes EMA fast, EMA slow, ATR, and rolling
impulse extremes across the whole frame. All strictly causal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy


def _ema(x: np.ndarray, period: int) -> np.ndarray:
    """Wilder-style EMA with alpha = 2/(period+1) (classic)."""
    if period <= 1:
        return x.copy()
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


@register_strategy
class TrendPullbackScalper(BaseStrategy):
    name = "trend_pullback_scalper"

    def __init__(
        self,
        fast_ema: int = 20,
        slow_ema: int = 50,
        slope_bars: int = 5,
        slope_min_atr: float = 0.02,
        impulse_lookback: int = 60,
        fib_min: float = 0.382,
        fib_max: float = 0.618,
        atr_period: int = 14,
        sl_atr_mult: float = 0.5,
        tp1_rr: float = 1.0,
        tp2_rr: float = 3.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 3,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            fast_ema=fast_ema, slow_ema=slow_ema,
            slope_bars=slope_bars, slope_min_atr=slope_min_atr,
            impulse_lookback=impulse_lookback,
            fib_min=fib_min, fib_max=fib_max,
            atr_period=atr_period,
            sl_atr_mult=sl_atr_mult,
            tp1_rr=tp1_rr, tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
        )
        self._last_signal_iloc: int = -(10**9)
        self.min_history = min_history or max(slow_ema * 3, impulse_lookback * 2, 100)
        # Caches populated by prepare().
        self._atr: pd.Series | None = None
        self._ema_fast: np.ndarray | None = None
        self._ema_slow: np.ndarray | None = None
        self._imp_high: np.ndarray | None = None   # rolling max high over window
        self._imp_low: np.ndarray | None = None    # rolling min low over window
        self._imp_low_for_up: np.ndarray | None = None   # recent low for up-impulse start
        self._imp_high_for_down: np.ndarray | None = None  # recent high for down-impulse start

    # ------------------------------------------------------------------
    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        self._atr = atr(df, period=p["atr_period"])
        self._ema_fast = _ema(close, p["fast_ema"])
        self._ema_slow = _ema(close, p["slow_ema"])

        # Rolling impulse detection. For an uptrend we want the
        # impulse to span from a recent LOW to the most recent HIGH.
        # We approximate that with:
        #   imp_high[i] = max(high) over last L bars      (the top)
        #   imp_low_for_up[i] = min(low) over the window preceding
        #     the iloc of that high.
        # A clean vectorised approximation: imp_low_for_up[i] =
        # min(low) over the last L*1.5 bars. It's close enough and
        # O(N) with sliding_window_view.
        n = len(df)
        L = int(p["impulse_lookback"])
        Lext = max(int(L * 1.5), L + 10)
        self._imp_high = _rolling_max(high, L)
        self._imp_low = _rolling_min(low, L)
        self._imp_low_for_up = _rolling_min(low, Lext)
        self._imp_high_for_down = _rolling_max(high, Lext)

    # ------------------------------------------------------------------
    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        reason: str,
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
            SignalLeg(
                weight=w1, take_profit=float(tp1),
                move_sl_to_on_fill=float(entry), tag="tp1",
            ),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    # ------------------------------------------------------------------
    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history:
            return None
        if n - self._last_signal_iloc < p["cooldown_bars"]:
            return None
        if (
            self._ema_fast is None or self._ema_slow is None or self._atr is None
            or self._imp_high is None or self._imp_low is None
            or self._imp_low_for_up is None or self._imp_high_for_down is None
        ):
            return None

        i = n - 1
        if i >= len(self._ema_fast):
            return None
        atr_val = float(self._atr.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        ema_f = float(self._ema_fast[i])
        ema_s = float(self._ema_slow[i])
        slope_k = int(p["slope_bars"])
        if i - slope_k < 0:
            return None
        slope = float(self._ema_slow[i] - self._ema_slow[i - slope_k])
        slope_floor = p["slope_min_atr"] * atr_val

        last = history.iloc[-1]
        prev = history.iloc[-2]
        o = float(last["open"]); h = float(last["high"])
        lo = float(last["low"]); c = float(last["close"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - lo

        # Uptrend conditions.
        up_trend = ema_f > ema_s and slope > slope_floor
        down_trend = ema_f < ema_s and slope < -slope_floor

        # ---------------- LONG ----------------
        if up_trend:
            # Impulse leg: recent high (top) and a low that precedes it.
            imp_high = float(self._imp_high[i])
            imp_low = float(self._imp_low_for_up[i])
            if imp_high <= imp_low:
                return None
            span = imp_high - imp_low
            if span < 2 * atr_val:  # trivial leg; skip
                return None
            zone_hi = imp_high - p["fib_min"] * span
            zone_lo = imp_high - p["fib_max"] * span
            in_zone = zone_lo <= lo <= zone_hi  # bar dipped into zone
            if not in_zone:
                return None
            # Rejection trigger.
            bullish = (
                c > o
                and lower_wick >= body
                and c > float(prev["close"])
                and c > zone_lo  # closed back above zone low
            )
            if not bullish:
                return None
            entry = c
            sl = zone_lo - p["sl_atr_mult"] * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk,
                reason=f"trend-pullback long zone=[{zone_lo:.2f},{zone_hi:.2f}]",
            )

        # ---------------- SHORT ----------------
        if down_trend:
            imp_low = float(self._imp_low[i])
            imp_high = float(self._imp_high_for_down[i])
            if imp_high <= imp_low:
                return None
            span = imp_high - imp_low
            if span < 2 * atr_val:
                return None
            zone_lo = imp_low + p["fib_min"] * span
            zone_hi = imp_low + p["fib_max"] * span
            in_zone = zone_lo <= h <= zone_hi
            if not in_zone:
                return None
            bearish = (
                c < o
                and upper_wick >= body
                and c < float(prev["close"])
                and c < zone_hi
            )
            if not bearish:
                return None
            entry = c
            sl = zone_hi + p["sl_atr_mult"] * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk,
                reason=f"trend-pullback short zone=[{zone_lo:.2f},{zone_hi:.2f}]",
            )

        return None


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _rolling_max(x: np.ndarray, window: int) -> np.ndarray:
    """Trailing-window max. Value at i uses x[i-window+1..i]."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(x)
    out = np.full(n, np.nan)
    if n < window:
        return out
    v = sliding_window_view(x, window_shape=window).max(axis=1)
    out[window - 1:] = v
    return out


def _rolling_min(x: np.ndarray, window: int) -> np.ndarray:
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(x)
    out = np.full(n, np.nan)
    if n < window:
        return out
    v = sliding_window_view(x, window_shape=window).min(axis=1)
    out[window - 1:] = v
    return out
