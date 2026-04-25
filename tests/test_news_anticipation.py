from pathlib import Path

import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.news_anticipation import NewsAnticipationFade


def _df_with_drift_up_into_event() -> pd.DataFrame:
    """Build the hour leading into a 12:30 event with a clear upside
    drift — the strategy should short-fade that drift before T-0.
    """
    idx = pd.date_range("2026-05-01T11:25:00Z", periods=70, freq="1min")
    rows = []
    base = 2000.0
    for i, ts in enumerate(idx):
        # 11:25 is ~5min before drift_window_min=60 anchor (which is 11:30).
        # Anchor price = close at 11:30. Drift starts 11:30+10=11:40.
        if i < 5:
            o = c = base
            h = base + 0.3
            l = base - 0.3
        elif i < 15:
            # quiet anchor period 11:30-11:40
            o = c = base
            h = base + 0.3
            l = base - 0.3
        elif i < 55:
            # 11:40-12:20: visible upside drift
            move = (i - 15) * 0.06
            o = base + move
            c = base + move + 0.03
            h = c + 0.1
            l = o - 0.1
        else:
            # 12:25 onward: near event window
            o = c = base + 2.5
            h = base + 2.7
            l = base + 2.3
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_news_anticipation_fades_pre_event_drift(tmp_path: Path):
    news = tmp_path / "news.csv"
    news.write_text(
        "time,impact,instrument,event\n"
        "2026-05-01T12:30:00Z,high,*,US test event\n"
    )
    df = _df_with_drift_up_into_event()
    strat = NewsAnticipationFade(
        news_csv=str(news),
        drift_window_min=60,
        delay_min=10,
        exit_buffer_min=5,
        trigger_atr=0.5,
        sl_atr_mult=0.5,
        atr_period=14,
        cooldown_bars=0,
        use_two_legs=False,
        min_history=14,
    )
    strat.prepare(df)
    sig = None
    for i in range(1, len(df) + 1):
        s = strat.on_bar(df.iloc[:i])
        if s is not None:
            sig = s
            break
    assert sig is not None, "Anticipation strategy must fire on the upside drift"
    assert sig.side == SignalSide.SELL
    assert "news-antic" in sig.reason


def test_news_anticipation_does_not_fire_inside_exit_buffer(tmp_path: Path):
    news = tmp_path / "news.csv"
    news.write_text(
        "time,impact,instrument,event\n"
        "2026-05-01T12:30:00Z,high,*,US test event\n"
    )
    df = _df_with_drift_up_into_event()
    # exit_buffer_min=15 means trading window ends at 12:15 — by then
    # the drift has only just started, so the strategy should not fire
    # on the late-event spike that would otherwise trigger it.
    strat = NewsAnticipationFade(
        news_csv=str(news),
        drift_window_min=60,
        delay_min=10,
        exit_buffer_min=15,
        trigger_atr=0.6,
        atr_period=14,
        cooldown_bars=0,
        use_two_legs=False,
        min_history=14,
    )
    strat.prepare(df)
    fires_inside_buffer = 0
    for i in range(1, len(df) + 1):
        s = strat.on_bar(df.iloc[:i])
        if s is None:
            continue
        ts = df.index[i - 1]
        # Must not fire at or after 12:15 (i.e. inside exit buffer).
        if ts >= pd.Timestamp("2026-05-01T12:15:00Z"):
            fires_inside_buffer += 1
    assert fires_inside_buffer == 0
