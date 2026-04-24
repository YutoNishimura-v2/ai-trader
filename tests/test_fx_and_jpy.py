"""JPY-native accounting + v3 lot cap (plan v3 §A.2, §A.4)."""
from datetime import datetime, timezone

import pytest

from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import Signal, SignalSide


def _xau_usd() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def test_fixed_fx_round_trip():
    fx = FixedFX.from_config({"USDJPY": 150.0})
    assert fx.convert(1.0, "USD", "JPY") == pytest.approx(150.0)
    assert fx.convert(150.0, "JPY", "USD") == pytest.approx(1.0)
    assert fx.convert(42.0, "USD", "USD") == 42.0


def test_risk_manager_requires_fx_for_cross_currency():
    with pytest.raises(ValueError, match="FXConverter is required"):
        RiskManager(
            starting_balance=100_000.0,
            max_leverage=100.0,
            instrument=_xau_usd(),
            account_currency="JPY",
        )


def test_tick_value_is_converted_to_jpy():
    fx = FixedFX.from_config({"USDJPY": 150.0})
    rm = RiskManager(
        starting_balance=100_000.0,
        max_leverage=100.0,
        instrument=_xau_usd(),
        account_currency="JPY",
        fx=fx,
    )
    assert rm.tick_value_account() == pytest.approx(150.0)
    # 1 lot notional at $2000 = $200,000 = ¥30,000,000
    assert rm.notional_account(1.0, ref_price=2000.0) == pytest.approx(30_000_000.0)


def test_v3_lot_cap_binds_on_small_jpy_account():
    """Plan v3 §A.2: ¥100k → 0.1 lot cap.

    Risk-% sizing with a small SL would otherwise size huge on a JPY
    account; the v3 cap clamps it.
    """
    fx = FixedFX.from_config({"USDJPY": 150.0})
    rm = RiskManager(
        starting_balance=100_000.0,              # ¥100k
        max_leverage=100.0,
        instrument=_xau_usd(),
        risk_per_trade_pct=5.0,                  # intentionally large
        daily_profit_target_pct=30.0,
        daily_max_loss_pct=10.0,
        lot_cap_per_unit_balance=1.0e-6,         # 0.1 lot per ¥100k
        account_currency="JPY",
        fx=fx,
    )
    # Use a tight SL so risk-% sizing isn't the binding cap.
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1999.9, take_profit=2001.0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert d.approved
    assert d.lots == pytest.approx(0.10, abs=1e-9)


def test_v3_lot_cap_scales_with_balance():
    """¥1M balance → 1.0 lot cap."""
    fx = FixedFX.from_config({"USDJPY": 150.0})
    rm = RiskManager(
        starting_balance=1_000_000.0,
        max_leverage=100.0,
        instrument=_xau_usd(),
        risk_per_trade_pct=5.0,
        daily_profit_target_pct=30.0,
        daily_max_loss_pct=10.0,
        lot_cap_per_unit_balance=1.0e-6,
        account_currency="JPY",
        fx=fx,
    )
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1999.9, take_profit=2001.0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert d.approved
    assert d.lots == pytest.approx(1.00, abs=1e-9)


def test_daily_envelope_expressed_in_account_currency():
    """Plan v3: +30 % / -10 % on realized P&L is evaluated against
    the JPY ledger. After a ¥35k realized win, the kill-switch trips
    even though the raw USD P&L was a modest number."""
    fx = FixedFX.from_config({"USDJPY": 150.0})
    rm = RiskManager(
        starting_balance=100_000.0,
        max_leverage=100.0,
        instrument=_xau_usd(),
        daily_profit_target_pct=30.0,
        daily_max_loss_pct=10.0,
        lot_cap_per_unit_balance=1.0e-6,
        account_currency="JPY",
        fx=fx,
    )
    now = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    rm._ensure_day(now)
    rm.on_trade_closed(35_000.0, when=now)  # ¥35k realized
    sig = Signal(side=SignalSide.BUY, entry=None, stop_loss=1990.0, take_profit=2010.0)
    d = rm.evaluate(sig, ref_price=2000.0, open_positions=0, now=now)
    assert not d.approved
    assert "profit target" in d.reason
