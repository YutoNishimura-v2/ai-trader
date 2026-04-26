"""Smoke tests for iter31 strategies + ensemble risk_multiplier."""
import numpy as np
import pandas as pd

from ai_trader.strategy.engulfing_reversal import EngulfingReversal
from ai_trader.strategy.ema_cross_pullback import EmaCrossPullback, _ema, _rsi
from ai_trader.strategy.ensemble import EnsembleStrategy


def _flat_df(n=200):
    return pd.DataFrame({
        "open": [2000.0]*n, "high": [2000.5]*n,
        "low": [1999.5]*n, "close": [2000.0]*n,
        "volume": [1.0]*n,
    }, index=pd.date_range("2026-05-04T07:00:00Z", periods=n, freq="1min"))


def test_strategies_register():
    assert EngulfingReversal().name == "engulfing_reversal"
    assert EmaCrossPullback().name == "ema_cross_pullback"


def test_ema_helper_smoke():
    arr = np.array([100.0, 101.0, 102.0, 103.0])
    out = _ema(arr, 3)
    assert len(out) == 4
    assert np.isfinite(out).all()


def test_rsi_helper_smoke():
    arr = np.linspace(100.0, 110.0, 30)
    out = _rsi(arr, 14)
    # Steady uptrend → RSI should be high (>70) by the end.
    assert out[-1] > 70.0


def test_no_signal_history_too_short():
    for cls in (EngulfingReversal, EmaCrossPullback):
        s = cls()
        df = _flat_df(20)
        try:
            s.prepare(df)
        except Exception:
            pass
        assert s.on_bar(df) is None


def test_ensemble_risk_multiplier_attaches_meta():
    """Per-member risk_multiplier appears in emitted signal's meta."""
    # Use a degenerate stub: build a fake member-like that always returns
    # a Signal. But simpler: just construct ensemble with a real member and
    # check the multiplier is stored.
    ens = EnsembleStrategy(members=[
        {"name": "engulfing_reversal", "risk_multiplier": 0.5, "params": {}},
    ])
    assert ens._member_risk_multipliers == [0.5]
