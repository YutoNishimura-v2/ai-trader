"""Risk manager.

Enforces every non-negotiable rule from `docs/plan.md §3`:

- leverage cap (hard)
- daily profit target + daily max loss (kill-switch)
- half-of-daily-profit withdrawal (end-of-day sweep)
- risk-% sizing bounded by leverage and broker lot step
- no concurrent positions beyond the configured max

The risk manager is the ONLY component allowed to turn a `Signal`
into sizing + a go/no-go decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from ..strategy.base import Signal


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    contract_size: float       # units per 1 lot (XAUUSD: 100 oz)
    tick_size: float           # smallest price increment (0.01)
    tick_value: float          # USD pnl per tick per 1 lot
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0


@dataclass
class RiskDecision:
    approved: bool
    lots: float = 0.0
    reason: str = ""


@dataclass
class DailyLedger:
    day: date
    realized_pnl: float = 0.0
    starting_equity: float = 0.0
    withdrawn_today: float = 0.0
    kill_switch: bool = False
    kill_reason: str = ""


@dataclass
class RiskManager:
    starting_balance: float
    max_leverage: float
    instrument: InstrumentSpec
    risk_per_trade_pct: float = 0.5
    daily_profit_target_pct: float = 2.0
    daily_max_loss_pct: float = 1.5
    withdraw_half_of_daily_profit: bool = True
    max_concurrent_positions: int = 1

    balance: float = field(init=False)
    withdrawn_total: float = 0.0
    _ledger: Optional[DailyLedger] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.balance = float(self.starting_balance)

    # ------------------------------------------------------------------
    # day boundary
    # ------------------------------------------------------------------
    def _ensure_day(self, now: datetime) -> DailyLedger:
        today = now.astimezone(timezone.utc).date()
        if self._ledger is None or self._ledger.day != today:
            # End of previous day: sweep half of the realized profit.
            if self._ledger is not None and self.withdraw_half_of_daily_profit:
                profit = self._ledger.realized_pnl
                if profit > 0:
                    sweep = profit * 0.5
                    self.balance -= sweep
                    self.withdrawn_total += sweep
                    self._ledger.withdrawn_today = sweep
            self._ledger = DailyLedger(day=today, starting_equity=self.balance)
        return self._ledger

    # ------------------------------------------------------------------
    # P&L notifications
    # ------------------------------------------------------------------
    def on_trade_closed(self, pnl: float, when: datetime) -> None:
        ledger = self._ensure_day(when)
        self.balance += pnl
        ledger.realized_pnl += pnl
        self._check_kill_switch(ledger)

    def _check_kill_switch(self, ledger: DailyLedger) -> None:
        if ledger.starting_equity <= 0:
            return
        pct = (ledger.realized_pnl / ledger.starting_equity) * 100.0
        if pct >= self.daily_profit_target_pct:
            ledger.kill_switch = True
            ledger.kill_reason = f"daily profit target hit ({pct:.2f}%)"
        elif pct <= -self.daily_max_loss_pct:
            ledger.kill_switch = True
            ledger.kill_reason = f"daily max loss hit ({pct:.2f}%)"

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def evaluate(
        self,
        signal: Signal,
        *,
        ref_price: float,
        open_positions: int,
        now: datetime,
    ) -> RiskDecision:
        ledger = self._ensure_day(now)

        if ledger.kill_switch:
            return RiskDecision(False, 0.0, f"kill-switch: {ledger.kill_reason}")

        if open_positions >= self.max_concurrent_positions:
            return RiskDecision(False, 0.0, "max concurrent positions reached")

        sl_distance = abs(ref_price - signal.stop_loss)
        if sl_distance <= 0:
            return RiskDecision(False, 0.0, "invalid SL distance")

        # 1) Risk-% sizing.
        risk_usd = self.balance * (self.risk_per_trade_pct / 100.0)
        ticks = sl_distance / self.instrument.tick_size
        if ticks <= 0:
            return RiskDecision(False, 0.0, "zero tick distance")
        lots_by_risk = risk_usd / (ticks * self.instrument.tick_value)

        # 2) Leverage cap.
        #    notional = lots * contract_size * price
        #    we need notional <= balance * max_leverage.
        max_notional = self.balance * self.max_leverage
        lots_by_leverage = max_notional / (self.instrument.contract_size * ref_price)

        lots = min(lots_by_risk, lots_by_leverage, self.instrument.max_lot)
        lots = _round_to_step(lots, self.instrument.lot_step)

        if lots < self.instrument.min_lot:
            return RiskDecision(
                False,
                0.0,
                f"sized below min lot ({lots:.4f} < {self.instrument.min_lot})",
            )

        return RiskDecision(True, lots, "ok")


def _round_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step
