import numpy as np
import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.squeeze_breakout import SqueezeBreakout


def _df() -> pd.DataFrame:
    idx = pd.date_range("2026-05-01T00:00:00Z", periods=140, freq="1min")
    rows = []
    price = 2000.0
    for i in range(len(idx)):
        if i < 100:
            # Long, low-volatility compression.
            price = 2000.0 + 0.05 * np.sin(i / 2.0)
            o = c = price
            h = price + 0.08
            l = price - 0.08
        elif i == 100:
            o = 2000.0
            c = 2003.0
            h = 2003.2
            l = 1999.9
        else:
            o = c = 2003.0
            h = 2003.2
            l = 2002.8
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_squeeze_breakout_fires_on_compression_release():
    df = _df()
    strat = SqueezeBreakout(
        bb_n=20,
        squeeze_lookback=40,
        break_atr=0.2,
        min_history=40,
        session="always",
    )
    strat.prepare(df)
    sig = None
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "squeeze-breakout long" in sig.reason
    assert len(sig.legs) == 2
