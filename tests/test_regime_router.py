from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide
from ai_trader.strategy.regime_router import RegimeRouterStrategy
from ai_trader.strategy.registry import register_strategy


class _AlwaysFireForRouter(BaseStrategy):
    name = "_always_fire_for_router"

    def __init__(self, side: str = "buy"):
        super().__init__(side=side)
        self.min_history = 1
        self.side = SignalSide(side)

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        close = float(history.iloc[-1]["close"])
        if self.side == SignalSide.BUY:
            return Signal(
                side=SignalSide.BUY,
                entry=None,
                stop_loss=close - 1.0,
                take_profit=close + 1.0,
                reason="stub",
            )
        return Signal(
            side=SignalSide.SELL,
            entry=None,
            stop_loss=close + 1.0,
            take_profit=close - 1.0,
            reason="stub",
        )


try:
    register_strategy(_AlwaysFireForRouter)
except ValueError:
    pass


def _trend_df(n: int = 500) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    close = 2000.0 + np.arange(n) * 0.25
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close + 0.1,
            "volume": 1.0,
        },
        index=idx,
    )


def _chop_df(n: int = 500) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    close = 2000.0 + np.sin(np.arange(n) / 5.0) * 0.5
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


def test_regime_router_routes_trend_member():
    df = _trend_df()
    router = RegimeRouterStrategy(
        htf="M5",
        trend_adx_min=20.0,
        members=[
            {"name": "_always_fire_for_router", "regimes": ["trend"], "params": {"side": "buy"}},
            {"name": "_always_fire_for_router", "regimes": ["range"], "params": {"side": "sell"}},
        ],
    )
    router.prepare(df)
    sig = router.on_bar(df.iloc[:400])
    assert sig is not None
    assert sig.side == SignalSide.BUY
    assert "|trend]" in sig.reason


def test_regime_router_routes_chop_member():
    df = _chop_df()
    router = RegimeRouterStrategy(
        htf="M5",
        range_adx_max=25.0,
        members=[
            {"name": "_always_fire_for_router", "regimes": ["trend"], "params": {"side": "buy"}},
            {"name": "_always_fire_for_router", "regimes": ["range"], "params": {"side": "sell"}},
        ],
    )
    router.prepare(df)
    sig = router.on_bar(df.iloc[:400])
    assert sig is not None
    assert sig.side == SignalSide.SELL
    assert "|range]" in sig.reason


def test_regime_router_requires_members():
    with pytest.raises(ValueError, match="members"):
        RegimeRouterStrategy(members=[])
