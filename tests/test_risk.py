from datetime import datetime, timedelta, timezone

import pytest

from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import Signal, SignalSide


def _xauusd():
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        min_lot=0.01, lot_step=0.01,
    )


def _rm(**overrides) -> RiskManager:
    defaults = dict(
        starting_balance=10_000.0,
        max_leverage=100.0,
        instrument=_xauusd(),
        risk_per_trade_pct=0.5,
        daily_profit_target_pct=2.0,
        daily_max_loss_pct=1.5,
        withdraw_half_of_daily_profit=True,
        max_concurrent_positions=1,
    )
    defaults.update(overrides)
    return RiskManager(**defaults)


def test_sizing_is_risk_pct_bounded():
    rm = _rm()
    # SL 500 ticks away ($5 per lot loss). Risk budget = 0.5% of 10k = $50.
    # => expected lots = 50 / 500 = 0.10
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1995.0, take_profit=2015.0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert d.approved
    assert d.lots == pytest.approx(0.10, abs=1e-9)


def test_leverage_cap_can_bite():
    # Shrink account so 1% risk sizing would blow leverage.
    rm = _rm(starting_balance=200.0, risk_per_trade_pct=5.0)
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1999.9, take_profit=2000.2)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    # max notional = 200 * 100 = 20_000 USD; one lot = 100 * 2000 = 200_000 USD
    # so lots_by_leverage = 0.1 -> rounded to 0.10 lot
    # risk-% sizing would be huge with a 1-cent SL; leverage must clamp it.
    if d.approved:
        assert d.lots <= 0.10 + 1e-9


def test_daily_kill_switch_on_profit_target():
    rm = _rm()
    now = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    rm._ensure_day(now)
    # 2% of 10k = $200. Realize $201 and the switch must trigger.
    rm.on_trade_closed(201.0, when=now)
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1995.0, take_profit=2005.0)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert not d.approved
    assert "profit target" in d.reason


def test_daily_kill_switch_on_max_loss():
    rm = _rm()
    now = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    rm._ensure_day(now)
    rm.on_trade_closed(-200.0, when=now)
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1995.0, take_profit=2005.0)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert not d.approved
    assert "max loss" in d.reason


def test_half_profit_is_withdrawn_at_day_rollover():
    rm = _rm()
    day1 = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    rm._ensure_day(day1)
    rm.on_trade_closed(120.0, when=day1)  # under target, no kill
    assert rm.balance == pytest.approx(10_120.0)
    # Crossing midnight triggers the sweep: half of 120 = 60.
    day2 = day1 + timedelta(days=1)
    rm._ensure_day(day2)
    assert rm.balance == pytest.approx(10_060.0)
    assert rm.withdrawn_total == pytest.approx(60.0)


def test_losing_day_does_not_withdraw():
    rm = _rm()
    day1 = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    rm._ensure_day(day1)
    rm.on_trade_closed(-80.0, when=day1)
    day2 = day1 + timedelta(days=1)
    rm._ensure_day(day2)
    assert rm.withdrawn_total == 0.0
    assert rm.balance == pytest.approx(9_920.0)


def test_concurrent_position_limit():
    rm = _rm(max_concurrent_positions=1)
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1995.0, take_profit=2005.0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=1, now=now)
    assert not d.approved
    assert "concurrent" in d.reason
