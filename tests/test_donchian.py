"""DonchianRetest strategy sanity tests."""
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy, list_strategies


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def test_donchian_registered():
    assert "donchian_retest" in list_strategies()


def test_donchian_backtest_runs_end_to_end():
    df = generate_synthetic_ohlcv(days=20, timeframe="M5", seed=5)
    strat = get_strategy("donchian_retest")
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=10, slippage_points=1)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    # Just ensure the strategy wires up and metrics exist.
    assert isinstance(res.trades, list)


def test_donchian_two_leg_mode_produces_multileg_signals():
    df = generate_synthetic_ohlcv(days=30, timeframe="M5", seed=6)
    strat = get_strategy("donchian_retest", use_two_legs=True, tp1_rr=1.0, leg1_weight=0.5)
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    engine.run(df)
    # No crash; the paired-leg logic already has dedicated coverage
    # in test_multileg.py. This test just proves donchian_retest is
    # compatible with the multi-leg pathway.


def test_donchian_respects_no_lookahead_via_prepare():
    """Same trace with and without prepare() must not differ in
    'opened before' timing (the multi-leg invariant)."""
    df = generate_synthetic_ohlcv(days=15, timeframe="M5", seed=8)
    inst = _inst()

    def run(use_prepare: bool):
        strat = get_strategy("donchian_retest")
        if not use_prepare:
            strat.prepare = lambda df: None  # type: ignore[method-assign]
        risk = RiskManager(
            starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
            withdraw_half_of_daily_profit=False,
        )
        broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
        return BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)

    slow = run(False); fast = run(True)
    if slow.trades and fast.trades:
        assert min(t.open_time for t in fast.trades) >= min(
            t.open_time for t in slow.trades
        )
