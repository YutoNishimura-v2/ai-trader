"""zigzag_fib_mtf: registration and backtest smoke."""
from datetime import timezone

import numpy as np
import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy, list_strategies


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD",
        contract_size=100.0,
        tick_size=0.01,
        tick_value=1.0,
        quote_currency="USD",
        min_lot=0.01,
        lot_step=0.01,
    )


def test_registered():
    assert "zigzag_fib_mtf" in list_strategies()


def test_runs_on_synthetic_m1():
    df = generate_synthetic_ohlcv(days=15, timeframe="M1", seed=202)
    strat = get_strategy(
        "zigzag_fib_mtf",
        zigzag_threshold_atr=1.2,
        fib_touch_atr=0.5,
        cooldown_bars=2,
        session="always",
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0,
        max_leverage=100.0,
        instrument=inst,
        risk_per_trade_pct=1.0,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert isinstance(res.trades, list)


def test_helpers_fib_prices_up_leg():
    from ai_trader.strategy.zigzag_fib_mtf import _fib_retrace_prices

    lo, hi = 100.0, 200.0
    xs = _fib_retrace_prices(lo, hi, up_leg=True)
    assert len(xs) == 3
    assert abs(xs[0] - (200 - 0.382 * 100)) < 1e-6
    assert abs(xs[1] - 150.0) < 1e-6
    assert abs(xs[2] - (200 - 0.618 * 100)) < 1e-6


def test_helpers_fib_prices_down_leg():
    from ai_trader.strategy.zigzag_fib_mtf import _fib_retrace_prices

    lo, hi = 100.0, 200.0
    xs = _fib_retrace_prices(lo, hi, up_leg=False)
    assert abs(xs[0] - (100 + 0.382 * 100)) < 1e-6
    assert abs(xs[1] - 150.0) < 1e-6


def test_uptrend_no_short_spam():
    """Strong M1 uptrend: should not emit sells (bias up on both TFs)."""
    n = 60 * 24 * 8
    idx = pd.date_range("2026-02-01", periods=n, freq="1min", tz=timezone.utc)
    rng = np.random.default_rng(7)
    drift = np.linspace(2000, 2100, n)
    close = drift + rng.normal(0, 0.3, n)
    df = pd.DataFrame(
        {
            "open": close - 0.02,
            "close": close + 0.02,
            "high": close + 0.4,
            "low": close - 0.4,
            "volume": 1.0,
        },
        index=idx,
    )
    strat = get_strategy(
        "zigzag_fib_mtf",
        zigzag_threshold_atr=0.9,
        fib_touch_atr=0.8,
        cooldown_bars=5,
        session="always",
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0,
        max_leverage=100.0,
        instrument=inst,
        risk_per_trade_pct=1.5,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    sells = [t for t in res.trades if t.side == "sell"]
    assert len(sells) == 0
