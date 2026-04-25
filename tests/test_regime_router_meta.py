from datetime import timezone

import numpy as np
import pandas as pd

from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide
from ai_trader.strategy.regime_router import RegimeRouterStrategy
from ai_trader.strategy.registry import register_strategy


class _MetaStubStrategy(BaseStrategy):
    name = "_meta_stub_strategy"

    def __init__(self):
        super().__init__()
        self.min_history = 1

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        close = float(history.iloc[-1]["close"])
        return Signal(
            side=SignalSide.BUY,
            entry=None,
            stop_loss=close - 1.0,
            take_profit=close + 1.0,
            reason="meta-stub",
        )


try:
    register_strategy(_MetaStubStrategy)
except ValueError:
    pass


def _trend_df(n: int = 600) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    close = 2000.0 + np.arange(n) * 0.2
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close + 0.1,
            "volume": 1.0,
        },
        index=idx,
    )


def test_regime_router_sets_risk_meta():
    df = _trend_df()
    router = RegimeRouterStrategy(
        htf="M5",
        trend_adx_min=20.0,
        adx_confidence_weight=0.0,
        regime_risk_multipliers={"trend": 1.2},
        member_risk_multipliers={"_meta_stub_strategy": 1.1},
        regime_confidence={"trend": 0.7},
        members=[
            {"name": "_meta_stub_strategy", "regimes": ["trend"], "params": {}},
        ],
    )
    router.prepare(df)
    sig = router.on_bar(df.iloc[:500])
    assert sig is not None
    assert sig.meta is not None
    assert sig.meta["router_member"] == "_meta_stub_strategy"
    assert sig.meta["regime"] == "trend"
    assert sig.meta["risk_multiplier"] == 1.32
    assert sig.meta["confidence"] == 0.7
