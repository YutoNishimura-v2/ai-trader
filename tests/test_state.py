"""BotState persistence (plan v3 §A.8, §A.10)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.state.store import BotState, StateStore
from ai_trader.strategy.base import Signal, SignalSide


def _xau_usd() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def _mk_risk(path: Path) -> RiskManager:
    return RiskManager(
        starting_balance=100_000.0,
        max_leverage=100.0,
        instrument=_xau_usd(),
        risk_per_trade_pct=0.5,
        daily_profit_target_pct=30.0,
        daily_max_loss_pct=10.0,
        withdraw_half_of_daily_profit=False,
        lot_cap_per_unit_balance=1.0e-6,
        account_currency="JPY",
        fx=FixedFX.from_config({"USDJPY": 150.0}),
        state_store=StateStore(path),
    )


def test_store_roundtrip_empty(tmp_path: Path):
    path = tmp_path / "state.json"
    assert StateStore(path).load() == BotState()


def test_save_is_atomic(tmp_path: Path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    s = BotState(day="2026-04-24", kill_switch=True, kill_reason="test")
    store.save(s)
    # A tmp file should not linger.
    siblings = list(path.parent.iterdir())
    assert siblings == [path]
    loaded = store.load()
    assert loaded.day == "2026-04-24"
    assert loaded.kill_switch is True


def test_kill_switch_survives_restart(tmp_path: Path):
    path = tmp_path / "state.json"
    now = datetime(2026, 4, 24, 10, tzinfo=timezone.utc)
    rm = _mk_risk(path)
    rm._ensure_day(now)
    # Drive the kill switch via a big win.
    rm.on_trade_closed(35_000.0, when=now, reason="tp")
    assert rm._ledger.kill_switch

    # Simulate a restart: new RiskManager reading the same state.
    rm2 = _mk_risk(path)
    rm2._ensure_day(now)                    # same UTC day
    assert rm2._ledger is not None
    assert rm2._ledger.kill_switch
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1999.0, take_profit=2001.0)
    d = rm2.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert not d.approved
    assert "kill-switch" in d.reason


def test_consecutive_sl_counter_persists(tmp_path: Path):
    path = tmp_path / "state.json"
    now = datetime(2026, 4, 24, 10, tzinfo=timezone.utc)
    rm = _mk_risk(path)
    rm.on_trade_closed(-500.0, when=now, reason="sl")
    rm.on_trade_closed(-500.0, when=now, reason="sl")
    assert rm.consecutive_sl == 2

    rm2 = _mk_risk(path)
    assert rm2.consecutive_sl == 2


def test_winning_trade_resets_consecutive_sl(tmp_path: Path):
    path = tmp_path / "state.json"
    now = datetime(2026, 4, 24, 10, tzinfo=timezone.utc)
    rm = _mk_risk(path)
    rm.on_trade_closed(-500.0, when=now, reason="sl")
    assert rm.consecutive_sl == 1
    rm.on_trade_closed(800.0, when=now, reason="tp")
    assert rm.consecutive_sl == 0


def test_day_rollover_resets_consecutive_sl(tmp_path: Path):
    path = tmp_path / "state.json"
    d1 = datetime(2026, 4, 24, 23, tzinfo=timezone.utc)
    d2 = d1 + timedelta(hours=2)             # new UTC day
    rm = _mk_risk(path)
    rm.on_trade_closed(-500.0, when=d1, reason="sl")
    assert rm.consecutive_sl == 1
    rm._ensure_day(d2)
    assert rm.consecutive_sl == 0
