"""Simulated broker for backtesting and offline demos.

Applies:
  - spread (half added on buy, half subtracted on sell at entry;
    mirrored on exit);
  - slippage (fixed number of ticks, worst-case direction);
  - commission per lot per side (symmetric).

The backtest engine drives the broker bar by bar, telling it the
current bar's OHLC so it can check whether SL/TP was hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from ..risk.manager import InstrumentSpec
from ..strategy.base import SignalSide
from .base import Broker, ClosedTrade, Order, OrderResult, Position


@dataclass
class PaperBroker(Broker):
    instrument: InstrumentSpec
    spread_points: int = 20
    slippage_points: int = 2
    commission_per_lot: float = 0.0

    _next_id: int = field(default=1, init=False)
    _positions: dict[int, Position] = field(default_factory=dict, init=False)

    # ---------- helpers ----------
    def _fill_price(self, side: SignalSide, ref: float) -> float:
        tick = self.instrument.tick_size
        half_spread = (self.spread_points / 2.0) * tick
        slip = self.slippage_points * tick
        if side == SignalSide.BUY:
            return ref + half_spread + slip
        return ref - half_spread - slip

    def _exit_price(self, side: SignalSide, ref: float) -> float:
        tick = self.instrument.tick_size
        half_spread = (self.spread_points / 2.0) * tick
        slip = self.slippage_points * tick
        if side == SignalSide.BUY:
            # Closing a long = selling back.
            return ref - half_spread - slip
        return ref + half_spread + slip

    def _pnl(self, pos: Position, close_price: float) -> float:
        diff = close_price - pos.entry_price
        if pos.side == SignalSide.SELL:
            diff = -diff
        ticks = diff / self.instrument.tick_size
        gross = ticks * self.instrument.tick_value * pos.lots
        commission = self.commission_per_lot * pos.lots * 2.0
        return gross - commission

    # ---------- Broker API ----------
    def submit(self, order: Order, *, ref_price: float, now: datetime) -> OrderResult:
        fill = self._fill_price(order.side, ref_price)
        pos = Position(
            id=self._next_id,
            side=order.side,
            lots=order.lots,
            entry_price=fill,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            open_time=now,
            comment=order.comment,
        )
        self._positions[pos.id] = pos
        self._next_id += 1
        return OrderResult(ok=True, position=pos)

    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    def close(self, position_id: int, *, price: float, now: datetime, reason: str) -> ClosedTrade:
        pos = self._positions.pop(position_id)
        exit_px = self._exit_price(pos.side, price)
        # If the caller is claiming a SL/TP hit, honor the stop/tp exactly:
        if reason == "sl":
            exit_px = pos.stop_loss
        elif reason == "tp":
            exit_px = pos.take_profit
        pnl = self._pnl(pos, exit_px)
        return ClosedTrade(position=pos, close_price=exit_px, close_time=now, pnl=pnl, reason=reason)

    # ---------- driven by the backtest engine ----------
    def check_stops(self, bar_high: float, bar_low: float, now: datetime) -> Iterable[ClosedTrade]:
        """Return any positions that should be closed by this bar.

        Conservative rule: if a bar could plausibly hit SL OR TP and
        we can't tell which came first, assume SL (worst case).
        """
        closed: list[ClosedTrade] = []
        for pid in list(self._positions.keys()):
            pos = self._positions[pid]
            if pos.side == SignalSide.BUY:
                hit_sl = bar_low <= pos.stop_loss
                hit_tp = bar_high >= pos.take_profit
            else:
                hit_sl = bar_high >= pos.stop_loss
                hit_tp = bar_low <= pos.take_profit

            if hit_sl and hit_tp:
                reason = "sl"
            elif hit_sl:
                reason = "sl"
            elif hit_tp:
                reason = "tp"
            else:
                continue
            closed.append(self.close(pid, price=pos.stop_loss if reason == "sl" else pos.take_profit, now=now, reason=reason))
        return closed
