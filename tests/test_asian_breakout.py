"""Tests for AsianBreakout (the trend-day complement to SessionSweepReclaim).

Each test builds a synthetic, deterministic OHLCV frame with known
Asian range, known M15 trend, and verifies the binary outcome.
"""
import numpy as np
import pandas as pd

from ai_trader.strategy.asian_breakout import AsianBreakout
from ai_trader.strategy.base import SignalSide


def _trend_up_df(periods: int = 600) -> pd.DataFrame:
    """600 minutes of M1 starting 00:00 UTC.

    - 00:00-04:59 (Asian box): tight range around 2000.
    - 05:00-06:59: small drift up to seed M15 trend.
    - 07:00-07:01 (active window): explicit upside breakout.
    - 07:02+: continued drift up.
    """
    idx = pd.date_range("2026-05-04T00:00:00Z", periods=periods, freq="1min")
    rows = []
    for ts in idx:
        m = ts.hour * 60 + ts.minute
        if m < 300:  # Asian box 00:00-04:59
            o = c = 2000.0
            h = 2001.0
            l = 1999.0
        elif m < 420:  # 05:00-06:59 drift up
            base = 2000.0 + (m - 300) * 0.05  # +6 over 120m
            o = c = base
            h = base + 0.5
            l = base - 0.5
        elif m == 420:  # 07:00 — close > 2001 (= range_hi) + buffer
            o = 2006.0
            c = 2008.5
            h = 2009.0
            l = 2005.5
        else:  # continue up
            base = 2008.5 + (m - 420) * 0.02
            o = c = base
            h = base + 0.5
            l = base - 0.5
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def _trend_down_df(periods: int = 600) -> pd.DataFrame:
    idx = pd.date_range("2026-05-04T00:00:00Z", periods=periods, freq="1min")
    rows = []
    for ts in idx:
        m = ts.hour * 60 + ts.minute
        if m < 300:
            o = c = 2000.0
            h = 2001.0
            l = 1999.0
        elif m < 420:
            base = 2000.0 - (m - 300) * 0.05
            o = c = base
            h = base + 0.5
            l = base - 0.5
        elif m == 420:
            o = 1994.0
            c = 1991.5
            h = 1994.5
            l = 1991.0
        else:
            base = 1991.5 - (m - 420) * 0.02
            o = c = base
            h = base + 0.5
            l = base - 0.5
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def _no_trend_df(periods: int = 600) -> pd.DataFrame:
    """Asian box + flat afternoon = M15 ADX low; no breakout signal."""
    idx = pd.date_range("2026-05-04T00:00:00Z", periods=periods, freq="1min")
    rows = []
    for ts in idx:
        m = ts.hour * 60 + ts.minute
        if m < 300:
            o = c = 2000.0
            h = 2001.0
            l = 1999.0
        else:
            o = c = 2000.0
            h = 2000.5
            l = 1999.5
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def _run_until_signal(strat: AsianBreakout, df: pd.DataFrame):
    sig = None
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    return sig


def test_long_breakout_fires_when_uptrend_aligned():
    df = _trend_up_df()
    strat = AsianBreakout(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        break_atr=0.05,
        sl_atr_buffer=0.05,
        max_sl_atr=2.0,
        min_trend_adx=10.0,  # synthetic data has weak ADX
        min_history=10,
    )
    strat.prepare(df)
    # Force the HTF bias arrays to known values: fast > slow + ADX > min.
    assert strat._htf_fast is not None and strat._htf_slow is not None
    strat._htf_fast[:] = 2010.0
    strat._htf_slow[:] = 2000.0
    strat._htf_adx[:] = 25.0
    sig = _run_until_signal(strat, df)
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "asian-break long" in sig.reason
    assert len(sig.legs) == 2


