"""Tests for NewsContinuation."""
import os
import tempfile

import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.news_continuation import NewsContinuation


def _df_with_uptrend_after_event(periods: int = 200) -> pd.DataFrame:
    """600 minutes of M1 around event T at 12:30. Anchor = 2000.
    After T+5 the price drifts UP by ~1.5%. Confirm bars all positive."""
    idx = pd.date_range("2026-05-04T12:00:00Z", periods=periods, freq="1min")
    rows = []
    event_min = 30  # 12:30 = minute 30 of dataset
    for i, ts in enumerate(idx):
        if i < event_min:
            o = c = 2000.0
            h = 2000.5
            l = 1999.5
        elif i < event_min + 5:
            # initial spike
            o = c = 2000.0 + (i - event_min) * 1.0
            h = c + 0.5
            l = o - 0.3
        else:
            # sustained uptrend +1.0/bar after delay
            base = 2010.0 + (i - event_min - 5) * 1.0
            o = c = base
            h = base + 0.5
            l = base - 0.3
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def _news_csv(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "news.csv")
    with open(path, "w") as f:
        f.write("time,impact,instrument,event\n")
        f.write("2026-05-04T12:30:00Z,high,*,Test Event\n")
    return path


def test_news_continuation_long_after_sustained_drift():
    df = _df_with_uptrend_after_event()
    with tempfile.TemporaryDirectory() as tmp:
        nc = _news_csv(tmp)
        strat = NewsContinuation(
            news_csv=nc,
            impact_filter=("high",),
            delay_min=5,
            window_min=60,
            trigger_atr=0.5,  # synthetic data has small ATR
            confirm_bars=3,
            sl_atr_mult=0.8,
            tp_rr=2.0,
            atr_period=14,
            cooldown_bars=2,
            min_history=20,
            symbol="XAUUSD",
        )
        strat.prepare(df)
        sig = None
        for i in range(1, len(df) + 1):
            sig = strat.on_bar(df.iloc[:i])
            if sig is not None:
                break
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "news-cont long" in sig.reason
    assert len(sig.legs) == 2


def test_news_continuation_no_signal_outside_window():
    """Before delay_min and after window_min there is no signal."""
    df = _df_with_uptrend_after_event()
    with tempfile.TemporaryDirectory() as tmp:
        nc = _news_csv(tmp)
        strat = NewsContinuation(
            news_csv=nc,
            impact_filter=("high",),
            delay_min=200,  # Delay larger than dataset = never active
            window_min=60,
            trigger_atr=0.5,
            confirm_bars=3,
            min_history=20,
            symbol="XAUUSD",
        )
        strat.prepare(df)
        sig = None
        for i in range(1, len(df) + 1):
            sig = strat.on_bar(df.iloc[:i])
            if sig is not None:
                break
    assert sig is None


def test_news_continuation_one_trade_per_event():
    """Cooldown + fired_events: only one signal per event."""
    df = _df_with_uptrend_after_event(periods=300)
    with tempfile.TemporaryDirectory() as tmp:
        nc = _news_csv(tmp)
        strat = NewsContinuation(
            news_csv=nc,
            impact_filter=("high",),
            delay_min=5,
            window_min=60,
            trigger_atr=0.5,
            confirm_bars=3,
            cooldown_bars=2,
            min_history=20,
            symbol="XAUUSD",
        )
        strat.prepare(df)
        seen = 0
        for i in range(1, len(df) + 1):
            sig = strat.on_bar(df.iloc[:i])
            if sig is not None:
                seen += 1
        # Even with the uptrend continuing, only one signal per event.
        assert seen == 1
