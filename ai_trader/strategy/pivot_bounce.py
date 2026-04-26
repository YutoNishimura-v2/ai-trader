"""Daily pivot-point bounce scalper for XAUUSD.

The classic floor-trader pivot levels:
  P  = (PrevH + PrevL + PrevC) / 3
  R1 = 2P - PrevL    S1 = 2P - PrevH
  R2 = P + (PrevH - PrevL)    S2 = P - (PrevH - PrevL)

Institutional desks publish & defend these levels; retail price
action often respects them as bounce targets. The mechanical
edge: when price touches a pivot level, MOST of the time it
reacts (≥1R move) before continuing through.

Strategy:
  - Compute P, S1, R1, S2, R2 from the PREVIOUS UTC trading day's
    OHLC (causal — uses only fully-closed prior days).
  - When the current M1 bar's wick touches S1/R1/S2/R2 + ATR buffer
    AND closes back inside (rejection), enter the bounce.
  - Long bounce on S1/S2; short bounce on R1/R2.
  - SL: a fixed ATR-buffer beyond the pivot.
  - 2-leg: TP1 = midway to next pivot or +1R, TP2 = next pivot or
    +2R (whichever is closer = adaptive).
  - Cooldown to prevent the same wick from firing repeatedly.

Why uncorrelated with existing strategies:
  - Reference levels are EXTERNAL (yesterday's OHLC), not a
    rolling-window feature. Different population of trades than
    fib pullbacks (impulse-based) or Asian-range sweeps
    (session-based).
"""
from __future__ import annotations

from datetime import timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from ..indicators import atr
from .base import BaseStrategy, Signal, SignalLeg, SignalSide
from .registry import register_strategy
from .session import check_session


def _adx_series_for_pivot(df: pd.DataFrame, period: int) -> np.ndarray:
    """Causal ADX. Local helper — duplicates pattern from session_sweep_reclaim
    to avoid cross-strategy import."""
    high = df["high"]; low = df["low"]; close = df["close"]
    up = high.diff(); dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)


