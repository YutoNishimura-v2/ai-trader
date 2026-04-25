"""Bollinger-band squeeze reversal scalper (3-confluence).

Inspired by Gold Scalper V4.2:
  (1) Statistical exhaustion via BB extremes (touch outer band).
  (2) Momentum shift confirmation (band starts contracting).
  (3) Macro trend filter on higher timeframe (don't fade strong HTF trend).

Trades only when ALL THREE conditions align — the 3-confluence
requirement is what separates this from naive band-touch fades.

Entry:
  - LONG: M1 low touches BB lower band, AND BB width is contracting
    (today's BB-width < BB-width 5 bars ago = momentum exhaustion),
    AND M15 EMA(50) slope is NOT strongly down (don't fight strong HTF trend).
  - Mirror for SHORT.

SL: structural beyond the wick + ATR buffer, capped by max_sl_atr.
TP: TP1 at midline (BB middle) or +1R, TP2 at opposite band or +2R.
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


@register_strategy
class BbSqueezeReversal(BaseStrategy):
    name = "bb_squeeze_reversal"

    def __init__(
        self,
        bb_period: int = 20,
        bb_mult: float = 2.0,
        contract_lookback: int = 5,
        atr_period: int = 14,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.0,
        tp1_rr: float = 0.6,
        tp2_rr: float = 1.8,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 8,
        max_trades_per_day: int = 5,
        # HTF EMA-slope gate.
        htf: str | None = "M15",
        htf_ema: int = 50,
        htf_slope_max_atr: float = 0.50,   # if |EMA slope| > this*ATR, skip
        session: str | None = "london_or_ny",
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            bb_period=bb_period,
            bb_mult=bb_mult,
            contract_lookback=contract_lookback,
            atr_period=atr_period,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            max_trades_per_day=max_trades_per_day,
            htf=htf,
            htf_ema=htf_ema,
            htf_slope_max_atr=htf_slope_max_atr,
            session=session,
        )
        self.min_history = min_history or max(bb_period * 3, atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        self._bb_mid: np.ndarray | None = None
        self._bb_upper: np.ndarray | None = None
        self._bb_lower: np.ndarray | None = None
        self._bb_width: np.ndarray | None = None
        self._mtf: MTFContext | None = None
        self._htf_ema: np.ndarray | None = None
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])

        period = int(p["bb_period"])
        mult = float(p["bb_mult"])
        c = df["close"]
        mid = c.rolling(period).mean().to_numpy()
        std = c.rolling(period).std(ddof=0).to_numpy()
        upper = mid + mult * std
        lower = mid - mult * std
        self._bb_mid = mid
        self._bb_upper = upper
        self._bb_lower = lower
        self._bb_width = upper - lower

        if p.get("htf") and p.get("htf_ema"):
            self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
            htf_df = self._mtf.frame(p["htf"])
            ema_period = int(p["htf_ema"])
            ema = htf_df["close"].ewm(span=ema_period, adjust=False, min_periods=ema_period).mean().to_numpy(copy=True)
            self._htf_ema = ema
        else:
            self._mtf = None
            self._htf_ema = None

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._bb_mid is None:
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

        mid = float(self._bb_mid[i])
        upper = float(self._bb_upper[i])
        lower = float(self._bb_lower[i])
        if not (np.isfinite(mid) and np.isfinite(upper) and np.isfinite(lower)):
            return None

        # Momentum shift: BB width is contracting (smaller than k bars ago)
        cb = int(p["contract_lookback"])
        if i < cb:
            return None
        width_now = float(self._bb_width[i])
        width_then = float(self._bb_width[i - cb])
        if not (np.isfinite(width_now) and np.isfinite(width_then)):
            return None
        contracting = width_now < width_then

        # HTF EMA-slope filter.
        htf_slope_ok_long = True
        htf_slope_ok_short = True
        if self._mtf is not None and self._htf_ema is not None:
            pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if pos is None or pos < 5 or pos >= len(self._htf_ema):
                return None
            slope = float(self._htf_ema[pos]) - float(self._htf_ema[pos - 5])
            slope_thresh = float(p["htf_slope_max_atr"]) * atr_val
            # Don't fade longs in a strong DOWN trend (slope very negative).
            htf_slope_ok_long = slope > -slope_thresh
            # Don't fade shorts in a strong UP trend (slope very positive).
            htf_slope_ok_short = slope < slope_thresh

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

        # LONG: low touches lower band, close back above, contracting width, HTF not down-strong.
        if (
            htf_slope_ok_long
            and contracting
            and l <= lower
            and c > lower
            and c > o
            and lower_wick >= body * 0.6
        ):
            entry = c
            structural_sl = l - sl_buf
            cap_sl = entry - max_sl
            sl = max(structural_sl, cap_sl)
            risk = entry - sl
            if risk <= 0:
                return None
            tp1 = entry + float(p["tp1_rr"]) * risk
            # TP2 = mid (BB middle) or rr-based, whichever closer
            tp2_rr = entry + float(p["tp2_rr"]) * risk
            tp2 = min(tp2_rr, mid) if mid > entry else tp2_rr
            w1 = float(p["leg1_weight"])
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.BUY, entry=None, stop_loss=sl, legs=legs,
                reason=f"bb-squeeze long band={lower:.2f}",
            )

        # SHORT: mirror.
        if (
            htf_slope_ok_short
            and contracting
            and h >= upper
            and c < upper
            and c < o
            and upper_wick >= body * 0.6
        ):
            entry = c
            structural_sl = h + sl_buf
            cap_sl = entry + max_sl
            sl = min(structural_sl, cap_sl)
            risk = sl - entry
            if risk <= 0:
                return None
            tp1 = entry - float(p["tp1_rr"]) * risk
            tp2_rr = entry - float(p["tp2_rr"]) * risk
            tp2 = max(tp2_rr, mid) if mid < entry else tp2_rr
            w1 = float(p["leg1_weight"])
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
            self._day_trades += 1
            self._last_signal_iloc = n
            return Signal(
                side=SignalSide.SELL, entry=None, stop_loss=sl, legs=legs,
                reason=f"bb-squeeze short band={upper:.2f}",
            )

        return None
