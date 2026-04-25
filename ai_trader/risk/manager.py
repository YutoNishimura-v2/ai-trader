"""Risk manager.

Enforces every user constraint from `docs/plan.md §A`:

- leverage cap (§A.1)
- per-instrument position cap scaled by balance (§A.2)
- daily envelope: +30 % / -10 % on combined realized P&L (§A.3)
- half-of-daily-profit withdrawal hint (§A.9)
- risk-% sizing bounded by all caps + broker lot step
- optional dynamic risk scaling from signal conviction + drawdown throttle
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

from ..state.store import BotState, StateStore
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
    # Optional persistent-state store. When supplied, the daily
    # ledger, kill-switch, and consecutive-SL counter survive
    # restarts (plan v3 §A.8).
    state_store: StateStore | None = None
    # Optional dynamic sizing layer:
    # - ``signal.meta["risk_multiplier"]`` scales risk directly.
    # - ``signal.meta["confidence"]`` (0..1) maps to
    #   [confidence_risk_floor, confidence_risk_ceiling].
    # - a drawdown throttle reduces risk when account value is below
    #   peak to avoid ruin spirals during bad streaks.
    dynamic_risk_enabled: bool = False
    min_risk_per_trade_pct: float | None = None
    max_risk_per_trade_pct: float | None = None
    confidence_risk_floor: float = 0.75
    confidence_risk_ceiling: float = 1.50
    drawdown_soft_limit_pct: float = 12.0
    drawdown_hard_limit_pct: float = 25.0
    drawdown_soft_multiplier: float = 0.70
    drawdown_hard_multiplier: float = 0.40

    balance: float = field(init=False)
    withdrawn_total: float = 0.0
    _ledger: Optional[DailyLedger] = field(default=None, init=False, repr=False)
    _state: BotState = field(default_factory=BotState, init=False, repr=False)
    consecutive_sl: int = field(default=0, init=False)
    _peak_account_value: float = field(default=0.0, init=False, repr=False)

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
        if self.state_store is not None:
            self._state = self.state_store.load()
            self.withdrawn_total = self._state.withdrawn_total
            self.consecutive_sl = self._state.consecutive_sl
            if self._state.day is not None:
                # Restore the in-memory ledger from persisted state.
                restored_day = date.fromisoformat(self._state.day)
                self._ledger = DailyLedger(
                    day=restored_day,
                    realized_pnl=self._state.day_realized_pnl,
                    starting_equity=self._state.day_starting_equity,
                    kill_switch=self._state.kill_switch,
                    kill_reason=self._state.kill_reason,
                )
        self._peak_account_value = max(float(self.starting_balance), self._account_value())

    def _persist(self) -> None:
        if self.state_store is None:
            return
        ledger = self._ledger
        self._state.day = ledger.day.isoformat() if ledger else None
        self._state.day_starting_equity = ledger.starting_equity if ledger else 0.0
        self._state.day_realized_pnl = ledger.realized_pnl if ledger else 0.0
        self._state.kill_switch = ledger.kill_switch if ledger else False
        self._state.kill_reason = ledger.kill_reason if ledger else ""
        self._state.withdrawn_total = self.withdrawn_total
        self._state.consecutive_sl = self.consecutive_sl
        self.state_store.save(self._state)

    def _account_value(self) -> float:
        """Trading balance plus swept/withdrawn ledger."""
        return float(self.balance + self.withdrawn_total)

    def _current_drawdown_pct(self) -> float:
        if self._peak_account_value <= 0:
            return 0.0
        return max(0.0, (1.0 - self._account_value() / self._peak_account_value) * 100.0)

    @staticmethod
    def _as_float(x: object) -> float | None:
        try:
            if x is None:
                return None
            return float(x)
        except (TypeError, ValueError):
            return None

    def _effective_risk_pct(self, signal: Signal) -> float:
        risk_pct = float(self.risk_per_trade_pct)
        if not self.dynamic_risk_enabled:
            return max(0.0, risk_pct)

        meta = signal.meta or {}
        mult = 1.0

        m = self._as_float(meta.get("risk_multiplier"))
        if m is not None and m > 0:
            mult *= m

        c = self._as_float(meta.get("confidence"))
        if c is not None:
            c = min(max(c, 0.0), 1.0)
            conf_floor = max(0.0, float(self.confidence_risk_floor))
            conf_ceiling = max(conf_floor, float(self.confidence_risk_ceiling))
            conf_mult = conf_floor + (conf_ceiling - conf_floor) * c
            mult *= conf_mult

        dd = self._current_drawdown_pct()
        if dd >= float(self.drawdown_hard_limit_pct):
            mult *= float(self.drawdown_hard_multiplier)
        elif dd >= float(self.drawdown_soft_limit_pct):
            mult *= float(self.drawdown_soft_multiplier)

        eff = risk_pct * mult
        min_risk = self._as_float(self.min_risk_per_trade_pct)
        max_risk = self._as_float(self.max_risk_per_trade_pct)
        if min_risk is not None and min_risk > 0:
            eff = max(eff, min_risk)
        if max_risk is not None and max_risk > 0:
            eff = min(eff, max_risk)
        return max(0.0, eff)

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
            # Reset the consecutive-SL counter at day boundary: a new
            # trading day is a fresh slate for the 2-SL trigger.
            self.consecutive_sl = 0
            self._persist()
        return self._ledger

    # ------------------------------------------------------------------
    # P&L notifications
    # ------------------------------------------------------------------
    def on_trade_closed(
        self, pnl: float, when: datetime, *, reason: str = ""
    ) -> None:
        ledger = self._ensure_day(when)
        self.balance += pnl
        self._peak_account_value = max(self._peak_account_value, self._account_value())
        ledger.realized_pnl += pnl
        self._check_kill_switch(ledger)
        # Consecutive-SL counter for the review-trigger engine.
        if reason == "sl":
            self.consecutive_sl += 1
        elif reason == "tp":
            # Any winning close resets the streak.
            self.consecutive_sl = 0
        self._persist()

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
        effective_risk_pct = self._effective_risk_pct(signal)
        risk_budget = self.balance * (effective_risk_pct / 100.0)
        if risk_budget <= 0:
            return RiskDecision(False, 0.0, "risk budget is zero")
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
