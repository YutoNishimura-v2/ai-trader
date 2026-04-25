import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.friday_flush import FridayFlushFade


def _friday_df() -> pd.DataFrame:
    """Build a Friday window 17:00-20:00 UTC with a clear late-day
    drive that the strategy should fade.
    """
    idx = pd.date_range("2026-04-24T17:00:00Z", periods=180, freq="1min")
    rows = []
    base = 2000.0
    for i, ts in enumerate(idx):
        if i < 60:
            # 17:00-18:00 quiet leadup, anchor formed at 18:00
            o = c = base
            h = base + 0.4
            l = base - 0.4
        elif i < 100:
            # 18:00-18:40 still quiet
            o = c = base
            h = base + 0.5
            l = base - 0.5
        elif i < 130:
            # 18:40-19:10 strong upside drive (the "flush")
            move = (i - 100) * 0.15
            o = base + move
            c = base + move + 0.05
            h = c + 0.1
            l = o - 0.1
        else:
            # 19:10-20:00 hold near the high
            o = c = base + 5.0
            h = base + 5.2
            l = base + 4.8
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_friday_flush_fades_late_day_drive():
    df = _friday_df()
    strat = FridayFlushFade(
        anchor_hour=18,
        delay_min=30,
        fri_close_hour=20,
        trigger_atr=0.5,
        sl_atr_mult=0.5,
        atr_period=14,
        cooldown_bars=0,
        min_history=20,
    )
    strat.prepare(df)
    sig = None
    fired_at = None
    for i in range(1, len(df) + 1):
        s = strat.on_bar(df.iloc[:i])
        if s is not None:
            sig = s
            fired_at = i - 1
            break
    assert sig is not None, "Friday flush should fire after the upside drive"
    # Faded an UP move => SELL
    assert sig.side == SignalSide.SELL
    assert "friday-flush" in sig.reason
    # Anchor TP should be back near base (2000)
    assert sig.legs[-1].take_profit < float(df.iloc[fired_at]["close"])


def test_friday_flush_does_not_fire_on_other_days():
    """Same shape but on Thursday: must not fire."""
    idx = pd.date_range("2026-04-23T17:00:00Z", periods=180, freq="1min")
    fri = _friday_df()
    df = fri.copy()
    df.index = idx
    strat = FridayFlushFade(trigger_atr=0.5, atr_period=14, min_history=20)
    strat.prepare(df)
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        assert sig is None, "Strategy must not fire on a non-Friday"


def test_friday_flush_one_trade_per_day():
    df = _friday_df()
    strat = FridayFlushFade(trigger_atr=0.5, atr_period=14, cooldown_bars=0, min_history=20)
    strat.prepare(df)
    fires = 0
    for i in range(1, len(df) + 1):
        if strat.on_bar(df.iloc[:i]) is not None:
            fires += 1
    assert fires == 1
