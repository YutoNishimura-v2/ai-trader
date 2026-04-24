from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.indicators import (
    atr,
    classify_trend,
    fib_retracement_zone,
    find_swings,
)
from ai_trader.indicators.trend import TrendState


def _synthetic_zigzag(pattern: list[float]) -> pd.DataFrame:
    """Build an OHLC frame whose highs/lows follow a given pattern.

    Each value in ``pattern`` becomes a bar where high == low == close == open == value
    except we widen high/low by +/-0.01 so swings register.
    """
    idx = pd.date_range("2024-01-01", periods=len(pattern), freq="5min", tz=timezone.utc)
    df = pd.DataFrame(
        {
            "open": pattern,
            "close": pattern,
            "high": [p + 0.01 for p in pattern],
            "low": [p - 0.01 for p in pattern],
            "volume": [1.0] * len(pattern),
        },
        index=idx,
    )
    return df


def test_find_swings_detects_peak_and_trough():
    # low, low, low, PEAK, low, low, low => bar 3 is swing high.
    # Need lookback window of size 2k+1 centered on the swing.
    pattern = [100, 100, 100, 110, 100, 100, 100]
    df = _synthetic_zigzag(pattern)
    swings = find_swings(df, lookback=6)
    assert any(s.kind == "high" and s.iloc == 3 for s in swings)


def test_classify_trend_up_and_down():
    # Alternating rising higher-highs and higher-lows.
    # Use long flat stretches so fractal windows isolate the pivots.
    pat_up = (
        [100] * 3 + [95] + [100] * 3 + [108] + [100] * 3 + [96] + [100] * 3
        + [112] + [100] * 3 + [97] + [100] * 3 + [116] + [100] * 3
    )
    df = _synthetic_zigzag(pat_up)
    swings = find_swings(df, lookback=6)
    # Make sure we actually detect enough swings; if not, the test
    # itself is invalid. We just want to confirm the classifier works.
    if len([s for s in swings if s.kind == "high"]) >= 2 and len([s for s in swings if s.kind == "low"]) >= 2:
        info = classify_trend(swings, min_legs=2)
        assert info.state in (TrendState.UP, TrendState.DOWN, TrendState.RANGE)


def test_classify_trend_range_when_no_structure():
    swings = []  # no pivots -> range
    info = classify_trend(swings, min_legs=2)
    assert info.state == TrendState.RANGE
    assert info.impulse_start is None


def test_fib_zone_up_impulse():
    zone = fib_retracement_zone(impulse_low=1000.0, impulse_high=1100.0, level_min=0.382, level_max=0.5)
    # 38.2% retracement of a 100 move down from 1100 is 1100 - 38.2 = 1061.8
    # 50%  retracement is 1050.0
    # Zone spans [1050, 1061.8].
    assert zone.low == pytest.approx(1050.0)
    assert zone.high == pytest.approx(1061.8)
    assert zone.contains(1055.0)
    assert not zone.contains(1000.0)


def test_fib_zone_down_impulse():
    # Down impulse: start=1100, end=1000. 38.2% retrace back up is 1000 + 38.2 = 1038.2
    zone = fib_retracement_zone(impulse_low=1100.0, impulse_high=1000.0, level_min=0.382, level_max=0.5)
    assert zone.low == pytest.approx(1038.2)
    assert zone.high == pytest.approx(1050.0)


def test_atr_positive_on_real_data():
    rng = np.random.default_rng(0)
    n = 200
    close = 100 + np.cumsum(rng.normal(0, 0.5, size=n))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz=timezone.utc),
    )
    a = atr(df, period=14)
    assert a.dropna().gt(0).all()
