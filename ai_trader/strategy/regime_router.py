"""Regime-aware strategy router.

The GOLD-only research loop repeatedly found that strategies can look
excellent in one regime and collapse in the next. This wrapper makes
that hypothesis testable without changing the member strategies:

- compute a causal higher-timeframe ADX regime (range / transition /
  trend) using only fully closed HTF bars;
- ask only the members whose configured ``regimes`` include the
  current regime;
- preserve the ensemble priority semantics: the first eligible member
  that fires wins the bar.

Config shape::

    params:
      htf: M15
      adx_period: 14
      range_adx_max: 20
      trend_adx_min: 25
      members:
        - name: session_sweep_reclaim
          regimes: [range, transition]
          params: {...}
        - name: mtf_zigzag_bos
          regimes: [trend]
          params: {...}

Regime calculation is done in ``prepare`` on the HTF frame. ``on_bar``
uses ``MTFContext.last_closed_idx`` so a still-forming M15/H1 candle is
never visible to an M1 signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from .base import BaseStrategy, Signal
from .registry import get_strategy, register_strategy


@dataclass(frozen=True)
class _RoutedMember:
    name: str
    strategy: BaseStrategy
    regimes: frozenset[str]


def _adx(df: pd.DataFrame, period: int) -> np.ndarray:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = (
        100
        * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / atr
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / atr
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)


@register_strategy
class RegimeRouterStrategy(BaseStrategy):
    name = "regime_router"

    def __init__(
        self,
        members: list[dict[str, Any]] | None = None,
        htf: str = "M15",
        adx_period: int = 14,
        range_adx_max: float = 20.0,
        trend_adx_min: float = 25.0,
        default_regimes: tuple[str, ...] = ("range", "transition", "trend"),
        regime_risk_multipliers: dict[str, float] | None = None,
        member_risk_multipliers: dict[str, float] | None = None,
        regime_confidence: dict[str, float] | None = None,
        adx_confidence_weight: float = 0.5,
    ) -> None:
        super().__init__(
            members=members or [],
            htf=htf,
            adx_period=adx_period,
            range_adx_max=range_adx_max,
            trend_adx_min=trend_adx_min,
            default_regimes=default_regimes,
            regime_risk_multipliers=regime_risk_multipliers or {},
            member_risk_multipliers=member_risk_multipliers or {},
            regime_confidence=regime_confidence or {},
            adx_confidence_weight=adx_confidence_weight,
        )
        if not members:
            raise ValueError("RegimeRouterStrategy needs a non-empty 'members' list")
        routed: list[_RoutedMember] = []
        for m in members:
            nm = m.get("name")
            if not nm:
                raise ValueError(f"regime router member missing 'name': {m}")
            params = m.get("params", {}) or {}
            regimes = frozenset(m.get("regimes", default_regimes) or default_regimes)
            unknown = regimes - {"range", "transition", "trend", "all"}
            if unknown:
                raise ValueError(f"unknown regimes for {nm}: {sorted(unknown)}")
            routed.append(_RoutedMember(nm, get_strategy(nm, **params), regimes))
        self._members = routed
        self.min_history = max(getattr(m.strategy, "min_history", 0) for m in self._members)
        self._mtf: MTFContext | None = None
        self._adx: np.ndarray | None = None
        self._last_adx: float | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        self._adx = _adx(htf_df, int(p["adx_period"]))
        for m in self._members:
            m.strategy.prepare(df)

    def _regime(self, ts) -> str | None:
        p = self.params
        if self._mtf is None or self._adx is None:
            return None
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        pos = self._mtf.last_closed_idx(p["htf"], ts_dt)
        if pos is None or pos >= len(self._adx):
            return None
        val = float(self._adx[pos])
        if not np.isfinite(val):
            return None
        self._last_adx = val
        if val <= float(p["range_adx_max"]):
            return "range"
        if val >= float(p["trend_adx_min"]):
            return "trend"
        return "transition"

    def _risk_meta(self, member_name: str, regime: str) -> dict[str, float]:
        p = self.params
        r_mult = {
            "range": 1.00,
            "transition": 0.85,
            "trend": 1.10,
        }
        r_mult.update(p.get("regime_risk_multipliers") or {})
        m_mult = p.get("member_risk_multipliers") or {}
        regime_mult = float(r_mult.get(regime, 1.0))
        member_mult = float(m_mult.get(member_name, 1.0))
        risk_multiplier = max(0.1, regime_mult * member_mult)

        base_conf = {
            "range": 0.60,
            "transition": 0.45,
            "trend": 0.65,
        }
        base_conf.update(p.get("regime_confidence") or {})
        confidence = float(base_conf.get(regime, 0.5))
        adx = self._last_adx
        if adx is not None:
            # Normalize ADX into 0..1 and blend with regime prior confidence.
            adx_norm = min(1.0, max(0.0, adx / 50.0))
            w = min(1.0, max(0.0, float(p.get("adx_confidence_weight", 0.5))))
            confidence = confidence * (1.0 - w) + adx_norm * w
        confidence = min(1.0, max(0.0, confidence))
        return {
            "risk_multiplier": risk_multiplier,
            "confidence": confidence,
        }

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        n = len(history)
        if n < self.min_history:
            return None
        regime = self._regime(history.index[-1])
        if regime is None:
            return None
        for member in self._members:
            if "all" not in member.regimes and regime not in member.regimes:
                continue
            sig = member.strategy.on_bar(history)
            if sig is not None:
                object.__setattr__(sig, "reason", f"[{member.name}|{regime}] {sig.reason}")
                meta = dict(sig.meta or {})
                meta.update(self._risk_meta(member.name, regime))
                meta["regime"] = regime
                meta["router_member"] = member.name
                if self._last_adx is not None:
                    meta["regime_adx"] = float(self._last_adx)
                object.__setattr__(sig, "meta", meta)
                return sig
        return None
