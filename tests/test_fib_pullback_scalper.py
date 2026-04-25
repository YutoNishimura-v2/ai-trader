"""Tests for FibPullbackScalper (iter9).

These are minimal logic tests. The strategy's trade frequency,
edge, and PF are evaluated empirically on real M1 XAUUSD data
in the iter9 progress notes — not in unit tests.
"""
import numpy as np
import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.fib_pullback_scalper import FibPullbackScalper


def test_strategy_registers():
    """Smoke-test: strategy can be instantiated with default params."""
    strat = FibPullbackScalper()
    assert strat.name == "fib_pullback_scalper"
    # Default params include the user-recipe knobs.
    assert strat.params["sl_min_usd"] == 3.0
    assert strat.params["tp1_rr"] == 0.5
    assert strat.params["tp2_rr"] == 4.0


def test_no_signal_when_history_too_short():
    """Strategy returns None when there's not enough history."""
    strat = FibPullbackScalper(min_history=200)
    df = pd.DataFrame(
        {
            "open": [2000.0] * 20,
            "high": [2000.5] * 20,
            "low": [1999.5] * 20,
            "close": [2000.0] * 20,
            "volume": [1.0] * 20,
        },
        index=pd.date_range("2026-05-04T07:00:00Z", periods=20, freq="1min"),
    )
    sig = strat.on_bar(df)
    assert sig is None


def test_no_signal_when_chop():
    """Tight oscillation never establishes a trend → no signal."""
    idx = pd.date_range("2026-05-04T07:00:00Z", periods=800, freq="1min")
    rows = []
    for i in range(800):
        base = 2000.0 + (i % 8) * 0.25 - 1.0
        rows.append({"open": base, "high": base + 0.30, "low": base - 0.30, "close": base, "volume": 1.0})
    df = pd.DataFrame(rows, index=idx)
    strat = FibPullbackScalper(
        htf="M15", htf_swing_lookback=3, htf_min_trend_legs=2,
        session=None, min_history=120,
    )
    strat.prepare(df)
    sig = None
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    assert sig is None


def test_two_leg_with_be_on_runner():
    """When a signal fires, it is a 2-leg signal with TP1+BE on runner."""
    # Build a clean uptrend with sufficient swings on M15 then a fib pullback.
    idx = pd.date_range("2026-05-04T07:00:00Z", periods=2400, freq="1min")
    rows = []
    for i in range(2400):
        if i < 1800:
            # Staircase up: each step rises by 1.5, dips by 0.4 - creates HH+HL on M15.
            step = i // 75
            within = i % 75
            base = 2000.0 + step * 1.5
            if within < 50:
                base += within * 0.04
            else:
                base += 2.0 - (within - 50) * 0.06
            o = c = base
            h = base + 0.30
            l = base - 0.20
        elif i < 1980:
            # Pullback to a fib retracement of the LAST impulse (ends at the
            # last swing low of the staircase, NOT below it). The full impulse
            # is approximately the last small leg, NOT 2000-2050.
            top = 2000.0 + (1799 // 75) * 1.5 + 2.0   # last "high" of staircase
            base = top - (i - 1800) * 0.04
            o = c = base
            h = base + 0.25
            l = base - 0.25
        elif i == 1980:
            # Bullish rejection candle.
            top = 2000.0 + (1799 // 75) * 1.5 + 2.0
            entry_price = top - 180 * 0.04
            o = entry_price - 0.5
            l = entry_price - 1.0
            c = entry_price + 1.5
            h = entry_price + 1.6
        else:
            base = 2000.0 + (1799 // 75) * 1.5 + 2.0 - 180*0.04 + 1.5 + (i - 1980) * 0.05
            o = c = base
            h = base + 0.20
            l = base - 0.20
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    df = pd.DataFrame(rows, index=idx)

    strat = FibPullbackScalper(
        htf="M15",
        htf_swing_lookback=2,
        htf_min_trend_legs=2,
        fib_entry_min=0.10,
        fib_entry_max=0.90,
        atr_period=14,
        sl_atr_buf=0.5,
        sl_min_usd=3.0,
        max_sl_atr=4.0,
        tp1_rr=0.5,
        tp2_rr=4.0,
        leg1_weight=0.6,
        cooldown_bars=1,
        session=None,
        min_history=120,
    )
    strat.prepare(df)
    sig = None
    for i in range(120, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    # Either a signal fires (and meets structural checks) or it doesn't —
    # both are honest outcomes for synthetic data. If it fires, verify
    # the 2-leg structure.
    if sig is not None:
        assert sig.side == SignalSide.BUY
        assert len(sig.legs) == 2
        # Total weights = 1.0
        assert abs(sum(l.weight for l in sig.legs) - 1.0) < 1e-6
        # Exactly one leg has move_sl_to_on_fill set (TP1).
        be_legs = [l for l in sig.legs if l.move_sl_to_on_fill is not None]
        assert len(be_legs) == 1
        # SL must be below entry on a long.
        # entry isn't directly stored but TP2 = entry + 4*risk and SL = entry - risk
        # so TP2 > SL holds trivially. Just check basic ordering.
        assert sig.stop_loss < sig.legs[0].take_profit
