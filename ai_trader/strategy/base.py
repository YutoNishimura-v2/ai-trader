"""Strategy interface.

A strategy is a pure function of the OHLCV history seen so far. It
returns zero or one ``Signal`` per call. It does NOT know about the
account, open positions, or the broker. Sizing and order routing are
the risk manager's and broker's job respectively.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd


class SignalSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Signal:
    """Intent to open a single position.

    - ``entry`` is a limit price if not None; otherwise market.
    - ``stop_loss`` is mandatory (the spec forbids unbounded risk).
    - ``take_profit`` is mandatory. If the strategy has no structural
      TP, compute it from an R:R multiple of the SL distance.
    """

    side: SignalSide
    entry: float | None
    stop_loss: float
    take_profit: float
    reason: str = ""
    meta: dict[str, Any] | None = None


class BaseStrategy(ABC):
    name: str = "base"

    def __init__(self, **params: Any) -> None:
        self.params = params

    @abstractmethod
    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        """Called after every completed bar.

        ``history`` is the full OHLCV frame up to and including the
        just-closed bar. Strategies must not peek at future bars.
        """
