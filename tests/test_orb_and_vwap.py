"""Smoke + invariants for london_orb and vwap_reversion."""
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy, list_strategies
from ai_trader.strategy.vwap_reversion import _session_vwap_and_dev


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def test_orb_registered():
    assert "london_orb" in list_strategies()


def test_vwap_registered():
    assert "vwap_reversion" in list_strategies()


def test_orb_runs_on_synthetic():
    df = generate_synthetic_ohlcv(days=10, timeframe="M1", seed=131)
    strat = get_strategy("london_orb")
    inst = _inst()
    risk = RiskManager(starting_balance=10_000.0, max_leverage=100.0,
                       instrument=inst, withdraw_half_of_daily_profit=False)
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert isinstance(res.trades, list)


def test_vwap_runs_on_synthetic():
    df = generate_synthetic_ohlcv(days=10, timeframe="M1", seed=132)
    strat = get_strategy("vwap_reversion")
    inst = _inst()
    risk = RiskManager(starting_balance=10_000.0, max_leverage=100.0,
                       instrument=inst, withdraw_half_of_daily_profit=False)
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert isinstance(res.trades, list)


def test_session_vwap_resets_each_day():
    """VWAP at the start of each new UTC day should not depend on
    prior days. Build 2 days where day 1 prices average 100 and
    day 2 prices average 200; the day-2 VWAP should be near 200,
    not somewhere between."""
    n_per_day = 60 * 24
    n = n_per_day * 2
    idx = pd.date_range("2026-01-01 00:00", periods=n, freq="1min", tz=timezone.utc)
    closes = np.concatenate([np.full(n_per_day, 100.0), np.full(n_per_day, 200.0)])
    df = pd.DataFrame({
        "open": closes, "close": closes,
        "high": closes + 0.1, "low": closes - 0.1, "volume": 1.0,
    }, index=idx)
    vwap, _ = _session_vwap_and_dev(df, warmup_bars=5)
    # Mid-day-2 VWAP should be ~200, not ~150.
    mid_day2 = vwap[n_per_day + 600]   # 600 minutes into day 2
    assert abs(mid_day2 - 200.0) < 0.5, f"VWAP did not reset: {mid_day2}"


def test_orb_one_trade_per_day_max():
    """The default max_trades_per_day=1 must hold even on a chaotic
    pattern where multiple breakouts could fire."""
    df = generate_synthetic_ohlcv(days=15, timeframe="M1", seed=133)
    strat = get_strategy("london_orb", max_trades_per_day=1)
    inst = _inst()
    risk = RiskManager(starting_balance=10_000.0, max_leverage=100.0,
                       instrument=inst, withdraw_half_of_daily_profit=False)
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    # At most 1 DECISION (group_id) per UTC date. A 2-leg signal
    # opens 2 sub-positions sharing one group_id; both legs
    # collapse to one decision.
    by_day: dict = {}
    for t in res.trades:
        day = t.open_time.astimezone(timezone.utc).date()
        gid = t.group_id if t.group_id is not None else id(t)
        by_day.setdefault(day, set()).add(gid)
    for day, groups in by_day.items():
        assert len(groups) <= 1, f"day {day}: {len(groups)} decisions (>1)"
