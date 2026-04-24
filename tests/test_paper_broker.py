from datetime import datetime, timezone

import pytest

from ai_trader.broker.paper import PaperBroker
from ai_trader.broker.base import Order
from ai_trader.risk.manager import InstrumentSpec
from ai_trader.strategy.base import SignalSide


def _xau() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        min_lot=0.01, lot_step=0.01,
    )


def test_tp_hit_produces_positive_pnl():
    b = PaperBroker(instrument=_xau(), spread_points=0, slippage_points=0, commission_per_lot=0.0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    res = b.submit(Order(SignalSide.BUY, lots=0.1, stop_loss=1990.0, take_profit=2010.0), ref_price=2000.0, now=now)
    assert res.ok
    closed = list(b.check_stops(bar_high=2011.0, bar_low=2001.0, now=now))
    assert len(closed) == 1
    assert closed[0].reason == "tp"
    assert closed[0].pnl > 0


def test_sl_hit_produces_negative_pnl():
    b = PaperBroker(instrument=_xau(), spread_points=0, slippage_points=0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    res = b.submit(Order(SignalSide.BUY, lots=0.1, stop_loss=1990.0, take_profit=2010.0), ref_price=2000.0, now=now)
    assert res.ok
    closed = list(b.check_stops(bar_high=2001.0, bar_low=1989.0, now=now))
    assert len(closed) == 1
    assert closed[0].reason == "sl"
    assert closed[0].pnl < 0


def test_spread_reduces_pnl():
    b0 = PaperBroker(instrument=_xau(), spread_points=0, slippage_points=0)
    b1 = PaperBroker(instrument=_xau(), spread_points=20, slippage_points=0)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

    for b in (b0, b1):
        b.submit(Order(SignalSide.BUY, lots=1.0, stop_loss=1990.0, take_profit=2010.0),
                 ref_price=2000.0, now=now)
    c0 = list(b0.check_stops(bar_high=2011.0, bar_low=2001.0, now=now))[0]
    c1 = list(b1.check_stops(bar_high=2011.0, bar_low=2001.0, now=now))[0]
    assert c1.pnl < c0.pnl
