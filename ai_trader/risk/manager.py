"""Risk manager.

Enforces every user constraint from `docs/plan.md §A`:

- leverage cap (§A.1)
- per-instrument position cap scaled by balance (§A.2)
- daily envelope: +30 % / -10 % on combined realized P&L (§A.3)
- half-of-daily-profit withdrawal hint (§A.9)
- risk-% sizing bounded by all caps + broker lot step
- entry-decision concurrency limit

Balance is in ACCOUNT currency (JPY for HFM Katana). An
``FXConverter`` is required whenever an instrument's
``quote_currency`` differs from the account currency.

The risk manager is the ONLY component allowed to turn a `Signal`
into sizing + a go/no-go decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from ..strategy.base import Signal
from .fx import FXConverter, FixedFX


@dataclass(frozen=True)
class InstrumentSpec:
    """Static spec of a tradable instrument.

    ``tick_value`` is always expressed in the instrument's **quote
    currency** (USD for XAUUSD/BTCUSD). The account may be denominated
    in a different currency (e.g. JPY). ``quote_currency`` + an
    ``FXConverter`` are what let the risk manager and PaperBroker
    correctly express P&L in account-currency.
    """
    symbol: str
    contract_size: float       # units per 1 lot (XAUUSD: 100 oz)
    tick_size: float           # smallest price increment (0.01)
    tick_value: float          # PnL per tick per 1 lot, in QUOTE currency
    quote_currency: str = "USD"
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0
    is_24_7: bool = False      # BTCUSD = True; XAUUSD = False


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
    # Plan v3 §A.2: lot cap scales with balance. With ``lot_cap_per_unit
    # = 0.1 / 100_000`` and ``lot_cap_unit_balance = 1.0`` (JPY), a
    # ¥100k balance gives a 0.1-lot cap, ¥1M a 1.0-lot cap, and so on.
    # Setting lot_cap_per_unit_balance <= 0 disables this cap.
    lot_cap_per_unit_balance: float = 0.0
    # Account currency (JPY for HFM Katana). If the instrument's quote
    # currency differs, an FXConverter is required.
    account_currency: str = "USD"
    fx: FXConverter | None = None

    balance: float = field(init=False)
    withdrawn_total: float = 0.0
    _ledger: Optional[DailyLedger] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.balance = float(self.starting_balance)
        if (
            self.instrument.quote_currency != self.account_currency
            and self.fx is None
        ):
            raise ValueError(
                f"instrument quote_currency={self.instrument.quote_currency} "
                f"differs from account_currency={self.account_currency}; "
                "an FXConverter is required."
            )

    def tick_value_account(self, ref_price: float | None = None) -> float:
        """``tick_value`` expressed in the account currency."""
        tv = self.instrument.tick_value
        if self.instrument.quote_currency == self.account_currency:
            return tv
        assert self.fx is not None
        return self.fx.convert(tv, self.instrument.quote_currency, self.account_currency)

    def notional_account(self, lots: float, ref_price: float) -> float:
        """Lot notional expressed in the account currency."""
        notional_quote = lots * self.instrument.contract_size * ref_price
        if self.instrument.quote_currency == self.account_currency:
            return notional_quote
        assert self.fx is not None
        return self.fx.convert(
            notional_quote, self.instrument.quote_currency, self.account_currency
        )

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

        # All maths below are in the ACCOUNT currency.

        # 1) Risk-% sizing.
        risk_budget = self.balance * (self.risk_per_trade_pct / 100.0)
        ticks = sl_distance / self.instrument.tick_size
        if ticks <= 0:
            return RiskDecision(False, 0.0, "zero tick distance")
        tv_account = self.tick_value_account(ref_price)
        lots_by_risk = risk_budget / (ticks * tv_account)

        # 2) Leverage cap: notional_account <= balance * max_leverage.
        max_notional = self.balance * self.max_leverage
        notional_per_lot = self.notional_account(1.0, ref_price)
        lots_by_leverage = max_notional / notional_per_lot if notional_per_lot > 0 else float("inf")

        # 3) Plan v3 §A.2 per-instrument position cap scaled by balance.
        if self.lot_cap_per_unit_balance > 0:
            lots_by_v3_cap = self.balance * self.lot_cap_per_unit_balance
        else:
            lots_by_v3_cap = float("inf")

        lots = min(lots_by_risk, lots_by_leverage, lots_by_v3_cap, self.instrument.max_lot)
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