@register_strategy
class PivotBounce(BaseStrategy):
    name = "pivot_bounce"

    def __init__(
        self,
        atr_period: int = 14,
        touch_atr_buf: float = 0.10,
        sl_atr_buf: float = 0.30,
        max_sl_atr: float = 2.0,
        tp1_rr: float = 0.8,
        tp2_rr: float = 2.0,
        leg1_weight: float = 0.5,
        cooldown_bars: int = 30,
        session: str | None = "london_or_ny",
        use_s2r2: bool = True,
        max_trades_per_day: int = 4,
        # Optional HTF ADX gate (skip strong-trend regimes for the
        # mean-reversion-style bounce). adx_max=None disables.
        htf: str | None = None,
        adx_period: int = 14,
        adx_max: float | None = None,
        # "daily" (default) — pivots from prior UTC day OHLC.
        # "weekly"          — pivots from prior calendar week OHLC.
        pivot_period: str = "daily",
        # iter28: day-of-week filter (UTC weekday 0=Mon..4=Fri,5=Sat,6=Sun).
        # If set, only trade on these days. None = all days allowed.
        # Set as e.g. [0,2,3] to trade only Mon/Wed/Thu (the strong days
        # discovered in the iter28 dow-profile of v4_extended_a).
        weekdays: list[int] | tuple[int, ...] | None = None,
        # Optional level allowlist. Example: ["S1", "R1"] keeps only the
        # first support/resistance bounces. None = use the historic
        # use_s2r2 behavior unchanged.
        levels: list[str] | tuple[str, ...] | None = None,
        # iter28: hour-of-day blacklist (UTC). Skip these hours entirely
        # even if inside the session window. e.g. [8,13] = avoid worst hours.
        block_hours_utc: list[int] | tuple[int, ...] | None = None,
        # Optional dynamic-risk metadata. Defaults preserve historic
        # behaviour; when set, RiskManager's dynamic_risk layer can size
        # this pivot member without a wrapper strategy.
        risk_multiplier: float | None = None,
        confidence: float | None = None,
        emit_context_meta: bool = False,
        min_history: int | None = None,
    ) -> None:
        super().__init__(
            atr_period=atr_period,
            touch_atr_buf=touch_atr_buf,
            sl_atr_buf=sl_atr_buf,
            max_sl_atr=max_sl_atr,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
            leg1_weight=leg1_weight,
            cooldown_bars=cooldown_bars,
            session=session,
            use_s2r2=use_s2r2,
            max_trades_per_day=max_trades_per_day,
            htf=htf,
            adx_period=adx_period,
            adx_max=adx_max,
            pivot_period=pivot_period,
            weekdays=tuple(weekdays) if weekdays is not None else None,
            levels=tuple(str(x).upper() for x in levels) if levels is not None else None,
            block_hours_utc=tuple(block_hours_utc) if block_hours_utc is not None else None,
            risk_multiplier=risk_multiplier,
            confidence=confidence,
            emit_context_meta=emit_context_meta,
        )
        self.min_history = min_history or max(atr_period * 3, 60)
        self._atr_cache: pd.Series | None = None
        # Per-bar pivot levels precomputed in prepare().
        self._pivots: pd.DataFrame | None = None
        self._mtf: MTFContext | None = None
        self._htf_adx: np.ndarray | None = None
        self._higher_pivots: dict[str, pd.DataFrame] = {}
        self._last_signal_iloc: int = -(10**9)
        self._day_key: str | None = None
        self._day_trades: int = 0
        # Pivots already touched today, by side+level (avoid re-firing on the same touch).
        self._touched: set[str] = set()

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._atr_cache = atr(df, period=p["atr_period"])

        # Pivot period: "daily" (prior UTC day) or "weekly" (prior cal week).
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        df_utc = df.copy()
        df_utc.index = idx

        period = p.get("pivot_period", "daily")
        idx_naive = idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx
        if period == "weekly":
            # Group by Mon-anchored ISO week (Mon..Sun). Convert through
            # tz-naive timestamps first to avoid pandas PeriodIndex
            # timezone-drop warnings while keeping identical UTC buckets.
            buckets = idx_naive.to_period("W-SUN").to_timestamp().tz_localize("UTC")
        elif period == "monthly":
            buckets = idx_naive.to_period("M").to_timestamp().tz_localize("UTC")
        elif period in ("4h", "h4", "H4"):
            # iter28: 4-hour pivots — much faster, fires more often.
            buckets = idx.floor("4h")
        elif period in ("h1", "H1", "1h"):
            buckets = idx.floor("1h")
        else:
            buckets = idx.normalize()
        agg = df_utc.groupby(buckets).agg(
            bk_open=("open", "first"),
            bk_high=("high", "max"),
            bk_low=("low", "min"),
            bk_close=("close", "last"),
        )
        # Shift by one bucket so each row has the PRIOR bucket's OHLC
        # (causal — no peek at current bucket's close).
        prev = agg.shift(1)
        prev["P"] = (prev["bk_high"] + prev["bk_low"] + prev["bk_close"]) / 3.0
        prev["R1"] = 2 * prev["P"] - prev["bk_low"]
        prev["S1"] = 2 * prev["P"] - prev["bk_high"]
        prev["R2"] = prev["P"] + (prev["bk_high"] - prev["bk_low"])
        prev["S2"] = prev["P"] - (prev["bk_high"] - prev["bk_low"])
        # Map back onto each M1 bar via the bar's bucket-key.
        per_bar = prev.loc[buckets, ["P", "R1", "S1", "R2", "S2"]].set_axis(idx)
        self._pivots = per_bar

        self._higher_pivots = {}
        for hp in p.get("min_distance_from_periods") or ():
            hp_buckets = self._bucket_index(idx, str(hp))
            hp_agg = df_utc.groupby(hp_buckets).agg(
                bk_open=("open", "first"),
                bk_high=("high", "max"),
                bk_low=("low", "min"),
                bk_close=("close", "last"),
            )
            hp_prev = hp_agg.shift(1)
            hp_prev["P"] = (hp_prev["bk_high"] + hp_prev["bk_low"] + hp_prev["bk_close"]) / 3.0
            hp_prev["R1"] = 2 * hp_prev["P"] - hp_prev["bk_low"]
            hp_prev["S1"] = 2 * hp_prev["P"] - hp_prev["bk_high"]
            hp_prev["R2"] = hp_prev["P"] + (hp_prev["bk_high"] - hp_prev["bk_low"])
            hp_prev["S2"] = hp_prev["P"] - (hp_prev["bk_high"] - hp_prev["bk_low"])
            self._higher_pivots[str(hp)] = hp_prev.loc[hp_buckets, ["P", "R1", "S1", "R2", "S2"]].set_axis(idx)

        # Optional HTF ADX gate.
        if p.get("htf") and p.get("adx_max") is not None:
            self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
            htf_df = self._mtf.frame(p["htf"])
            self._htf_adx = _adx_series_for_pivot(htf_df, int(p["adx_period"]))
        else:
            self._mtf = None
            self._htf_adx = None

    def _build_signal(
        self,
        side: SignalSide,
        entry: float,
        sl: float,
        risk: float,
        reason: str,
        *,
        level_name: str,
        level_value: float,
        atr_value: float,
        ts_utc,
        htf_adx: float | None = None,
    ) -> Signal:
        p = self.params
        tp1 = entry + p["tp1_rr"] * risk if side == SignalSide.BUY else entry - p["tp1_rr"] * risk
        tp2 = entry + p["tp2_rr"] * risk if side == SignalSide.BUY else entry - p["tp2_rr"] * risk
        w1 = float(p["leg1_weight"])
        # iter28: single-TP case (leg1_weight ≈ 1.0). Only emit one leg.
        if w1 >= 0.999:
            legs = (
                SignalLeg(weight=1.0, take_profit=float(tp1), tag="tp1"),
            )
        elif w1 <= 0.001:
            legs = (
                SignalLeg(weight=1.0, take_profit=float(tp2), tag="tp2"),
            )
        else:
            legs = (
                SignalLeg(weight=w1, take_profit=float(tp1), move_sl_to_on_fill=float(entry), tag="tp1"),
                SignalLeg(weight=1.0 - w1, take_profit=float(tp2), tag="tp2"),
            )
        meta = None
        if p.get("emit_context_meta") or p.get("risk_multiplier") is not None or p.get("confidence") is not None:
            meta = {
                "strategy": self.name,
                "pivot_period": p.get("pivot_period", "daily"),
                "pivot_level": level_name,
                "pivot_level_value": float(level_value),
                "session": p.get("session"),
                "weekday": int(ts_utc.weekday()),
                "hour_utc": int(ts_utc.hour),
                "atr": float(atr_value),
            }
            if htf_adx is not None:
                meta["htf_adx"] = float(htf_adx)
            if p.get("risk_multiplier") is not None:
                meta["risk_multiplier"] = float(p["risk_multiplier"])
            if p.get("confidence") is not None:
                meta["confidence"] = float(p["confidence"])
        return Signal(side=side, entry=None, stop_loss=sl, legs=legs, reason=reason, meta=meta)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        p = self.params
        n = len(history)
        if n < self.min_history or self._atr_cache is None or self._pivots is None:
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
        # iter28: day-of-week filter (UTC weekday 0=Mon).
        wds = p.get("weekdays")
        if wds is not None and ts_utc.weekday() not in wds:
            return None
        # iter28: hour blacklist (UTC).
        bhrs = p.get("block_hours_utc")
        if bhrs is not None and ts_utc.hour in bhrs:
            return None

        day_key = ts_utc.date().isoformat()
        if day_key != self._day_key:
            self._day_key = day_key
            self._day_trades = 0
            self._touched = set()
        if self._day_trades >= int(p["max_trades_per_day"]):
            return None

        i = n - 1
        atr_val = float(self._atr_cache.iloc[i])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None

        # HTF ADX gate (skip strong-trend regimes where pivot bounces fail).
        htf_adx_val: float | None = None
        if self._mtf is not None and self._htf_adx is not None:
            pos = self._mtf.last_closed_idx(p["htf"], ts_utc)
            if pos is None or pos >= len(self._htf_adx):
                return None
            htf_adx_val = float(self._htf_adx[pos])
            if not np.isfinite(htf_adx_val):
                return None
            if htf_adx_val > float(p["adx_max"]):
                return None

        last = history.iloc[-1]
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])
        o = float(last["open"])
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        pv = self._pivots.iloc[i]
        if pv.isna().any():
            return None

        touch_buf = float(p["touch_atr_buf"]) * atr_val
        sl_buf = float(p["sl_atr_buf"]) * atr_val
        max_sl = float(p["max_sl_atr"]) * atr_val

        levels_buy = [("S1", float(pv["S1"]))]
        levels_sell = [("R1", float(pv["R1"]))]
        if p["use_s2r2"]:
            levels_buy.append(("S2", float(pv["S2"])))
            levels_sell.append(("R2", float(pv["R2"])))
        allowed_levels = p.get("levels")
        if allowed_levels is not None:
            allowed = set(allowed_levels)
            levels_buy = [(nm, lvl) for nm, lvl in levels_buy if nm in allowed]
            levels_sell = [(nm, lvl) for nm, lvl in levels_sell if nm in allowed]

        # Long bounce: low touched the support (or pierced) AND
        # close > support, with bullish rejection wick.
        for name, lvl in levels_buy:
            key = f"BUY-{name}"
            if key in self._touched:
                continue
            if l <= lvl + touch_buf and c > lvl:
                bullish = c > o and lower_wick >= body * 0.6
                if not bullish:
                    continue
                entry = c
                structural_sl = l - sl_buf
                cap_sl = entry - max_sl
                sl = max(structural_sl, cap_sl)
                risk = entry - sl
                if risk <= 0:
                    continue
                self._day_trades += 1
                self._touched.add(key)
                self._last_signal_iloc = n
                return self._build_signal(
                    SignalSide.BUY, entry, sl, risk,
                    reason=f"pivot-bounce long @{name}={lvl:.2f}",
                    level_name=name,
                    level_value=lvl,
                    atr_value=atr_val,
                    ts_utc=ts_utc,
                    htf_adx=htf_adx_val,
                )

        for name, lvl in levels_sell:
            key = f"SELL-{name}"
            if key in self._touched:
                continue
            if h >= lvl - touch_buf and c < lvl:
                bearish = c < o and upper_wick >= body * 0.6
                if not bearish:
                    continue
                entry = c
                structural_sl = h + sl_buf
                cap_sl = entry + max_sl
                sl = min(structural_sl, cap_sl)
                risk = sl - entry
                if risk <= 0:
                    continue
                self._day_trades += 1
                self._touched.add(key)
                self._last_signal_iloc = n
                return self._build_signal(
                    SignalSide.SELL, entry, sl, risk,
                    reason=f"pivot-bounce short @{name}={lvl:.2f}",
                    level_name=name,
                    level_value=lvl,
                    atr_value=atr_val,
                    ts_utc=ts_utc,
                    htf_adx=htf_adx_val,
                )

        return None
