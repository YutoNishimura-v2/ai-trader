"""Structural tests for BosRetestScalper + session filter."""
from datetime import time, timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy, list_strategies
from ai_trader.strategy.session import check_session, in_london, in_ny, in_overlap


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


# ---------- session ----------
def test_session_london_range():
    assert in_london(time(6, 59)) is False
    assert in_london(time(7, 0)) is True
    assert in_london(time(15, 59)) is True
    assert in_london(time(16, 0)) is False


def test_session_ny_range():
    assert in_ny(time(11, 59)) is False
    assert in_ny(time(12, 0)) is True
    assert in_ny(time(20, 59)) is True
    assert in_ny(time(21, 0)) is False


def test_session_overlap_is_intersection():
    assert in_overlap(time(11, 0)) is False
    assert in_overlap(time(12, 0)) is True
    assert in_overlap(time(15, 59)) is True
    assert in_overlap(time(16, 0)) is False


def test_session_always_mode_is_always_true():
    for h in range(24):
        assert check_session(time(h), "always") is True


def test_session_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unknown session"):
        check_session(time(12), "bogus")


# ---------- strategy registration ----------
def test_bos_retest_registered():
    assert "bos_retest_scalper" in list_strategies()


# ---------- smoke run ----------
def test_bos_retest_runs_on_synthetic_m1():
    df = generate_synthetic_ohlcv(days=7, timeframe="M1", seed=51)
    strat = get_strategy("bos_retest_scalper", session="always")
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    assert isinstance(res.trades, list)


# ---------- structural: BOS triggers a setup ----------
def _build_uptrend_with_bos(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """Build an OHLC frame with a clean HH+HL structure followed
    by a break of the latest HH. Returns a frame dense enough that
    the default swing_lookback=10 can confirm pivots."""
    rng = np.random.default_rng(seed)
    # Structural points: four clean swings ramping up, then a break.
    pivots = [
        (50,  1000.0, "low"),
        (100, 1010.0, "high"),
        (150, 1005.0, "low"),
        (200, 1020.0, "high"),
        (250, 1015.0, "low"),
        (300, 1030.0, "high"),
        (350, 1025.0, "low"),
        (395, 1045.0, "high"),   # BOS bar
    ]
    # Interpolate a smooth path between pivots.
    xs = np.array([0] + [p[0] for p in pivots] + [n - 1])
    ys = np.array([1000.0] + [p[1] for p in pivots] + [1045.0])
    close = np.interp(np.arange(n), xs, ys) + rng.normal(0, 0.05, size=n)
    idx = pd.date_range("2026-02-01 09:00", periods=n, freq="1min", tz=timezone.utc)
    df = pd.DataFrame(
        {
            "open": close - 0.02,
            "close": close + 0.02,
            "high": close + 0.3,
            "low": close - 0.3,
            "volume": 1.0,
        },
        index=idx,
    )
    # Sharpen each pivot a bit so the fractal detector sees them.
    for iloc, price, kind in pivots:
        if kind == "high":
            df.iloc[iloc, df.columns.get_loc("high")] = price + 0.5
        else:
            df.iloc[iloc, df.columns.get_loc("low")] = price - 0.5
    return df


def test_bos_retest_fires_on_constructed_uptrend():
    df = _build_uptrend_with_bos(n=500, seed=7)
    # Extend the frame with a retest + rejection after the BOS.
    # After iloc 395 (BOS high at 1045), dip to 1030 (retest of the
    # prior HH level ~1030) then close a bullish rejection.
    n_extra = 100
    idx_extra = pd.date_range(
        df.index[-1] + pd.Timedelta(minutes=1), periods=n_extra, freq="1min", tz=timezone.utc,
    )
    retest_close = np.concatenate([
        np.linspace(1045, 1031, 40),       # pullback
        np.linspace(1031, 1040, 20),       # rejection + recovery
        np.linspace(1040, 1050, 40),       # resume up
    ])
    extra = pd.DataFrame(
        {
            "open": retest_close - 0.02,
            "close": retest_close + 0.02,
            "high": retest_close + 0.3,
            "low": retest_close - 0.3,
            "volume": 1.0,
        },
        index=idx_extra,
    )
    # Make the rejection bar (iloc ~540 in combined frame) sharp.
    rej_i = 540
    if rej_i < len(df) + len(extra):
        combined_idx = rej_i - len(df)
        if 0 <= combined_idx < len(extra):
            extra.iloc[combined_idx, extra.columns.get_loc("open")] = 1030.0
            extra.iloc[combined_idx, extra.columns.get_loc("low")] = 1029.0
            extra.iloc[combined_idx, extra.columns.get_loc("close")] = 1035.0
            extra.iloc[combined_idx, extra.columns.get_loc("high")] = 1036.0
    full = pd.concat([df, extra])

    strat = get_strategy(
        "bos_retest_scalper",
        swing_lookback=10,
        min_legs=2,
        atr_period=14,
        retest_tolerance_atr=3.0,
        sl_atr_buffer=0.3,
        cooldown_bars=2,
        setup_ttl_bars=200,
        session="always",
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(full)
    longs = [t for t in res.trades if t.side == "buy"]
    assert len(longs) >= 1, (
        f"expected at least 1 long BOS-retest entry on constructed uptrend; got {res.trades}"
    )


def test_bos_session_filter_silences_outside_overlap():
    """When session='overlap', bars outside 12:00-16:00 UTC must
    never produce a signal, even on a perfect uptrend."""
    df = _build_uptrend_with_bos(n=500, seed=3)
    # The constructed frame starts at 09:00 UTC; 12:00 UTC is bar
    # iloc 180, 16:00 is iloc 420. The BOS bar is at 395. If we
    # ran with session='overlap' and the retest happens after the
    # overlap closes, no trade should fire.
    # Confirm the constructed retest (ilocs 500-540) is *after* the
    # overlap ends at iloc 420 → no trades allowed.
    strat = get_strategy(
        "bos_retest_scalper",
        swing_lookback=10, min_legs=2, atr_period=14,
        retest_tolerance_atr=3.0, cooldown_bars=2, setup_ttl_bars=200,
        session="overlap",
    )
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    assert all(False for _ in res.trades) or len(res.trades) == 0
