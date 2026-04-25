"""MTFContext: no-lookahead higher-timeframe bar lookups."""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.data.mtf import MTFContext


def _m1(n: int = 200) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(0, 0.05, n))
    return pd.DataFrame(
        {"open": close, "close": close,
         "high": close + 0.1, "low": close - 0.1, "volume": 1.0},
        index=idx,
    )


def test_mtf_returns_only_closed_bars():
    df = _m1(200)
    ctx = MTFContext(base=df, timeframes=["M5"])
    # Pick an M1 bar in the middle of the first M5 bar (e.g. t = 00:03)
    t = df.index[3].to_pydatetime()
    # First M5 bar covers 00:00-00:05, closes at 00:05. At 00:03 NO
    # M5 bar has closed yet.
    assert ctx.last_closed_idx("M5", t) is None
    # At 00:05 the first M5 bar (start=00:00) has just closed.
    t2 = df.index[5].to_pydatetime()
    pos = ctx.last_closed_idx("M5", t2)
    assert pos == 0
    # At 00:09 still only one M5 bar closed (next closes at 00:10).
    t3 = df.index[9].to_pydatetime()
    assert ctx.last_closed_idx("M5", t3) == 0
    # At 00:10 the second M5 bar has closed.
    t4 = df.index[10].to_pydatetime()
    assert ctx.last_closed_idx("M5", t4) == 1


def test_mtf_close_matches_m1_when_aligned():
    """The M5 bar that closes at t=00:05 should have close == the M1
    bar's close at t=00:04 (last M1 in the window)."""
    df = _m1(60)
    ctx = MTFContext(base=df, timeframes=["M5"])
    t = df.index[5].to_pydatetime()
    bar = ctx.last_closed("M5", t)
    assert bar is not None
    assert bar["close"] == pytest.approx(df.iloc[4]["close"])


def test_mtf_h1_in_m1_context():
    df = _m1(180)  # 3 hours
    ctx = MTFContext(base=df, timeframes=["H1"])
    # At t=00:30 no H1 bar closed.
    assert ctx.last_closed_idx("H1", df.index[30].to_pydatetime()) is None
    # At t=01:00 the first H1 bar (00:00-01:00) just closed.
    pos = ctx.last_closed_idx("H1", df.index[60].to_pydatetime())
    assert pos == 0


def test_mtf_unknown_timeframe_raises():
    df = _m1(60)
    ctx = MTFContext(base=df, timeframes=["M5"])
    with pytest.raises(KeyError):
        ctx.last_closed_idx("M15", df.index[10].to_pydatetime())
