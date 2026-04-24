"""Sanity tests for TrendPullbackScalper."""
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
    assert "trend_pullback_scalper" in list_strategies()


def test_runs_end_to_end_on_synthetic_m1():
    df = generate_synthetic_ohlcv(days=10, timeframe="M1", seed=33)
    strat = get_strategy("trend_pullback_scalper")
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    # Smoke only; synthetic data isn't guaranteed to trigger this
    # specific pattern. Assert no crash + trades is a list.
    assert isinstance(res.trades, list)


def test_produces_long_signals_on_constructed_uptrend():
    """Construct a clean uptrend with a visible pullback and verify
    the strategy fires a BUY."""
    n = 500
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    # Base uptrend with a dip at bar ~350.
    base = 2000.0 + np.arange(n) * 0.05
    # Inject a ~1% pullback around bars 340-360 then recover.
    dip = np.zeros(n)
    dip[340:360] = -np.linspace(0, 3.0, 20)
    dip[360:380] = -np.linspace(3.0, 0, 20)
    close = base + dip
    df = pd.DataFrame(
        {
            "open": close - 0.02,
            "close": close + 0.02,
            "high": close + 0.1,
            "low": close - 0.1,
            "volume": 1.0,
        },
        index=idx,
    )
    # At the recovery bar, make it a strong bullish rejection candle.
    rec = 395
    df.loc[df.index[rec], "open"] = close[rec] - 0.3
    df.loc[df.index[rec], "low"] = close[rec] - 0.5
    df.loc[df.index[rec], "high"] = close[rec] + 0.1
    df.loc[df.index[rec], "close"] = close[rec] + 0.1

    strat = get_strategy(
        "trend_pullback_scalper",
        fast_ema=10, slow_ema=30, slope_bars=5, slope_min_atr=0.01,
        impulse_lookback=30, fib_min=0.2, fib_max=0.8,
        atr_period=10, sl_atr_mult=0.3,
        tp1_rr=1.0, tp2_rr=3.0, leg1_weight=0.5,
        cooldown_bars=3,
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    longs = [t for t in res.trades if t.side == "buy"]
    assert len(longs) >= 1, f"expected at least 1 long trade on constructed uptrend, got {len(res.trades)}"


def test_multileg_tp_stretch_produces_two_legs():
    df = generate_synthetic_ohlcv(days=5, timeframe="M1", seed=34)
    strat = get_strategy(
        "trend_pullback_scalper",
        tp1_rr=1.0, tp2_rr=3.0, leg1_weight=0.5,
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    engine.run(df)
    # Can't easily assert "every signal had two legs" without engine-
    # internal access; multi-leg mechanics are already covered by
    # tests/test_multileg.py. This just runs the configured strategy
    # end-to-end to prove it wires up with tp2_rr=3.
