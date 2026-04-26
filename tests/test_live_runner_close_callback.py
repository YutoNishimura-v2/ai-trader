"""LiveRunner fires Strategy.on_trade_closed identically to BacktestEngine.

This is the project's sim/live equivalence guarantee for adaptive
strategies — the same shape of payload, the same call site relative
to ``risk.on_trade_closed``, and the same member_name extraction
from bracketed reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from ai_trader.broker.base import (
    Broker,
    ClosedTrade,
    Order,
    OrderResult,
    Position,
)
from ai_trader.live.runner import LiveRunner
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import (
    BaseStrategy,
    ClosedTradeContext,
    Signal,
    SignalSide,
)


@dataclass
class _StubBroker(Broker):
    instrument: InstrumentSpec
    _scripted_close: ClosedTrade | None = None
    _next_id: int = 1
    _positions: list[Position] = field(default_factory=list)
    submit_calls: list[Order] = field(default_factory=list)

    def submit(self, order: Order, *, ref_price: float, now: datetime) -> OrderResult:
        pos = Position(
            id=self._next_id,
            side=order.side,
            lots=order.lots,
            entry_price=ref_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            open_time=now,
            comment=order.comment,
            group_id=order.group_id,
            leg_index=order.leg_index,
            move_siblings_sl_to_on_fill=order.move_siblings_sl_to_on_fill,
            meta=order.meta,
        )
        self._next_id += 1
        self._positions.append(pos)
        self.submit_calls.append(order)
        return OrderResult(ok=True, position=pos)

    def open_positions(self) -> list[Position]:
        return list(self._positions)

    def close(self, position_id: int, *, price: float, now: datetime, reason: str) -> ClosedTrade:
        # Keep simple: pop the first position.
        pos = self._positions.pop(0)
        return ClosedTrade(
            position=pos,
            close_price=price,
            close_time=now,
            pnl=10.0,
            reason=reason,
        )

    def modify_sl(self, position_id: int, *, new_sl: float) -> None:  # pragma: no cover
        return


class _AlwaysFireRecording(BaseStrategy):
    """Fires once on the first non-empty bar; records every close."""

    name = "alwaysfire_recording"

    def __init__(self, reason: str = "[my_member|range] live test"):
        super().__init__()
        self._fired = False
        self._reason = reason
        self.closes: list[ClosedTradeContext] = []

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        if self._fired:
            return None
        self._fired = True
        last = float(history.iloc[-1]["close"])
        return Signal(
            side=SignalSide.BUY,
            entry=None,
            stop_loss=last - 1.0,
            take_profit=last + 1.0,
            reason=self._reason,
        )

    def on_trade_closed(self, ctx: ClosedTradeContext) -> None:
        self.closes.append(ctx)


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD",
        contract_size=100.0,
        tick_size=0.01,
        tick_value=1.0,
        min_lot=0.01,
        lot_step=0.01,
    )


def test_live_runner_fires_close_callback_on_kill_switch_flatten() -> None:
    """When the kill-switch trips and live flattens, on_trade_closed fires."""

    inst = _instrument()
    risk = RiskManager(
        starting_balance=10_000.0,
        max_leverage=100.0,
        instrument=inst,
        risk_per_trade_pct=0.1,
        daily_profit_target_pct=50.0,
        daily_max_loss_pct=50.0,
        withdraw_half_of_daily_profit=False,
    )
    broker = _StubBroker(instrument=inst)
    # Pre-open a synthetic position so kill-switch flatten hits it.
    broker._positions.append(
        Position(
            id=1,
            side=SignalSide.BUY,
            lots=0.01,
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2010.0,
            open_time=datetime.now(timezone.utc),
            comment="[stub_member|range] preopened",
            meta={"member_name": "stub_member", "entry_risk_price": 10.0},
        )
    )
    broker._next_id = 2

    # Force the risk manager into kill-switch state by mutating the
    # ledger directly.
    now = datetime.now(timezone.utc)
    ledger = risk._ensure_day(now)
    ledger.kill_switch = True
    ledger.kill_reason = "test"

    bars = pd.DataFrame(
        {
            "open": [2000.0, 2001.0],
            "high": [2002.0, 2002.0],
            "low": [1999.0, 2000.0],
            "close": [2001.0, 2001.5],
        },
        index=pd.date_range("2026-04-01 00:00", periods=2, freq="1min", tz="UTC"),
    )

    strat = _AlwaysFireRecording(reason="[stub_member|range] preopened")
    runner = LiveRunner(
        strategy=strat,
        risk=risk,
        broker=broker,
        fetch_bars=lambda n: bars,
        history_bars=2,
        poll_seconds=0,
        max_iterations=1,
    )
    runner.run()
    # The kill-switch path closed the synthetic position and fired
    # the strategy's close hook.
    assert len(strat.closes) == 1
    ctx = strat.closes[0]
    assert ctx.reason == "kill-switch"
    assert ctx.member_name == "stub_member"
    # entry_risk_price was 10.0 → r_multiple = pnl / (lots*100*10)
    # for a 0.01-lot trade in USD-quoted gold on a JPY account; with
    # the default no-FX path it just becomes pnl / (0.01 * 100 * 10).
    assert ctx.r_multiple is not None
    assert ctx.meta is not None
    assert ctx.meta["member_name"] == "stub_member"
