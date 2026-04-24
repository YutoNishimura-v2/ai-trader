"""LiquiditySweep sanity tests."""
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


def test_registered():
    assert "liquidity_sweep" in list_strategies()


def test_runs_on_synthetic_m1():
    df = generate_synthetic_ohlcv(days=10, timeframe="M1", seed=91)
    strat = get_strategy("liquidity_sweep")
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert isinstance(res.trades, list)


def test_constructed_low_sweep_triggers_long():
    """Build a clean sweep + reversal scenario."""
    n = 200
    idx = pd.date_range("2026-04-01 09:00", periods=n, freq="1min", tz=timezone.utc)
    # Background: a flat range around 2000, lows trace 1995.
    rng = np.random.default_rng(0)
    close = 2000.0 + rng.normal(0, 0.1, n)
    df = pd.DataFrame({
        "open": close - 0.05, "close": close + 0.05,
        "high": close + 0.5, "low": np.maximum(close - 0.5, 1995.0),
        "volume": 1.0,
    }, index=idx)
    # At bar 150: sweep the prior 30-bar low (~1995) by 1.0, close back above.
    df.iloc[150, df.columns.get_loc("open")] = 1996.0
    df.iloc[150, df.columns.get_loc("low")] = 1992.5     # swept
    df.iloc[150, df.columns.get_loc("close")] = 1996.5   # closed back above 1995
    df.iloc[150, df.columns.get_loc("high")] = 1997.0
    # Bar 151: bullish reversal candle.
    df.iloc[151, df.columns.get_loc("open")] = 1996.5
    df.iloc[151, df.columns.get_loc("low")] = 1996.4
    df.iloc[151, df.columns.get_loc("close")] = 1998.5
    df.iloc[151, df.columns.get_loc("high")] = 1998.7

    strat = get_strategy(
        "liquidity_sweep",
        swing_window=30, atr_period=14,
        min_sweep_atr=0.05,        # very small threshold for the test
        sl_atr_buffer=0.3,
        tp1_rr=1.0, tp2_rr=2.5,
        cooldown_bars=2, setup_ttl_bars=5,
        require_close_back=True,
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        risk_per_trade_pct=2.0,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    longs = [t for t in res.trades if t.side == "buy"]
    assert len(longs) >= 1, f"expected a long, got {res.trades}"
