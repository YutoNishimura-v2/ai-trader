"""mtf_zigzag_bos sanity."""
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
    assert "mtf_zigzag_bos" in list_strategies()


def test_runs_on_synthetic_m1():
    df = generate_synthetic_ohlcv(days=10, timeframe="M1", seed=101)
    strat = get_strategy("mtf_zigzag_bos")
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert isinstance(res.trades, list)


def test_htf_bias_drives_direction():
    """In a clean uptrend on M5, the strategy should not produce
    SHORT signals."""
    n = 60 * 24 * 5      # 5 days of M1
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    rng = np.random.default_rng(42)
    # Slow uptrend with mild noise; on M5 this aggregates to clean HH/HL.
    drift = np.linspace(2000, 2200, n)
    close = drift + rng.normal(0, 0.5, n)
    df = pd.DataFrame(
        {
            "open": close - 0.05,
            "close": close + 0.05,
            "high": close + 0.5,
            "low": close - 0.5,
            "volume": 1.0,
        },
        index=idx,
    )
    strat = get_strategy(
        "mtf_zigzag_bos",
        htf="M5", zigzag_threshold_atr=1.0, zigzag_atr_period=14,
        atr_period_m1=14, retest_tolerance_atr=1.0, sl_atr_buffer=0.3,
        cooldown_bars=2, setup_ttl_bars=120, session="always",
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        risk_per_trade_pct=2.0,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    sells = [t for t in res.trades if t.side == "sell"]
    # Should not produce any SHORT trades in a clean uptrend.
    assert len(sells) == 0, f"expected no shorts in clean uptrend, got {sells}"
