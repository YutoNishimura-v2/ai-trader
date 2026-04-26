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
from ..news.calendar import NewsCalendar, NoNewsCalendar
from ..risk.manager import RiskManager
from ..strategy.base import BaseStrategy, ClosedTradeContext, Signal, SignalSide


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
    comment: str = ""
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
        news: NewsCalendar | None = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.strategy = strategy
        self.risk = risk
        self.broker = broker
        self.news = news or NoNewsCalendar()
        self._log = log or (lambda _msg: None)
        self._next_group_id = 1

    def run(self, df: pd.DataFrame) -> BacktestResult:
        if len(df) < 2:
            raise ValueError("need at least 2 bars")

        # Backtest-only optimisation: let the strategy precompute
        # full-series indicators once. Causal (no future peek) by
        # contract. Not called in live mode.
        self.strategy.prepare(df)

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
            bar_closes = list(self.broker.check_stops(
                bar_high=float(bar["high"]),
                bar_low=float(bar["low"]),
                now=now,
            ))
            for closed in bar_closes:
                pnl_account = _to_account(self.risk, closed.pnl)
                self._book_close(closed, pnl_account=pnl_account, now=now, trades=trades)

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

            # 2a) Intra-bar kill-switch handler.
            #
            # If any of the just-booked closes pushed the day's realized
            # P&L past the -10% / +30% envelope, the risk manager's
            # kill-switch is now set. Without this block, other open
            # positions would sit exposed until the NEXT bar's
            # kill-switch flush — meaning the day's realized loss can
            # easily exceed the -10% cap (unrealized losses become
            # realized at the next bar's close). Flattening siblings
            # immediately at the current bar's close enforces the cap
            # as tightly as bar granularity allows.
            ledger_now = self.risk._ensure_day(now)
            if ledger_now.kill_switch and bar_closes:
                for pos in list(self.broker.open_positions()):
                    c2 = self.broker.close(
                        pos.id,
                        price=float(bar["close"]),
                        now=now,
                        reason="kill-switch",
                    )
                    pnl_account = _to_account(self.risk, c2.pnl)
                    self._book_close(c2, pnl_account=pnl_account, now=now, trades=trades)

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
                    self._book_close(closed, pnl_account=pnl_account, now=now, trades=trades)
                equity_times.append(ts)
                equity_values.append(self.risk.balance + self.risk.withdrawn_total)
                continue

            # 4) ask the strategy (history up to and including this bar).
            history = df.iloc[: i + 1]
            sig = self.strategy.on_bar(history)
            if sig is not None:
                pending_signal = sig

            equity_times.append(ts)
            # Total-account equity: trading balance + unrealized P&L +
            # withdrawn_total. Without +withdrawn_total the half-profit
            # sweep (§A.9) would look like a drawdown even though it's
            # a ledger transfer the user asked for.
            equity_values.append(
                self.risk.balance
                + self._unrealized(float(bar["close"]))
                + self.risk.withdrawn_total
            )

        # Flatten any positions still open at end-of-test at the last close.
        last_ts = df.index[-1].to_pydatetime()
        last_close = float(df.iloc[-1]["close"])
        for pos in list(self.broker.open_positions()):
            closed = self.broker.close(pos.id, price=last_close, now=last_ts, reason="eod")
            pnl_account = _to_account(self.risk, closed.pnl)
            self._book_close(closed, pnl_account=pnl_account, now=last_ts, trades=trades)

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
    def _book_close(
        self,
        closed,
        *,
        pnl_account: float,
        now: datetime,
        trades: list[ClosedTradeRecord],
    ) -> None:
        """Centralized close-event handler.

        Books the close in the risk manager, appends the trade record,
        and fires :meth:`BaseStrategy.on_trade_closed` so adaptive
        strategies (e.g. ``adaptive_router``) can update their causal
        state. The same call site fires in
        :class:`ai_trader.live.runner.LiveRunner` to keep simulation
        and live behaviorally identical.
        """
        self.risk.on_trade_closed(pnl_account, when=now, reason=closed.reason)
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
                comment=closed.position.comment,
                group_id=closed.position.group_id,
                leg_index=closed.position.leg_index,
            )
        )
        meta = dict(closed.position.meta) if closed.position.meta else None
        member_name = (meta or {}).get("member_name") or _member_name_from_reason(
            closed.position.comment
        )
        entry_risk_price = (meta or {}).get("entry_risk_price")
        # FX factor used by the engine for this account.
        if (
            self.risk.instrument.quote_currency != self.risk.account_currency
            and self.risk.fx is not None
        ):
            fx_factor = self.risk.fx.convert(
                1.0,
                self.risk.instrument.quote_currency,
                self.risk.account_currency,
            )
        else:
            fx_factor = 1.0
        r_mult = _r_multiple(
            pnl=pnl_account,
            lots=float(closed.position.lots),
            contract_size=float(self.broker.instrument.contract_size),
            entry_risk_price=(
                float(entry_risk_price) if entry_risk_price is not None else None
            ),
            fx_to_account=float(fx_factor),
        )
        ctx = ClosedTradeContext(
            member_name=member_name,
            pnl=float(pnl_account),
            r_multiple=r_mult,
            entry_time=closed.position.open_time,
            close_time=closed.close_time,
            reason=closed.reason,
            comment=closed.position.comment,
            meta=meta,
        )
        try:
            self.strategy.on_trade_closed(ctx)
        except Exception as exc:  # pragma: no cover -- defensive
            self._log(f"strategy.on_trade_closed raised: {exc!r}")

    # ------------------------------------------------------------------
    def _submit_signal(self, sig: Signal, *, bar_open: float, now: datetime, ts: pd.Timestamp) -> None:
        # News blackout (plan v3 §A.7).
        event = self.news.in_blackout(self.broker.instrument.symbol, now)
        if event is not None:
            self._log(f"{ts} signal skipped: news blackout '{event.event}'")
            return

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
            # Attach a copy of the originating Signal.meta to the
            # broker order so close events can be attributed back.
            # Also enrich with the per-leg risk price distance so
            # adaptive routers can compute R-multiples on close.
            order_meta: dict[str, Any] = dict(sig.meta) if sig.meta else {}
            if "entry_risk_price" not in order_meta:
                order_meta["entry_risk_price"] = abs(bar_open - sig.stop_loss)
            order_meta.setdefault("strategy_reason", sig.reason)
            # _member_name follows the EnsembleStrategy / RegimeRouter
            # convention of prefixing the reason with ``[name] ...``.
            mn = _member_name_from_reason(sig.reason)
            if mn is not None:
                order_meta.setdefault("member_name", mn)
            order = Order(
                side=sig.side,
                lots=lots,
                stop_loss=sig.stop_loss,
                take_profit=leg.take_profit,
                comment=f"{sig.reason} | leg{idx}:{leg.tag}" if leg.tag else sig.reason,
                group_id=group_id,
                leg_index=idx,
                move_siblings_sl_to_on_fill=leg.move_sl_to_on_fill,
                meta=order_meta,
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


def _member_name_from_reason(reason: str) -> str | None:
    """Extract the member name from an EnsembleStrategy-tagged reason.

    Both :class:`EnsembleStrategy` and :class:`RegimeRouterStrategy`
    prefix the Signal.reason with ``[<name>]`` (regime router uses
    ``[<name>|<regime>]``). Anything before a ``|`` inside the
    leading bracket is the member name.
    """
    if not reason or not reason.startswith("["):
        return None
    end = reason.find("]")
    if end < 0:
        return None
    inside = reason[1:end]
    if "|" in inside:
        inside = inside.split("|", 1)[0]
    return inside.strip() or None


def _r_multiple(
    *,
    pnl: float,
    lots: float,
    contract_size: float,
    entry_risk_price: float | None,
    fx_to_account: float,
) -> float | None:
    """Convert a closed P&L into an R-multiple, or None if unknown.

    The denominator is ``lots × contract_size × entry_risk_price``
    (the risk in quote currency at entry), converted to account
    currency via the same FX factor the engine used for ``pnl``.
    """
    if entry_risk_price is None or entry_risk_price <= 0:
        return None
    if lots <= 0 or contract_size <= 0:
        return None
    risk_account = lots * contract_size * entry_risk_price * fx_to_account
    if risk_account <= 0:
        return None
    return float(pnl) / float(risk_account)
