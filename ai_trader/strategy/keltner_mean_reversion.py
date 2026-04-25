"""Keltner channel mean-reversion scalper.

Keltner channels = EMA(period) ± mult * ATR. Unlike Bollinger
bands (which use std-dev of close prices), Keltner uses ATR which
includes the wick range — making it more responsive to volatility
changes and less prone to band-walking false signals.

Entry:
  - LONG: M1 low pierces the lower Keltner band AND closes back
    above the band (rejection wick) AND the EMA mid is rising or flat.
  - SHORT: mirror.

Exit (managed via 2-leg):
  - TP1 at the EMA mid (or +1R, whichever closer).
  - TP2 at +2R or opposite band (whichever closer).
  - SL: structural beyond the wick + ATR buffer, capped by max_sl_atr.

Why it might add edge over BB squeeze:
  - Keltner is naturally adaptive to volatility (ATR-based).
  - Mid-EMA exit is a logical mean-reversion target.
  - The "EMA slope rising or flat" gate skips strong-trend fades.

Per web research: NQ Keltner mean reversion daily PF 1.61 over 548
trades; gold futures 30-min Keltner positive across multi-year
backtests. Worth testing on M1 XAUUSD with iter9 user sizing.
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


@register_strategy
class KeltnerMeanReversion(BaseStrategy):
    name = "keltner_mean_reversion"

    def __init__(
        self,
        ema_period: int = 20,
        atr_period: int = 14,
        kelt_mult: float = 2.0,
        slope_lookback: int = 10,
        slope_max_atr_long: float = 0.20,    # |slope| > this means "strong against" — skip long if slope < -this
        slope_max_atr_short: float = 0.20,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.5,
        tp1_rr: float = 0.6,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 8,
        max_trades_per_day: int = 5,
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            ema_period=ema_period,
            atr_period=atr_period,
            kelt_mult=kelt_mult,
            slope_lookback=slope_lookback,
            slope_max_atr_long=slope_max_atr_long,
            slope_max_atr_short=slope_max_atr_short,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
            session=session,
        )
        self.min_history = min_history or max(ema_period * 3, atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._ema: np.ndarray | None = None
        self._upper: np.ndarray | None = None
        self._lower: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        period = int(p["ema_period"])
        ema = df["close"].ewm(span=period, adjust=False, min_periods=period).mean().to_numpy(copy=True)
        atr_arr = self._atr_cache.to_numpy(copy=True)
        mult = float(p["kelt_mult"])
        self._ema = ema
        self._upper = ema + mult * atr_arr
        self._lower = ema - mult * atr_arr

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._ema is None:
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

        ema = float(self._ema[i])
        upper = float(self._upper[i])
        lower = float(self._lower[i])
        if not (np.isfinite(ema) and np.isfinite(upper) and np.isfinite(lower)):
            return None

        # EMA slope (last slope_lookback bars).
        sb = int(p["slope_lookback"])
        if i < sb:
            return None
        slope = ema - float(self._ema[i - sb])
        slope_thresh_long = float(p["slope_max_atr_long"]) * atr_val
        slope_thresh_short = float(p["slope_max_atr_short"]) * atr_val
        # Skip long if EMA strongly DOWN, skip short if EMA strongly UP.
        long_ok = slope > -slope_thresh_long
        short_ok = slope < slope_thresh_short

        last = history.iloc[-1]
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        o = float(last["open"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val

        # LONG: low pierces lower band, close back above with bullish rejection.
        if long_ok and l <= lower and c > lower and c > o and lower_wick >= body * 0.5:
            entry = c
            structural_sl = l - sl_buf
            cap_sl = entry - max_sl
            sl = max(structural_sl, cap_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            tp1_rr_p = entry + float(p["tp1_rr"]) * risk
            tp2_rr_p = entry + float(p["tp2_rr"]) * risk
            # TP1 at EMA mid OR rr-based (whichever closer).
            tp1 = min(tp1_rr_p, ema) if ema > entry else tp1_rr_p
            # TP2 at upper band OR rr-based (whichever closer).
            tp2 = min(tp2_rr_p, upper) if upper > entry else tp2_rr_p
            w1 = float(p["leg1_weight"])
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.BUY, entry=None, stop_loss=sl, legs=legs,
                reason=f"keltner-rev long lower={lower:.2f} ema={ema:.2f}",
            )

        # SHORT: mirror.
        if short_ok and h >= upper and c < upper and c < o and upper_wick >= body * 0.5:
            entry = c
            structural_sl = h + sl_buf
            cap_sl = entry + max_sl
            sl = min(structural_sl, cap_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            tp1_rr_p = entry - float(p["tp1_rr"]) * risk
            tp2_rr_p = entry - float(p["tp2_rr"]) * risk
            tp1 = max(tp1_rr_p, ema) if ema < entry else tp1_rr_p
            tp2 = max(tp2_rr_p, lower) if lower < entry else tp2_rr_p
            w1 = float(p["leg1_weight"])
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.SELL, entry=None, stop_loss=sl, legs=legs,
                reason=f"keltner-rev short upper={upper:.2f} ema={ema:.2f}",
            )

        return None
