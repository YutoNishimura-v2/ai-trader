"""Strategy interface.

A strategy is a pure function of the OHLCV history seen so far. It
returns zero or one ``Signal`` per call. It does NOT know about the
account, open positions, or the broker. Sizing and order routing are
the risk manager's and broker's job respectively.

Per plan v3 §A.5, a single entry decision may open up to 2 sub-legs
sharing the same entry and initial SL but having distinct TPs.
On TP1 fill, the SL on the remaining leg(s) can be moved to break-
even (or another offset) automatically.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class SignalSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class SignalLeg:
    """One sub-position inside a multi-leg Signal.

    - ``weight`` is the fraction of the risk-sized total lot size
      that goes to this leg. Weights across a Signal's legs must sum
      to 1.0 (validated in ``Signal.__post_init__``).
    - ``take_profit`` is absolute price, in the same units as
      ``Signal.stop_loss``.
    - ``move_sl_to_on_fill`` (optional) is the absolute SL price
      that should be applied to the *other* still-open legs of the
      same Signal when this leg's TP fills. Typical use: set it on
      leg 1 (the runner) to the entry price so that when TP1 fills,
      the remaining leg(s) move to break-even.
    """

    weight: float
    take_profit: float
    move_sl_to_on_fill: float | None = None
    tag: str = ""


@dataclass(frozen=True)
class Signal:
    """Intent to open a position. May be single- or multi-leg.

    - ``entry`` is a limit price if not None; otherwise market.
    - ``stop_loss`` is the initial SL shared by all legs.
    - ``legs`` is 1..2 sub-positions. If omitted, a default single
      full-weight leg is constructed from ``take_profit``.
    - ``take_profit`` is a convenience for single-leg signals.
    """

    side: SignalSide
    entry: float | None
    stop_loss: float
    take_profit: float | None = None
    legs: tuple[SignalLeg, ...] = ()
    reason: str = ""
    meta: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.legs:
            if self.take_profit is None:
                raise ValueError("Signal needs either legs=... or take_profit=...")
            object.__setattr__(
                self,
                "legs",
                (SignalLeg(weight=1.0, take_profit=float(self.take_profit), tag="tp"),),
            )
        if not 1 <= len(self.legs) <= 2:
            raise ValueError("Signal supports 1 or 2 legs (plan v3 §A.5)")
        total_w = sum(l.weight for l in self.legs)
        if abs(total_w - 1.0) > 1e-6:
            raise ValueError(f"Signal leg weights must sum to 1.0 (got {total_w:.4f})")
        for leg in self.legs:
            if leg.weight <= 0:
                raise ValueError("leg weight must be positive")
        # Order legs so the closer-TP leg is first (leg 0 = TP1).
        # Always keep tuple sorted by distance-from-entry-SL so the engine
        # can reliably call the first leg "TP1".
        ordered = sorted(
            self.legs,
            key=lambda l: abs(l.take_profit - self.stop_loss),
        )
        object.__setattr__(self, "legs", tuple(ordered))


class BaseStrategy(ABC):
    name: str = "base"

    def __init__(self, **params: Any) -> None:
        self.params = params

    def prepare(self, df: pd.DataFrame) -> None:
        """Optional backtest-only optimisation hook.

        For backtests the engine knows the full OHLCV up front and
        can hand it to the strategy once for precomputation of
        per-bar indicator series that would otherwise be recomputed
        on every ``on_bar`` call.

        Important contract: the strategy must still NOT peek into
        the future. ``prepare`` may compute full-series indicators
        (which are causal: value at row ``i`` depends only on rows
        ``<= i``), and ``on_bar`` may only read entries up to and
        including the current row index.

        The engine calls ``prepare`` once before the bar loop in
        backtest mode and does NOT call it in live mode (where no
        such lookahead exists). Default: no-op.
        """
        return

    @abstractmethod
    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        """Called after every completed bar.

        ``history`` is the full OHLCV frame up to and including the
        just-closed bar. Strategies must not peek at future bars.
        """
