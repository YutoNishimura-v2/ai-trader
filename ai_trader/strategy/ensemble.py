"""Ensemble wrapper: run N sub-strategies in parallel.

Each sub-strategy keeps its own state; the ensemble asks each in a
fixed priority order and returns the first Signal that any of them
produces. Other sub-strategies on the same bar get a look on the
*next* call (common case: they wouldn't have fired anyway since
their own state machines usually require distinct structural
conditions).

The risk manager's ``max_concurrent_positions`` still caps total
live exposure, and the engine's per-Signal cap still applies per
decision (multi-leg is one decision). So this wrapper is purely
"route bars to whichever sub-strategy wants to act."

Trade-offs by design:

- No second-chance: if strategy A fires on bar t, strategy B's
  potential signal on bar t is dropped. The alternative (queue
  it for bar t+1) is strictly worse: by bar t+1 the price action
  will have moved and B's trigger may no longer be valid.
- Cooldown is per-sub-strategy (inherited from each). Ensemble
  adds no extra cooldown.

The ``reason`` string on emitted signals is prefixed with the
sub-strategy name so trade-log analysis can attribute attribution.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .base import BaseStrategy, Signal
from .registry import get_strategy, register_strategy


@register_strategy
class EnsembleStrategy(BaseStrategy):
    """Ordered ensemble.

    Config shape::

        params:
          members:
            - name: bos_retest_scalper
              params: {...}
            - name: bb_scalper
              params: {...}

    The first member to return a non-None ``Signal`` on a given
    bar wins that bar. Order in ``members`` is the priority order
    (first = highest priority).
    """

    name = "ensemble"

    def __init__(self, members: list[dict[str, Any]] | None = None) -> None:
        super().__init__(members=members or [])
        if not members:
            raise ValueError("EnsembleStrategy needs a non-empty 'members' list")
        self._members: list[BaseStrategy] = []
        self._member_names: list[str] = []
        for m in members:
            nm = m.get("name")
            if not nm:
                raise ValueError(f"ensemble member missing 'name': {m}")
            params = m.get("params", {}) or {}
            self._members.append(get_strategy(nm, **params))
            self._member_names.append(nm)
        # min_history = max across members so every member is warmed.
        self.min_history = max(getattr(m, "min_history", 0) for m in self._members)

    def prepare(self, df: pd.DataFrame) -> None:
        for m in self._members:
            m.prepare(df)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        n = len(history)
        if n < self.min_history:
            return None
        for name, member in zip(self._member_names, self._members):
            sig = member.on_bar(history)
            if sig is not None:
                # Tag the reason with the winning member for attribution.
                tagged_reason = f"[{name}] {sig.reason}"
                # Signal is frozen; rebuild with tagged reason.
                object.__setattr__(sig, "reason", tagged_reason)
                return sig
        return None
