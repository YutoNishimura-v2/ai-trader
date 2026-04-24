"""Event-driven backtest loop.

One iteration per bar. Ordering per bar:

1. Broker checks SL/TP against the *new* bar's OHLC. Any closed
   trades are booked into the risk manager (drives equity + daily
   kill-switch) and, if the closed trade was leg N of a multi-leg
   group with a ``move_siblings_sl_to_on_fill`` price, siblings are
   moved to that SL (break-even).
2. If the risk manager's kill-switch is ON, close any remaining
   positions at the bar open and skip signal generation for the
   rest of the day.
3. Otherwise feed history up to & including this bar to the
   strategy. If it returns a Signal, risk-check + size + submit
   one order per leg (lot size scaled by leg weight).

We do NOT allow intrabar entries: the signal fires on bar close and
fills at the *next* bar's open. This is the standard "no look-ahead"
discipline.
"""
from __future__ import annotations

import math
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
    group_id: int | None = None
    leg_index: int | None = None


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
        self._next_group_id = 1

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

            # 1) fill any pending signal at this bar's open, one
            #    order per leg.
            if pending_signal is not None:
                self._submit_signal(pending_signal, bar_open=float(bar["open"]), now=now, ts=ts)
                pending_signal = None

            # 2) stop/tp check against this bar's range.
            for closed in self.broker.check_stops(
                bar_high=float(bar["high"]),
                bar_low=float(bar["low"]),
                now=now,
            ):
                pnl_account = _to_account(self.risk, closed.pnl)
                self.risk.on_trade_closed(pnl_account, when=now)
                trades.append(
                    ClosedTradeRecord(
                        open_time=closed.position.open_time,
                        close_time=closed.close_time,
                        side=closed.position.side.value,
                        lots=closed.position.lots,
                        entry=closed.position.entry_price,
                        exit=closed.close_price,
                        pnl=pnl_account,
                        reason=closed.reason,
                        group_id=closed.position.group_id,
                        leg_index=closed.position.leg_index,
                    )
                )

                # Break-even: if this leg had a sibling-SL-move instruction
                # and it closed on TP, move siblings' SL to the requested
                # price.
                if (
                    closed.reason == "tp"
                    and closed.position.group_id is not None
                    and closed.position.move_siblings_sl_to_on_fill is not None
                ):
                    new_sl = closed.position.move_siblings_sl_to_on_fill
                    for sibling in self.broker.open_positions():
                        if sibling.group_id == closed.position.group_id and sibling.id != closed.position.id:
                            self.broker.modify_sl(sibling.id, new_sl=new_sl)
                            self._log(
                                f"{ts} break-even: moved pos {sibling.id} SL to {new_sl:.5f}"
                            )

            # 3) kill-switch: if the day is done, flatten and skip strat.
            ledger = self.risk._ensure_day(now)
            if ledger.kill_switch:
                for pos in list(self.broker.open_positions()):
                    closed = self.broker.close(
                        pos.id,
                        price=float(bar["close"]),
                        now=now,
                        reason="kill-switch",
                    )
                    pnl_account = _to_account(self.risk, closed.pnl)
                    self.risk.on_trade_closed(pnl_account, when=now)
                    trades.append(
                        ClosedTradeRecord(
                            open_time=closed.position.open_time,
                            close_time=closed.close_time,
                            side=closed.position.side.value,
                            lots=closed.position.lots,
                            entry=closed.position.entry_price,
                            exit=closed.close_price,
                            pnl=pnl_account,
                            reason=closed.reason,
                            group_id=closed.position.group_id,
                            leg_index=closed.position.leg_index,
                        )
                    )
                equity_times.append(ts)
                equity_values.append(self.risk.balance)
                continue

            # 4) ask the strategy (history up to and including this bar).
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
            pnl_account = _to_account(self.risk, closed.pnl)
            self.risk.on_trade_closed(pnl_account, when=last_ts)
            trades.append(
                ClosedTradeRecord(
                    open_time=closed.position.open_time,
                    close_time=closed.close_time,
                    side=closed.position.side.value,
                    lots=closed.position.lots,
                    entry=closed.position.entry_price,
                    exit=closed.close_price,
                    pnl=pnl_account,
                    reason=closed.reason,
                    group_id=closed.position.group_id,
                    leg_index=closed.position.leg_index,
                )
            )

        equity_curve = pd.Series(
            equity_values, index=pd.DatetimeIndex(equity_times), name="equity"
        )
        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            final_balance=self.risk.balance,
            withdrawn_total=self.risk.withdrawn_total,
        )

    # ------------------------------------------------------------------
    def _submit_signal(self, sig: Signal, *, bar_open: float, now: datetime, ts: pd.Timestamp) -> None:
        # Count *distinct entry decisions* that are still open, not
        # individual legs. Two legs from the same Signal share a
        # group_id and count as one conceptual position.
        open_groups = {
            p.group_id for p in self.broker.open_positions() if p.group_id is not None
        }
        ungrouped = sum(1 for p in self.broker.open_positions() if p.group_id is None)
        open_decisions = len(open_groups) + ungrouped
        decision = self.risk.evaluate(
            sig,
            ref_price=bar_open,
            open_positions=open_decisions,
            now=now,
        )
        if not decision.approved:
            self._log(f"{ts} signal rejected: {decision.reason}")
            return

        group_id = self._next_group_id
        self._next_group_id += 1

        step = self.broker.instrument.lot_step
        min_lot = self.broker.instrument.min_lot

        # Compute per-leg lots, rounded down to lot_step, with leftover
        # allocated to the largest-weight leg to avoid completely
        # dropping small legs.
        per_leg = [decision.lots * leg.weight for leg in sig.legs]
        rounded = [_floor_step(x, step) for x in per_leg]

        # If any leg rounds to zero, fall back: give the whole decision
        # to the first leg as a single-leg fill. This keeps the trade
        # alive but preserves correctness (risk manager already
        # approved the total lot size).
        if any(r < min_lot for r in rounded):
            self._log(
                f"{ts} multi-leg sizing collapsed to single leg "
                f"(per-leg lots: {per_leg}); using leg 0 only"
            )
            rounded = [_floor_step(decision.lots, step)] + [0.0] * (len(sig.legs) - 1)

        leaked = decision.lots - sum(rounded)
        if leaked >= step and rounded:
            idx_max = max(range(len(rounded)), key=lambda i: rounded[i])
            rounded[idx_max] = _floor_step(rounded[idx_max] + leaked, step)

        legs_opened = 0
        for idx, (leg, lots) in enumerate(zip(sig.legs, rounded)):
            if lots < min_lot:
                continue
            order = Order(
                side=sig.side,
                lots=lots,
                stop_loss=sig.stop_loss,
                take_profit=leg.take_profit,
                comment=f"{sig.reason} | leg{idx}:{leg.tag}" if leg.tag else sig.reason,
                group_id=group_id,
                leg_index=idx,
                move_siblings_sl_to_on_fill=leg.move_sl_to_on_fill,
            )
            res = self.broker.submit(order, ref_price=bar_open, now=now)
            if not res.ok:
                self._log(f"{ts} submit failed (leg {idx}): {res.error}")
            else:
                legs_opened += 1

        if legs_opened == 0:
            self._log(f"{ts} no legs opened from signal {sig.reason}")

    def _unrealized(self, price: float) -> float:
        """Open-P&L in the account currency."""
        total_quote = 0.0
        for pos in self.broker.open_positions():
            diff = price - pos.entry_price
            if pos.side == SignalSide.SELL:
                diff = -diff
            ticks = diff / self.broker.instrument.tick_size
            total_quote += ticks * self.broker.instrument.tick_value * pos.lots
        return _to_account(self.risk, total_quote)


def _floor_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step


def _to_account(risk: RiskManager, amount_quote: float) -> float:
    """Convert a quote-currency amount to the account currency."""
    if risk.instrument.quote_currency == risk.account_currency or risk.fx is None:
        return amount_quote
    return risk.fx.convert(
        amount_quote, risk.instrument.quote_currency, risk.account_currency
    )
