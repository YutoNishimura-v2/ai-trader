"""Pure momentum-continuation scalper (no news dependency).

When the M1 chart prints a strong directional bar (range > k * ATR
with body fraction > p), AND the M15 trend agrees, enter a
continuation trade in the bar's direction.

This is the trend-following complement to all our mean-reversion
edges (sweep_reclaim, pivot_bounce, bb_squeeze). The user's
discretionary recipe is fib pullback (with-trend after pullback);
this catches the impulse leg itself rather than waiting for a
pullback that may never come.

Entry:
  - M1 bar range (high - low) >= range_atr_mult * ATR
  - Body fraction (|close - open| / range) >= body_frac_min
  - Bar direction agrees with M15 EMA(slow) > EMA(fast) for shorts
    (or fast > slow for longs)
  - HTF ADX >= min_adx (trend regime, not chop)
  - Bar closes in the upper third of the range (long) or lower third (short)
  - Cooldown to prevent over-firing

SL: structural beyond the bar's opposite extreme + ATR buffer
TP: 2-leg, TP1 = 0.7R + BE on runner, TP2 = 2R
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _adx_series(df: pd.DataFrame, period: int) -> np.ndarray:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)


@register_strategy
class MomentumContinuation(BaseStrategy):
    name = "momentum_continuation"

    def __init__(
        self,
        atr_period: int = 14,
        range_atr_mult: float = 1.2,
        body_frac_min: float = 0.6,
        close_frac_min: float = 0.65,    # close in upper/lower N% of range
        sl_atr_buf: float = 0.40,
        max_sl_atr: float = 2.0,
        tp1_rr: float = 0.7,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 12,
        max_trades_per_day: int = 6,
        # HTF gate.
        htf: str | None = "M15",
        htf_fast_ema: int = 20,
        htf_slow_ema: int = 50,
        htf_adx_period: int = 14,
        min_trend_adx: float = 22.0,
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period,
            range_atr_mult=range_atr_mult,
            body_frac_min=body_frac_min,
            close_frac_min=close_frac_min,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
            htf=htf,
            htf_fast_ema=htf_fast_ema,
            htf_slow_ema=htf_slow_ema,
            htf_adx_period=htf_adx_period,
            min_trend_adx=min_trend_adx,
            session=session,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._mtf: MTFContext | None = None
        self._htf_fast: np.ndarray | None = None
        self._htf_slow: np.ndarray | None = None
        self._htf_adx: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])
        if p.get("htf"):
            self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
            htf_df = self._mtf.frame(p["htf"])
            f = int(p["htf_fast_ema"])
            s = int(p["htf_slow_ema"])
            self._htf_fast = htf_df["close"].ewm(span=f, adjust=False, min_periods=f).mean().to_numpy(copy=True)
            self._htf_slow = htf_df["close"].ewm(span=s, adjust=False, min_periods=s).mean().to_numpy(copy=True)
            self._htf_adx = _adx_series(htf_df, int(p["htf_adx_period"]))
        else:
            self._mtf = None
            self._htf_fast = None
            self._htf_slow = None
            self._htf_adx = None

    def _build_signal(self, side, entry, sl, risk, reason) -> Signal:
        p = self.params
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        tp2 = entry + p["tp2_rr"] * risk if side == SignalSide.BUY else entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        legs = (
            SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
            SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
        )
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None:
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

        last = history.iloc[-1]
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        o = float(last["open"])
        rng = h - l
        if rng < float(p["range_atr_mult"]) * atr_val:
            return None
        body = abs(c - o)
        if body / max(rng, 1e-9) < float(p["body_frac_min"]):
            return None

        # HTF gate: trend direction + strength.
        bias_up = bias_dn = False
        if self._mtf is not None and self._htf_adx is not None and self._htf_fast is not None and self._htf_slow is not None:
            pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if pos is None or pos >= len(self._htf_adx):
                return None
            adx_val = float(self._htf_adx[pos])
            if not np.isfinite(adx_val) or adx_val < float(p["min_trend_adx"]):
                return None
            ef = float(self._htf_fast[pos])
            es = float(self._htf_slow[pos])
            if not (np.isfinite(ef) and np.isfinite(es)):
                return None
            bias_up = ef > es
            bias_dn = ef < es

        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val
        close_frac = (c - l) / max(rng, 1e-9)

        # LONG: bullish bar (close in upper N%) AND M15 bias up.
        if bias_up and c > o and close_frac >= float(p["close_frac_min"]):
            entry = c
            structural_sl = l - sl_buf
            cap_sl = entry - max_sl
            sl = max(structural_sl, cap_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            self._day_trades += 1
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.BUY, entry, sl, risk,
                reason=f"momentum-cont long range={rng:.2f} body={body:.2f}",
            )

        # SHORT: bearish bar (close in lower N%) AND M15 bias down.
        if bias_dn and c < o and (1.0 - close_frac) >= float(p["close_frac_min"]):
            entry = c
            structural_sl = h + sl_buf
            cap_sl = entry + max_sl
            sl = min(structural_sl, cap_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            self._day_trades += 1
            self._last_signal_iloc = n
            return self._build_signal(
                SignalSide.SELL, entry, sl, risk,
                reason=f"momentum-cont short range={rng:.2f} body={body:.2f}",
            )

        return None
