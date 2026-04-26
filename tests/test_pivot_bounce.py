from __future__ import annotations

import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.pivot_bounce import PivotBounce


def _bars() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01 00:00", periods=6 * 60 + 2, freq="min", tz="UTC")
    df = pd.DataFrame(index=idx)
    df["open"] = 100.0
    df["high"] = 101.0
    df["low"] = 99.0
    df["close"] = 100.0
    df["volume"] = 1.0

    # Prior 4h bucket (00:00-03:59): H=110 L=90 C=96 => P=98.6667, S1=87.3333, R1=107.3333
    early = df.index < pd.Timestamp("2026-01-01 04:00", tz="UTC")
    df.loc[early, "high"] = 110.0
    df.loc[early, "low"] = 90.0
    df.loc[early, "close"] = 96.0

    # Current 4h bucket starts at 04:00. Make 04:30 a support rejection.
    current = df.index >= pd.Timestamp("2026-01-01 04:00", tz="UTC")
    df.loc[current, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
    t = pd.Timestamp("2026-01-01 04:30", tz="UTC")
    df.loc[t, ["open", "high", "low", "close"]] = [87.4, 88.4, 87.0, 87.8]
    return df


def test_pivot_bounce_4h_uses_prior_bucket_and_emits_meta() -> None:
    df = _bars()
    strat = PivotBounce(
        pivot_period="4h",
        session=None,
        atr_period=2,
        min_history=10,
        touch_atr_buf=0.20,
        sl_atr_buf=0.05,
        max_sl_atr=2.0,
        cooldown_bars=1,
        use_s2r2=False,
        levels=["S1"],
        risk_multiplier=0.5,
        confidence=0.8,
        emit_context_meta=True,
    )
    strat.prepare(df)

    sig = strat.on_bar(df.loc[: "2026-01-01 04:30"])

    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "S1" in sig.reason
    assert sig.meta is not None
    assert sig.meta["pivot_period"] == "4h"
    assert sig.meta["pivot_level"] == "S1"
    assert sig.meta["risk_multiplier"] == 0.5
    assert sig.meta["confidence"] == 0.8


def test_pivot_bounce_level_filter_blocks_unlisted_level() -> None:
    df = _bars()
    strat = PivotBounce(
        pivot_period="4h",
        session=None,
        atr_period=2,
        min_history=10,
        touch_atr_buf=0.20,
        sl_atr_buf=0.05,
        cooldown_bars=1,
        use_s2r2=False,
        levels=["R1"],
    )
    strat.prepare(df)

    assert strat.on_bar(df.loc[: "2026-01-01 04:30"]) is None

