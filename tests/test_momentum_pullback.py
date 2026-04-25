import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.momentum_pullback import MomentumPullback


def _df() -> pd.DataFrame:
    idx = pd.date_range("2026-05-01T07:00:00Z", periods=120, freq="1min")
    rows = []
    price = 2000.0
    for i, _ in enumerate(idx):
        if i < 50:
            o = price
            c = price + 0.05
            h = c + 0.1
            l = o - 0.1
            price = c
        elif i == 50:
            o = price
            c = price + 6.0
            h = c + 0.4
            l = o - 0.2
            price = c
        elif i in (51, 52, 53):
            o = price
            c = price - 1.0
            h = o + 0.1
            l = c - 0.2
            price = c
        elif i == 54:
            o = price - 0.5
            c = price + 1.0
            h = c + 0.2
            l = o - 1.2
            price = c
        else:
            o = price
            c = price + 0.05
            h = c + 0.1
            l = o - 0.1
            price = c
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def test_momentum_pullback_fires_after_impulse_and_retest():
    df = _df()
    strat = MomentumPullback(
        impulse_body_atr=1.0,
        fib_min=0.25,
        fib_max=0.8,
        session="always",
        min_history=30,
    )
    strat.prepare(df)
    sig = None
    for i in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:i])
        if sig is not None:
            break
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "momentum-pullback long" in sig.reason
    assert len(sig.legs) == 2
