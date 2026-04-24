"""MetaTrader 5 live broker adapter.

The actual ``MetaTrader5`` Python package ships only for Windows, so
this module imports it lazily. Anything that touches the real API is
guarded so the module can still be *imported* on Linux for unit
tests that only need the type signatures.

Enough of the adapter is implemented for a simple 1-position-at-a-
time strategy (which is what the spec requires today). It will need
to grow when strategies B and C are added.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..risk.manager import InstrumentSpec
from ..strategy.base import SignalSide
from .base import Broker, ClosedTrade, Order, OrderResult, Position


def _import_mt5() -> Any:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception as e:  # pragma: no cover — environment dependent
        raise RuntimeError(
            "MetaTrader5 package not available. Install `ai-trader[live]` "
            "on a Windows host with the MT5 terminal installed."
        ) from e
    return mt5


@dataclass
class MT5LiveBroker(Broker):
    instrument: InstrumentSpec
    magic: int = 20260424
    comment: str = "ai-trader"
    deviation_points: int = 10
    login: Optional[int] = None
    server: Optional[str] = None
    password: Optional[str] = None

    _mt5: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    def connect(self) -> None:  # pragma: no cover — requires MT5 runtime
        mt5 = _import_mt5()
        kwargs: dict[str, Any] = {}
        if self.login is not None:
            kwargs["login"] = int(self.login)
        if self.server is not None:
            kwargs["server"] = self.server
        if self.password is not None:
            kwargs["password"] = self.password
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(self.instrument.symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for {self.instrument.symbol}")
        self._mt5 = mt5
        self._connected = True

    def disconnect(self) -> None:  # pragma: no cover
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False

    # ------------------------------------------------------------------
    def submit(self, order: Order, *, ref_price: float, now: datetime) -> OrderResult:  # pragma: no cover
        if not self._connected:
            self.connect()
        mt5 = self._mt5

        order_type = mt5.ORDER_TYPE_BUY if order.side == SignalSide.BUY else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(self.instrument.symbol)
        price = tick.ask if order.side == SignalSide.BUY else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.instrument.symbol,
            "volume": float(order.lots),
            "type": order_type,
            "price": float(price),
            "sl": float(order.stop_loss),
            "tp": float(order.take_profit),
            "deviation": int(self.deviation_points),
            "magic": int(self.magic),
            "comment": order.comment or self.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = mt5.last_error()
            return OrderResult(ok=False, error=f"order_send failed: retcode={getattr(result, 'retcode', None)} err={err}")

        pos = Position(
            id=int(result.order),
            side=order.side,
            lots=float(order.lots),
            entry_price=float(result.price),
            stop_loss=float(order.stop_loss),
            take_profit=float(order.take_profit),
            open_time=now,
            comment=order.comment,
        )
        return OrderResult(ok=True, position=pos)

    def modify_sl(self, position_id: int, *, new_sl: float) -> None:  # pragma: no cover
        if not self._connected:
            self.connect()
        mt5 = self._mt5
        raw = mt5.positions_get(ticket=int(position_id)) or ()
        if not raw:
            return
        p = raw[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.instrument.symbol,
            "position": int(position_id),
            "sl": float(new_sl),
            "tp": float(p.tp),
            "magic": int(self.magic),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"modify_sl failed: retcode={getattr(result, 'retcode', None)} err={mt5.last_error()}"
            )

    def open_positions(self) -> list[Position]:  # pragma: no cover
        if not self._connected:
            self.connect()
        mt5 = self._mt5
        raw = mt5.positions_get(symbol=self.instrument.symbol) or ()
        out: list[Position] = []
        for p in raw:
            if p.magic != self.magic:
                continue
            out.append(
                Position(
                    id=int(p.ticket),
                    side=SignalSide.BUY if p.type == mt5.POSITION_TYPE_BUY else SignalSide.SELL,
                    lots=float(p.volume),
                    entry_price=float(p.price_open),
                    stop_loss=float(p.sl),
                    take_profit=float(p.tp),
                    open_time=datetime.fromtimestamp(p.time),
                    comment=p.comment or "",
                )
            )
        return out

    def close(self, position_id: int, *, price: float, now: datetime, reason: str) -> ClosedTrade:  # pragma: no cover
        if not self._connected:
            self.connect()
        mt5 = self._mt5
        positions = [p for p in (mt5.positions_get(ticket=position_id) or ())]
        if not positions:
            raise RuntimeError(f"position {position_id} not found")
        p = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(self.instrument.symbol)
        exit_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.instrument.symbol,
            "volume": float(p.volume),
            "type": close_type,
            "position": int(position_id),
            "price": float(exit_price),
            "deviation": int(self.deviation_points),
            "magic": int(self.magic),
            "comment": f"close:{reason}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"close failed: retcode={getattr(result, 'retcode', None)} err={mt5.last_error()}")

        pos = Position(
            id=int(position_id),
            side=SignalSide.BUY if p.type == mt5.POSITION_TYPE_BUY else SignalSide.SELL,
            lots=float(p.volume),
            entry_price=float(p.price_open),
            stop_loss=float(p.sl),
            take_profit=float(p.tp),
            open_time=datetime.fromtimestamp(p.time),
            comment=p.comment or "",
        )
        pnl = float(p.profit)
        return ClosedTrade(position=pos, close_price=float(result.price), close_time=now, pnl=pnl, reason=reason)