def test_short_breakout_fires_when_downtrend_aligned():
    df = _trend_down_df()
    strat = AsianBreakout(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        break_atr=0.05,
        sl_atr_buffer=0.05,
        max_sl_atr=2.0,
        min_trend_adx=10.0,
        min_history=10,
    )
    strat.prepare(df)
    strat._htf_fast[:] = 1990.0
    strat._htf_slow[:] = 2000.0
    strat._htf_adx[:] = 25.0
    sig = _run_until_signal(strat, df)
    assert sig is not None
    assert sig.side == SignalSide.SELL
    assert "asian-break short" in sig.reason
    assert len(sig.legs) == 2


def test_long_blocked_when_htf_bias_down():
    """The whole point: don't fire long against an HTF downtrend."""
    df = _trend_up_df()
    strat = AsianBreakout(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        break_atr=0.05,
        sl_atr_buffer=0.05,
        max_sl_atr=2.0,
        min_trend_adx=10.0,
        min_history=10,
    )
    strat.prepare(df)
    # Bias DOWN: fast < slow but breakout shows price action UP.
    # The trend gate should suppress the long.
    strat._htf_fast[:] = 1990.0
    strat._htf_slow[:] = 2000.0
    strat._htf_adx[:] = 25.0
    sig = _run_until_signal(strat, df)
    assert sig is None


def test_no_signal_when_adx_below_threshold():
    """ADX gate suppresses signals in chop."""
    df = _trend_up_df()
    strat = AsianBreakout(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        break_atr=0.05,
        sl_atr_buffer=0.05,
        max_sl_atr=2.0,
        min_trend_adx=22.0,
        min_history=10,
    )
    strat.prepare(df)
    strat._htf_fast[:] = 2010.0
    strat._htf_slow[:] = 2000.0
    # ADX UNDER threshold - should suppress.
    strat._htf_adx[:] = 18.0
    sig = _run_until_signal(strat, df)
    assert sig is None


def test_max_trades_per_day_respected():
    df = _trend_up_df(periods=600)
    strat = AsianBreakout(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        break_atr=0.05,
        sl_atr_buffer=0.05,
        max_sl_atr=2.0,
        min_trend_adx=10.0,
        max_trades_per_day=1,
        cooldown_bars=1,
        min_history=10,
    )
    strat.prepare(df)
    strat._htf_fast[:] = 2010.0
    strat._htf_slow[:] = 2000.0
    strat._htf_adx[:] = 25.0
    seen = 0
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            seen += 1
            # Re-fire same direction must be suppressed by _fired_long
            assert sig.side == SignalSide.BUY
    assert seen == 1


def test_sl_capped_by_max_sl_atr():
    df = _trend_up_df()
    strat = AsianBreakout(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        break_atr=0.05,
        sl_atr_buffer=0.05,
        max_sl_atr=0.10,  # very tight cap, forces capped path
        min_trend_adx=10.0,
        min_history=10,
    )
    strat.prepare(df)
    strat._htf_fast[:] = 2010.0
    strat._htf_slow[:] = 2000.0
    strat._htf_adx[:] = 25.0
    sig = _run_until_signal(strat, df)
    assert sig is not None
    # Entry at the breakout close. SL is capped tight by max_sl_atr=0.10
    # so the structural SL (range_lo - buffer) does NOT bind; the cap does.
    # Verify SL distance is less than 1 ATR (i.e., the cap engaged).
    # ATR on the breakout bar is several dollars; cap=0.10*ATR is < 1 ATR.
    assert np.isfinite(sig.stop_loss)
    # SL must still be below entry (BUY signal).
    sl_distance = float("nan")
    # Find the entry bar index implicitly: signal fires on bar 420 (the
    # explicit-breakout bar). Use that bar's close as entry reference.
    entry_ref = float(df.iloc[420]["close"])
    sl_distance = entry_ref - sig.stop_loss
    assert sl_distance > 0
    # And it should be tight (< 5.0 in absolute price - the structural SL
    # would be ~3 below entry; the cap of 0.10*ATR is much tighter).
    assert sl_distance < 5.0
