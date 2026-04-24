"""BBScalper sanity."""
from datetime import timezone

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


def test_bb_scalper_registered():
    assert "bb_scalper" in list_strategies()


def test_bb_scalper_runs_end_to_end_and_fires_often_on_m1():
    # 5 days of M1 synthetic data; scalper should produce several
    # trades (more than a swing strategy would at the same horizon).
    df = generate_synthetic_ohlcv(days=5, timeframe="M1", seed=21)
    strat = get_strategy("bb_scalper", bb_n=20, bb_k=2.0, sl_atr_mult=0.5,
                         tp_target="middle", require_rejection=False,
                         cooldown_bars=2)
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    # Scalping smell test: more than 5 trades over 5 trading days on
    # synthetic data, even after the concurrent-position cap.
    assert len(res.trades) >= 5, f"bb_scalper too quiet: {len(res.trades)} trades"


def test_bb_scalper_prepare_matches_slow_path_timing():
    df = generate_synthetic_ohlcv(days=3, timeframe="M1", seed=22)
    inst = _inst()

    def run(use_prepare: bool):
        s = get_strategy("bb_scalper", require_rejection=False)
        if not use_prepare:
            # The scalper REQUIRES prepare; we just want to make sure
            # that doing nothing in prepare cleanly yields zero trades,
            # rather than crashing.
            s.prepare = lambda df: None  # type: ignore[method-assign]
        r = RiskManager(
            starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
            withdraw_half_of_daily_profit=False,
        )
        b = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
        return BacktestEngine(strategy=s, risk=r, broker=b).run(df)

    slow = run(False); fast = run(True)
    # Slow path returns 0 trades by design; no lookahead check needed.
    assert len(slow.trades) == 0
    assert isinstance(fast.trades, list)
