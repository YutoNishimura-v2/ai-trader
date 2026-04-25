import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.session_sweep_reclaim import SessionSweepReclaim


def _df() -> pd.DataFrame:
    idx = pd.date_range("2026-05-01T00:00:00Z", periods=600, freq="1min")
    rows = []
    for i, ts in enumerate(idx):
        minute = ts.hour * 60 + ts.minute
        if 0 <= minute < 300:
            o = c = 2000.0
            h = 2002.0
            l = 1998.0
        elif minute == 420:
            o = 2000.0
            c = 1997.5
            h = 2000.5
            l = 1996.0
        elif minute == 421:
            o = 1997.2
            c = 1999.7
            h = 1999.6
            l = 1996.8
        else:
            o = c = 1999.4
            h = 2000.0
            l = 1998.8
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_session_sweep_reclaim_fires_after_reclaim():
    df = _df()
    strat = SessionSweepReclaim(
        trade_start_hour=7,
        trade_end_hour=10,
        min_range_atr=0.1,
        min_sweep_atr=0.1,
        sl_atr_buffer=0.05,
        max_sl_atr=2.0,
        min_history=10,
    )
    strat.prepare(df)
    sig = None
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "session-sweep-reclaim long" in sig.reason
    assert len(sig.legs) == 2
