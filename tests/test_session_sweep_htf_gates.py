"""Regression tests for the HTF gates added to session_sweep_reclaim.

The gates are honest losers in production research (see lessons_learned),
but the unit tests still need to lock the gating behaviour so future
changes to the gate semantics are caught.
"""
from __future__ import annotations

import pandas as pd

from ai_trader.strategy.session_sweep_reclaim import SessionSweepReclaim


def _trending_up_day_with_sweep_above() -> pd.DataFrame:
    """A day where the M15 trend is strongly up over many earlier
    days, and on the test day the Asian-range high is swept above
    and reclaimed back inside. The default strategy would short-fade;
    with htf_mode=skip_counter_trend on a +1 bias day, the short
    reclaim must be suppressed.

    The data spans 4 days so the M15 EMA fast/slow on the close-only
    series can fully form a positive bias before the sweep day.
    """
    days = 4
    idx = pd.date_range("2026-04-19T00:00:00Z", periods=days * 24 * 60, freq="1min")
    rows = []
    sweep_day = pd.Timestamp("2026-04-22T00:00:00Z")
    range_hi_target = 2050.0
    range_lo_target = 2048.0
    for ts in idx:
        # background uptrend across days
        days_in = (ts - idx[0]).total_seconds() / 86400.0
        base = 2000.0 + 12.0 * days_in
        if ts < sweep_day:
            # Quiet uptrend: each bar nudges close upward
            o = c = base
            h = base + 0.5
            l = base - 0.5
        else:
            # Sweep day
            if ts.hour < 6:
                # Asian range pinned to [range_lo_target, range_hi_target]
                base = (range_hi_target + range_lo_target) / 2
                o = c = base
                h = range_hi_target
                l = range_lo_target
            elif ts.hour < 7:
                o = c = (range_hi_target + range_lo_target) / 2
                h = o + 0.3
                l = o - 0.3
            elif ts.hour == 7 and ts.minute == 30:
                # Sweep above range high, close back inside.
                o = range_hi_target + 1.0
                c = (range_hi_target + range_lo_target) / 2  # firmly inside
                h = range_hi_target + 3.0  # spike high
                l = c - 0.2
            else:
                o = c = (range_hi_target + range_lo_target) / 2
                h = o + 0.3
                l = o - 0.3
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_skip_counter_trend_blocks_short_in_uptrend():
    df = _trending_up_day_with_sweep_above()
    base = SessionSweepReclaim(
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=7, trade_end_hour=12,
        atr_period=14,
        min_range_atr=0.0,
        min_sweep_atr=0.05,
        sl_atr_buffer=0.3,
        max_sl_atr=2.0,
        tp_mode="rr",
        tp1_rr=1.0, tp2_rr=1.5,
        leg1_weight=0.5,
        max_trades_per_day=2,
        min_history=20,
    )
    base.prepare(df)
    base_fires = sum(1 for i in range(1, len(df) + 1) if base.on_bar(df.iloc[:i]) is not None)
    assert base_fires >= 1, "baseline must fire on this synthetic sweep"

    gated = SessionSweepReclaim(
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=7, trade_end_hour=12,
        atr_period=14,
        min_range_atr=0.0,
        min_sweep_atr=0.05,
        sl_atr_buffer=0.3,
        max_sl_atr=2.0,
        tp_mode="rr",
        tp1_rr=1.0, tp2_rr=1.5,
        leg1_weight=0.5,
        max_trades_per_day=2,
        htf="M15",
        htf_ema_fast=5, htf_ema_slow=10,
        htf_mode="skip_counter_trend",
        min_history=20,
    )
    gated.prepare(df)
    gated_fires = sum(1 for i in range(1, len(df) + 1) if gated.on_bar(df.iloc[:i]) is not None)
    # Strict-uptrend short-reclaim must be suppressed by the gate.
    assert gated_fires == 0
