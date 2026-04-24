"""Event-driven backtest loop.

One iteration per bar. Ordering per bar:

1. Broker checks SL/TP against the *new* bar's OHLC. Any closed
   trades are booked into the risk manager (drives equity + daily
   kill-switch).
2. If the risk manager's kill-switch is ON, close any remaining
   positions at the bar open and skip signal generation for the
   rest of the day.
3. Otherwise feed history up to & including this bar to the
   strategy. If it returns a Signal, risk-check + size + submit.

We do NOT allow intrabar entries: the signal fires on bar close and
fills at the *next* bar's open. This is the standard "no look-ahead"
discipline and prevents strategies from cheating with OHLC they
haven't seen yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd

from ..broker.base import Order
from ..broker.paper import PaperBroker
from ..risk.manager import RiskManager
from ..strategy.base import BaseStrategy, Signal, SignalSide


@dataclass
class ClosedTradeRecord:
    open_time: datetime
    close_time: datetime
    side: str
    lots: float
    entry: float
    exit: float
    pnl: float
    reason: str


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[ClosedTradeRecord]
    final_balance: float
    withdrawn_total: float
    config: dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    def __init__(
        self,
        strategy: BaseStrategy,
        risk: RiskManager,
        broker: PaperBroker,
        *,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.strategy = strategy
        self.risk = risk
        self.broker = broker
        self._log = log or (lambda _msg: None)

    def run(self, df: pd.DataFrame) -> BacktestResult:
        if len(df) < 2:
            raise ValueError("need at least 2 bars")

        trades: list[ClosedTradeRecord] = []
        equity_times: list[pd.Timestamp] = []
        equity_values: list[float] = []

        pending_signal: Optional[Signal] = None

        for i in range(len(df)):
            bar = df.iloc[i]
            ts = df.index[i]
            now: datetime = ts.to_pydatetime()

            # 1) fill any pending signal at this bar's open.
            if pending_signal is not None:
                sig = pending_signal
                pending_signal = None
                decision = self.risk.evaluate(
                    sig,
                    ref_price=float(bar["open"]),
                    open_positions=len(self.broker.open_positions()),
                    now=now,
                )
                if decision.approved:
                    order = Order(
                        side=sig.side,
                        lots=decision.lots,
                        stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit,
                        comment=sig.reason,
                    )
                    res = self.broker.submit(order, ref_price=float(bar["open"]), now=now)
                    if not res.ok:
                        self._log(f"{ts} submit failed: {res.error}")
                else:
                    self._log(f"{ts} signal rejected: {decision.reason}")

            # 2) stop/tp check against this bar's range.
            for closed in self.broker.check_stops(
                bar_high=float(bar["high"]),
                bar_low=float(bar["low"]),
                now=now,
            ):
                self.risk.on_trade_closed(closed.pnl, when=now)
                trades.append(
                    ClosedTradeRecord(
                        open_time=closed.position.open_time,
                        close_time=closed.close_time,
                        side=closed.position.side.value,
                        lots=closed.position.lots,
                        entry=closed.position.entry_price,
                        exit=closed.close_price,
                        pnl=closed.pnl,
                        reason=closed.reason,
                    )
                )

            # 3) kill-switch: if the day is done, flatten and skip strat.
            ledger = self.risk._ensure_day(now)  # safe: only reads state
            if ledger.kill_switch:
                for pos in list(self.broker.open_positions()):
                    closed = self.broker.close(
                        pos.id,
                        price=float(bar["close"]),
                        now=now,
                        reason="kill-switch",
                    )
                    self.risk.on_trade_closed(closed.pnl, when=now)
                    trades.append(
                        ClosedTradeRecord(
                            open_time=closed.position.open_time,
                            close_time=closed.close_time,
                            side=closed.position.side.value,
                            lots=closed.position.lots,
                            entry=closed.position.entry_price,
                            exit=closed.close_price,
                            pnl=closed.pnl,
                            reason=closed.reason,
                        )
                    )
                equity_times.append(ts)
                equity_values.append(self.risk.balance)
                continue

            # 4) ask the strategy (uses history up to AND including this bar).
            history = df.iloc[: i + 1]
            sig = self.strategy.on_bar(history)
            if sig is not None:
                pending_signal = sig

            equity_times.append(ts)
            equity_values.append(self.risk.balance + self._unrealized(float(bar["close"])))

        # Flatten any positions still open at end-of-test at the last close.
        last_ts = df.index[-1].to_pydatetime()
        last_close = float(df.iloc[-1]["close"])
        for pos in list(self.broker.open_positions()):
            closed = self.broker.close(pos.id, price=last_close, now=last_ts, reason="eod")
            self.risk.on_trade_closed(closed.pnl, when=last_ts)
            trades.append(
                ClosedTradeRecord(
                    open_time=closed.position.open_time,
                    close_time=closed.close_time,
                    side=closed.position.side.value,
                    lots=closed.position.lots,
                    entry=closed.position.entry_price,
                    exit=closed.close_price,
                    pnl=closed.pnl,
                    reason=closed.reason,
                )
            )

        equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_times), name="equity")
        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            final_balance=self.risk.balance,
            withdrawn_total=self.risk.withdrawn_total,
        )

    def _unrealized(self, price: float) -> float:
        total = 0.0
        for pos in self.broker.open_positions():
            diff = price - pos.entry_price
            if pos.side == SignalSide.SELL:
                diff = -diff
            ticks = diff / self.broker.instrument.tick_size
            total += ticks * self.broker.instrument.tick_value * pos.lots
        return total
