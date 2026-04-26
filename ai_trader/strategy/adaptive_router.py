"""Iter30 adaptive router strategy.

The project's prior "adaptive" simulator (``scripts/iter29_adaptive_sim.py``)
was a post-hoc daily-return mixer: it picked from precomputed expert
return streams. That cannot run live — in live, the strategy doesn't
have access to the closed-trade P&L of strategies it never deployed.
This module replaces that with an **in-engine causal adaptive
router** that wraps a member roster, gates each member by a causal
HTF ADX regime, and weights each member by its decayed realised
expectancy in R-multiples. State updates only on closed trades, via
the new :meth:`BaseStrategy.on_trade_closed` hook, so the very same
control loop runs identically in :class:`BacktestEngine` and
:class:`LiveRunner`.

Decision model (every ``on_bar``):

1. Determine HTF regime: ``range`` / ``transition`` / ``trend``.
2. Walk members in **adaptive priority order** (decayed expectancy
   desc; ``active`` outranks ``probe``; ties broken by config order).
3. Skip any member whose configured regimes don't include the
   current regime.
4. Ask each surviving member for a Signal. First non-None wins.
5. Attach ``risk_multiplier`` and ``confidence`` derived from the
   member's expectancy state and the regime confidence prior. The
   :class:`RiskManager` (with ``dynamic_risk_enabled: true``) sizes
   accordingly.

Eligibility hysteresis:

- New / unwarmed members start in ``probe`` with
  ``probe_risk_multiplier`` exposure (default 0.20). The probe path
  is the project's "keep a small probe trade alive so the regime
  shift is detectable" mechanism — explicitly not zero.
- A member's ``probe`` flips to ``active`` when its decayed
  expectancy reaches ``eligibility_on_threshold``. ``active`` flips
  back to ``probe`` only when it falls below
  ``eligibility_off_threshold`` (always strictly less than the on
  threshold). This blocks whipsaw between probe and active on
  borderline expectancies.

Causality is enforced at two levels:

- ``on_bar`` only reads expectancy state mutated by *prior* bars'
  close events. The engine fires :meth:`on_trade_closed` AFTER
  ``on_bar`` for the bar where the close fell, so the very next
  ``on_bar`` is the first one allowed to see the updated state.
  This is locked down by ``test_adaptive_router_causality`` below.
- The HTF ADX is computed via :class:`MTFContext.last_closed_idx`
  so a still-forming HTF candle is never visible.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any

import numpy as np
import pandas as pd

from ..data.mtf import MTFContext
from .base import BaseStrategy, ClosedTradeContext, Signal
from .registry import get_strategy, register_strategy


def _adx(df: pd.DataFrame, period: int) -> np.ndarray:
    """Standard causal Wilder-ADX on a pre-prepared HTF OHLC frame.

    Mirrors :func:`ai_trader.strategy.regime_router._adx`; duplicated
    locally so that adaptive_router doesn't reach into another
    strategy module's private symbols.
    """
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


@dataclass
class _MemberSlot:
    """Wrapper around a member strategy with adaptive bookkeeping."""

    name: str
    member_id: str  # Unique within the router (allows multiple of same name).
    strategy: BaseStrategy
    regimes: frozenset[str]
    config_priority: int
    state: str = "probe"   # "probe" | "active"
    # Optional per-member intrinsic sizing scalar applied AFTER the
    # router's probe/active multiplier. A "protector" member can be
    # configured with risk_multiplier=0.35 here so that the router
    # never inflates its size beyond what the offline tuning judged
    # safe, regardless of the active/probe state.
    member_base_risk_multiplier: float = 1.0
    samples: deque = field(default_factory=lambda: deque(maxlen=64))


def _decayed_expectancy(samples: list[float], halflife: float) -> float:
    """Decayed mean of a list of R-multiples, newest sample weighted 1.0.

    Older samples are weighted by ``0.5 ** (age / halflife)``. With
    ``halflife=10`` a 10-trade-old sample contributes half as much as
    the most recent one. ``len(samples) == 0`` returns 0.0.
    """
    if not samples:
        return 0.0
    n = len(samples)
    weights = np.array(
        [0.5 ** ((n - 1 - i) / max(halflife, 1e-6)) for i in range(n)],
        dtype=float,
    )
    arr = np.asarray(samples, dtype=float)
    return float((arr * weights).sum() / weights.sum())


@register_strategy
class AdaptiveRouterStrategy(BaseStrategy):
    """Regime-gated adaptive router with causal expectancy weighting."""

    name = "adaptive_router"

    def __init__(
        self,
        members: list[dict[str, Any]] | None = None,
        *,
        htf: str = "M15",
        adx_period: int = 14,
        range_adx_max: float = 20.0,
        trend_adx_min: float = 25.0,
        default_regimes: tuple[str, ...] = ("range", "transition", "trend"),
        # Expectancy machinery.
        expectancy_window: int = 30,
        expectancy_decay_halflife: float = 10.0,
        eligibility_on_threshold: float = 0.05,
        eligibility_off_threshold: float = -0.10,
        probe_risk_multiplier: float = 0.20,
        active_risk_multiplier_floor: float = 0.20,
        active_risk_multiplier_cap: float = 1.00,
        # Initial state: "probe" (default) starts every member at
        # probe_risk_multiplier and waits for evidence before scaling.
        # "active" trusts the prior research and starts every member
        # at full active sizing, demoting only on loss evidence. The
        # latter is closer to a live "warm-handoff" deployment where
        # we trust the offline-validated config until proven wrong.
        initial_state: str = "probe",
        # Confidence.
        regime_confidence: dict[str, float] | None = None,
        adx_confidence_weight: float = 0.5,
        # Priority mode: "expectancy" sorts active members by decayed
        # expectancy desc each bar (the original adaptive design);
        # "config" preserves the YAML order, treating expectancy
        # only as a sizing knob, never a tie-breaker. The latter is
        # closer to the iter29 ensemble priority semantics that the
        # member roster was tuned around.
        priority_mode: str = "expectancy",
    ) -> None:
        super().__init__(
            members=members or [],
            htf=htf,
            adx_period=adx_period,
            range_adx_max=range_adx_max,
            trend_adx_min=trend_adx_min,
            default_regimes=default_regimes,
            expectancy_window=expectancy_window,
            expectancy_decay_halflife=expectancy_decay_halflife,
            eligibility_on_threshold=eligibility_on_threshold,
            eligibility_off_threshold=eligibility_off_threshold,
            probe_risk_multiplier=probe_risk_multiplier,
            active_risk_multiplier_floor=active_risk_multiplier_floor,
            active_risk_multiplier_cap=active_risk_multiplier_cap,
            initial_state=initial_state,
            regime_confidence=regime_confidence or {},
            adx_confidence_weight=adx_confidence_weight,
            priority_mode=priority_mode,
        )
        if not members:
            raise ValueError("AdaptiveRouterStrategy needs a non-empty 'members' list")
        if eligibility_off_threshold >= eligibility_on_threshold:
            raise ValueError(
                "eligibility_off_threshold must be strictly less than "
                "eligibility_on_threshold (hysteresis requirement)"
            )
        if initial_state not in ("probe", "active"):
            raise ValueError(
                f"initial_state must be 'probe' or 'active', got {initial_state!r}"
            )
        if priority_mode not in ("expectancy", "config"):
            raise ValueError(
                f"priority_mode must be 'expectancy' or 'config', got {priority_mode!r}"
            )

        self._members: list[_MemberSlot] = []
        seen_ids: set[str] = set()
        for idx, m in enumerate(members):
            nm = m.get("name")
            if not nm:
                raise ValueError(f"adaptive_router member missing 'name': {m}")
            params = m.get("params", {}) or {}
            regimes = frozenset(m.get("regimes", default_regimes) or default_regimes)
            unknown = regimes - {"range", "transition", "trend", "all"}
            if unknown:
                raise ValueError(f"unknown regimes for {nm}: {sorted(unknown)}")
            # Allow multiple members of the same strategy via
            # explicit 'id' key, defaulting to "<name>#<idx>".
            mid = m.get("id") or f"{nm}#{idx}"
            if mid in seen_ids:
                raise ValueError(f"duplicate member id: {mid}")
            seen_ids.add(mid)
            base_mult = float(m.get("risk_multiplier", 1.0))
            self._members.append(
                _MemberSlot(
                    name=nm,
                    member_id=mid,
                    strategy=get_strategy(nm, **params),
                    regimes=regimes,
                    config_priority=idx,
                    state=initial_state,
                    member_base_risk_multiplier=base_mult,
                    samples=deque(maxlen=int(expectancy_window)),
                )
            )

        self.min_history = max(getattr(s.strategy, "min_history", 0) for s in self._members)
        self._mtf: MTFContext | None = None
        self._adx_arr: np.ndarray | None = None
        self._last_adx: float | None = None

    # ------------------------------------------------------------------
    def prepare(self, df: pd.DataFrame) -> None:
        p = self.params
        self._mtf = MTFContext(base=df, timeframes=[p["htf"]])
        htf_df = self._mtf.frame(p["htf"])
        self._adx_arr = _adx(htf_df, int(p["adx_period"]))
        for slot in self._members:
            slot.strategy.prepare(df)

    # ------------------------------------------------------------------
    def _regime(self, ts) -> str | None:
        p = self.params
        if self._mtf is None or self._adx_arr is None:
            return None
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        pos = self._mtf.last_closed_idx(p["htf"], ts_dt)
        if pos is None or pos >= len(self._adx_arr):
            return None
        val = float(self._adx_arr[pos])
        if not np.isfinite(val):
            return None
        self._last_adx = val
        if val <= float(p["range_adx_max"]):
            return "range"
        if val >= float(p["trend_adx_min"]):
            return "trend"
        return "transition"

    def _decayed_expectancy(self, slot: _MemberSlot) -> float:
        return _decayed_expectancy(
            list(slot.samples), float(self.params["expectancy_decay_halflife"])
        )

    def _confidence(self, regime: str) -> float:
        p = self.params
        base = {
            "range": 0.60,
            "transition": 0.45,
            "trend": 0.65,
        }
        base.update(p.get("regime_confidence") or {})
        confidence = float(base.get(regime, 0.5))
        adx = self._last_adx
        if adx is not None:
            adx_norm = min(1.0, max(0.0, adx / 50.0))
            w = min(1.0, max(0.0, float(p.get("adx_confidence_weight", 0.5))))
            confidence = confidence * (1.0 - w) + adx_norm * w
        return float(min(1.0, max(0.0, confidence)))

    def _risk_multiplier(self, slot: _MemberSlot) -> float:
        p = self.params
        # Members may declare an intrinsic risk_multiplier in their
        # config (e.g., a "protector" member sized at 0.35x). This
        # base scalar is multiplied into both the probe and active
        # multipliers, so a protector member never gets unintended
        # full risk just because the router said "active".
        member_base = float(slot.member_base_risk_multiplier)
        if slot.state == "probe":
            return float(p["probe_risk_multiplier"]) * member_base
        # Active: scale by decayed expectancy in [floor, cap].
        exp = max(0.0, self._decayed_expectancy(slot))
        # Map expectancy 0 → floor, expectancy 0.5R → cap, linear.
        floor = float(p["active_risk_multiplier_floor"])
        cap = float(p["active_risk_multiplier_cap"])
        ref = 0.5
        scaled = floor + (cap - floor) * min(exp / ref, 1.0)
        return float(min(cap, max(floor, scaled))) * member_base

    # ------------------------------------------------------------------
    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        n = len(history)
        if n < self.min_history:
            return None
        regime = self._regime(history.index[-1])
        if regime is None:
            return None

        eligible: list[_MemberSlot] = [
            slot for slot in self._members
            if "all" in slot.regimes or regime in slot.regimes
        ]
        if not eligible:
            return None

        # Adaptive priority: active members first, then probe members
        # (the "active outranks probe" rule). Within each group, sort
        # by expectancy desc OR by config order, depending on
        # priority_mode.
        active_slots = [s for s in eligible if s.state == "active"]
        probe_slots = [s for s in eligible if s.state == "probe"]
        if self.params.get("priority_mode", "expectancy") == "expectancy":
            active_slots.sort(
                key=lambda s: (-self._decayed_expectancy(s), s.config_priority)
            )
        else:
            active_slots.sort(key=lambda s: s.config_priority)
        probe_slots.sort(key=lambda s: s.config_priority)
        ordered = active_slots + probe_slots

        for slot in ordered:
            sig = slot.strategy.on_bar(history)
            if sig is None:
                continue
            mult = self._risk_multiplier(slot)
            confidence = self._confidence(regime)
            meta = dict(sig.meta or {})
            meta.setdefault("strategy", slot.name)
            meta["member_name"] = slot.member_id
            meta["regime"] = regime
            meta["state"] = slot.state
            meta["expectancy"] = self._decayed_expectancy(slot)
            meta["risk_multiplier"] = float(mult)
            meta["confidence"] = float(confidence)
            if self._last_adx is not None:
                meta["regime_adx"] = float(self._last_adx)
            object.__setattr__(sig, "meta", meta)
            tagged = (
                f"[{slot.member_id}|{regime}|{slot.state}] {sig.reason}"
            )
            object.__setattr__(sig, "reason", tagged)
            return sig
        return None

    # ------------------------------------------------------------------
    def on_trade_closed(self, ctx: ClosedTradeContext) -> None:
        # Find the originating slot. ctx.member_name was set at signal
        # time to slot.member_id, so we look up by that.
        member_id = ctx.member_name
        if member_id is None:
            return
        slot = next((s for s in self._members if s.member_id == member_id), None)
        if slot is None:
            return
        # Use r_multiple if present; otherwise fall back to sign(pnl)
        # so we always have *some* causal signal.
        if ctx.r_multiple is not None and np.isfinite(ctx.r_multiple):
            sample = float(ctx.r_multiple)
        else:
            sample = float(np.sign(ctx.pnl))
        slot.samples.append(sample)
        # Update probe ↔ active flag with hysteresis.
        exp = self._decayed_expectancy(slot)
        on_th = float(self.params["eligibility_on_threshold"])
        off_th = float(self.params["eligibility_off_threshold"])
        if slot.state == "probe" and exp >= on_th:
            slot.state = "active"
        elif slot.state == "active" and exp < off_th:
            slot.state = "probe"

    # ------------------------------------------------------------------
    # Test/diagnostic accessors (not part of the strategy contract).
    def _slot_state(self, member_id: str) -> str | None:
        for s in self._members:
            if s.member_id == member_id:
                return s.state
        return None

    def _slot_expectancy(self, member_id: str) -> float | None:
        for s in self._members:
            if s.member_id == member_id:
                return self._decayed_expectancy(s)
        return None
