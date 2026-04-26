"""Engine fires BaseStrategy.on_trade_closed correctly and causally."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import (
    BaseStrategy,
    ClosedTradeContext,
    Signal,
    SignalSide,
)


class _RecordingStrategy(BaseStrategy):
    """Fires a BUY once after warmup; records every close callback."""

    name = "recording_test_only"

    def __init__(self, warmup: int = 5, tp_up: float = 2.0, sl_down: float = 2.0) -> None:
        super().__init__()
        self.warmup = warmup
        self.tp_up = tp_up
        self.sl_down = sl_down
        self._fired = False
        self.observed_bar_at_close: list[pd.Timestamp] = []
        self.closes: list[ClosedTradeContext] = []
        self._last_history_ts: pd.Timestamp | None = None

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        self._last_history_ts = history.index[-1]
        if self._fired or len(history) < self.warmup:
            return None
        self._fired = True
        last = float(history.iloc[-1]["close"])
        return Signal(
            side=SignalSide.BUY,
            entry=None,
            stop_loss=last - self.sl_down,
            take_profit=last + self.tp_up,
            reason="recording-test",
        )

    def on_trade_closed(self, ctx: ClosedTradeContext) -> None:
        self.closes.append(ctx)
        # Record the bar timestamp when the close was observed.
        # The engine fires this hook after booking the close on the
        # current bar but BEFORE asking the strategy for the next
        # bar's signal — so observed_bar_at_close[i] should be <=
        # the timestamp passed to the very next on_bar call.
        self.observed_bar_at_close.append(
            pd.Timestamp(ctx.close_time)
            if ctx.close_time is not None
            else self._last_history_ts
        )


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD",
        contract_size=100.0,
        tick_size=0.01,
        tick_value=1.0,
        min_lot=0.01,
        lot_step=0.01,
    )


def _make_engine(strat: BaseStrategy) -> tuple[BacktestEngine, pd.DataFrame, float]:
    df = generate_synthetic_ohlcv(days=2, timeframe="M5", seed=11)
    inst = _inst()
    risk = RiskManager(
        starting_balance=10_000.0,
        max_leverage=100.0,
        instrument=inst,
        risk_per_trade_pct=0.5,
        daily_profit_target_pct=50.0,
        daily_max_loss_pct=50.0,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    return BacktestEngine(strategy=strat, risk=risk, broker=broker), df, 10_000.0


def test_callback_fires_once_per_close() -> None:
    strat = _RecordingStrategy()
    engine, df, _ = _make_engine(strat)
    res = engine.run(df)
    assert len(res.trades) == 1
    assert len(strat.closes) == 1
    ctx = strat.closes[0]
    assert isinstance(ctx, ClosedTradeContext)
    assert ctx.pnl == res.trades[0].pnl


def test_callback_includes_r_multiple_when_meta_is_present() -> None:
    strat = _RecordingStrategy(tp_up=4.0, sl_down=2.0)
    engine, df, _ = _make_engine(strat)
    engine.run(df)
    assert len(strat.closes) == 1
    ctx = strat.closes[0]
    # entry_risk_price is auto-attached by the engine, so r_multiple
    # must be a finite float.
    assert ctx.r_multiple is not None
    assert ctx.r_multiple == ctx.r_multiple  # not NaN


def test_callback_member_name_extracts_from_bracketed_reason() -> None:
    """A reason of the form ``[name|regime] ...`` produces member_name=name."""

    class BracketedReason(_RecordingStrategy):
        name = "bracketed_reason"

        def on_bar(self, history: pd.DataFrame) -> Signal | None:
            sig = super().on_bar(history)
            if sig is None:
                return None
            object.__setattr__(sig, "reason", "[my_member|range] details here")
            return sig

    strat = BracketedReason()
    engine, df, _ = _make_engine(strat)
    engine.run(df)
    assert len(strat.closes) == 1
    assert strat.closes[0].member_name == "my_member"


def test_callback_meta_carries_signal_meta_keys() -> None:
    """A custom Signal.meta key is delivered to the close callback."""

    class CustomMetaStrategy(_RecordingStrategy):
        name = "custom_meta"

        def on_bar(self, history: pd.DataFrame) -> Signal | None:
            sig = super().on_bar(history)
            if sig is None:
                return None
            object.__setattr__(sig, "meta", {"custom_key": "abc"})
            return sig

    strat = CustomMetaStrategy()
    engine, df, _ = _make_engine(strat)
    engine.run(df)
    assert len(strat.closes) == 1
    meta = strat.closes[0].meta or {}
    assert meta.get("custom_key") == "abc"
    # The engine auto-enriches meta with entry_risk_price.
    assert meta.get("entry_risk_price") is not None and float(meta["entry_risk_price"]) > 0
