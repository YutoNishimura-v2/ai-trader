from pathlib import Path

import pandas as pd

from ai_trader.strategy.news_breakout import NewsBreakout
from ai_trader.strategy.base import SignalSide


def _df() -> pd.DataFrame:
    idx = pd.date_range("2026-05-01T12:20:00Z", periods=40, freq="1min")
    rows = []
    for i, _ in enumerate(idx):
        if i < 10:
            o = c = 2000.0
            h = 2000.5
            l = 1999.5
        elif i < 15:
            o = c = 2000.0
            h = 2002.0
            l = 1998.0
        elif i == 15:
            o = 2003.8
            c = 2004.8
            h = 2005.0
            l = 2003.0
        elif i == 16:
            o = 2002.0
            c = 2004.2
            h = 2004.5
            l = 2001.2
        elif i == 17:
            o = 2003.0
            c = 2002.7
            h = 2003.3
            l = 2001.8
        elif i == 18:
            o = 2002.3
            c = 2003.8
            h = 2004.1
            l = 2001.9
        else:
            o = c = 2003.8
            h = 2004.2
            l = 2003.4
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_news_breakout_fires_after_range_break_and_retest(tmp_path: Path):
    news = tmp_path / "news.csv"
    news.write_text(
        "time,impact,instrument,event\n"
        "2026-05-01T12:30:00Z,high,*,US test event\n"
    )
    df = _df()
    strat = NewsBreakout(
        news_csv=str(news),
        delay_min=0,
        initial_range_min=5,
        window_min=30,
        cooldown_bars=0,
        break_atr=0.0,
        retest_tolerance_atr=2.5,
        min_history=5,
    )
    strat.prepare(df)
    sig = None
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "news-breakout long" in sig.reason
    assert len(sig.legs) == 2

