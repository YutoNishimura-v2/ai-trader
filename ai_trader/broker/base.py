"""Broker interface.

Two implementations: ``PaperBroker`` (simulated, used for backtests
and demos without an MT5 terminal) and ``MT5LiveBroker`` (thin
wrapper over the MetaTrader5 Python package, Windows-only).

Both present the same surface so the live runner is backend-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    # Correlate multiple legs opened from the same Signal so the
    # engine can implement break-even on TP1 fill.
    group_id: int | None = None
    leg_index: int | None = None
    # Optional: when this leg's TP fills, the engine will call
    # ``broker.modify_sl`` on all siblings in the same group to
    # move them to this price.
    move_siblings_sl_to_on_fill: float | None = None


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
    group_id: int | None = None
    leg_index: int | None = None
    move_siblings_sl_to_on_fill: float | None = None


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
    def close(
        self,
        position_id: int,
        *,
        price: float,
        now: datetime,
        reason: str,
    ) -> ClosedTrade: ...

    @abstractmethod
    def modify_sl(self, position_id: int, *, new_sl: float) -> None: ...
