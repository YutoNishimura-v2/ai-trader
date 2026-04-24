"""Broker interface.

Two implementations today: ``PaperBroker`` (simulated, used for
backtests and demos without an MT5 terminal) and ``MT5LiveBroker``
(thin wrapper over the MetaTrader5 Python package, Windows-only).

Both present the same surface so the live runner is backend-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..strategy.base import SignalSide


@dataclass
class Order:
    side: SignalSide
    lots: float
    stop_loss: float
    take_profit: float
    comment: str = ""


@dataclass
class Position:
    id: int
    side: SignalSide
    lots: float
    entry_price: float
    stop_loss: float
    take_profit: float
    open_time: datetime
    comment: str = ""


@dataclass
class OrderResult:
    ok: bool
    position: Optional[Position] = None
    error: str = ""


@dataclass
class ClosedTrade:
    position: Position
    close_price: float
    close_time: datetime
    pnl: float
    reason: str


class Broker(ABC):
    @abstractmethod
    def submit(self, order: Order, *, ref_price: float, now: datetime) -> OrderResult: ...

    @abstractmethod
    def open_positions(self) -> list[Position]: ...

    @abstractmethod
    def close(self, position_id: int, *, price: float, now: datetime, reason: str) -> ClosedTrade: ...
