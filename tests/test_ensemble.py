"""Ensemble wrapper basic semantics."""
from datetime import timezone

import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide
from ai_trader.strategy.ensemble import EnsembleStrategy
from ai_trader.strategy.registry import get_strategy, list_strategies, register_strategy


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def test_ensemble_registered():
    assert "ensemble" in list_strategies()


def test_ensemble_requires_members():
    with pytest.raises(ValueError, match="members"):
        EnsembleStrategy(members=[])


# Register two test-only stubs.
class _FireOnceAt(BaseStrategy):
    name = "_fire_once_at_bar"

    def __init__(self, at: int = 10, price_off: float = 1.0, side: str = "buy"):
        super().__init__(at=at, price_off=price_off, side=side)
        self._fired = False
        self.at = at
        self.off = price_off
        self.side = SignalSide(side)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        if self._fired or len(history) < self.at:
            return None
        self._fired = True
        close = float(history.iloc[-1]["close"])
        if self.side == SignalSide.BUY:
            return Signal(side=SignalSide.BUY, entry=None,
                          stop_loss=close - 2.0, take_profit=close + self.off,
                          reason="stub-buy")
        return Signal(side=SignalSide.SELL, entry=None,
                      stop_loss=close + 2.0, take_profit=close - self.off,
                      reason="stub-sell")


# Register once (guard against duplicate registration in rerun).
try:
    register_strategy(_FireOnceAt)
except Exception:
    pass


def test_ensemble_priority_order():
    """When both members fire at the same bar, member 0's signal
    wins; member 1 is NOT consumed on that bar (the ensemble returns
    early). On subsequent bars member 1 will fire on its own trigger
    — which is exactly what we want: no queuing, no dropping, just
    a clean 'first member to speak wins this bar'."""
    df = generate_synthetic_ohlcv(days=2, timeframe="M1", seed=71)
    inst = _inst()
    ens = EnsembleStrategy(members=[
        {"name": "_fire_once_at_bar", "params": {"at": 50, "side": "buy"}},
        {"name": "_fire_once_at_bar", "params": {"at": 50, "side": "sell"}},
    ])
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False, max_concurrent_positions=2,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=ens, risk=risk, broker=broker)
    res = engine.run(df)

    # The BUY trade opens first (fill at bar 51 = open of bar 50+1),
    # then the SELL trade opens later. Verify ordering by open_time.
    assert len(res.trades) >= 2
    by_open = sorted(res.trades, key=lambda t: t.open_time)
    assert by_open[0].side == "buy"
    assert by_open[1].side == "sell"
    assert by_open[0].open_time < by_open[1].open_time


def test_ensemble_members_both_trade_over_time():
    """Across many bars, both members get chances and trade."""
    df = generate_synthetic_ohlcv(days=2, timeframe="M1", seed=72)
    inst = _inst()
    ens = EnsembleStrategy(members=[
        {"name": "_fire_once_at_bar", "params": {"at": 50, "side": "buy"}},
        {"name": "_fire_once_at_bar", "params": {"at": 500, "side": "sell"}},
    ])
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False, max_concurrent_positions=2,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=ens, risk=risk, broker=broker)
    res = engine.run(df)
    sides = {t.side for t in res.trades}
    assert "buy" in sides
    assert "sell" in sides
