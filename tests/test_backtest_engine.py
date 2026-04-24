from datetime import datetime, timezone

import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide
from ai_trader.strategy.registry import get_strategy


class AlwaysBuy(BaseStrategy):
    """Test helper: fires a BUY on the very first bar after a warmup."""

    name = "always_buy_test_only"

    def __init__(self, warmup: int = 5, tp_up: float = 2.0, sl_down: float = 2.0):
        super().__init__()
        self.warmup = warmup
        self.tp_up = tp_up
        self.sl_down = sl_down
        self._fired = False

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        if self._fired or len(history) < self.warmup:
            return None
        self._fired = True
        last = history.iloc[-1]["close"]
        return Signal(
            side=SignalSide.BUY,
            entry=None,
            stop_loss=float(last - self.sl_down),
            take_profit=float(last + self.tp_up),
            reason="test-only",
        )


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        min_lot=0.01, lot_step=0.01,
    )


def test_engine_books_a_single_trade_end_to_end():
    df = generate_synthetic_ohlcv(days=2, timeframe="M5", seed=1)
    inst = _instrument()
    risk = RiskManager(starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
                       risk_per_trade_pct=0.5, daily_profit_target_pct=50.0,
                       daily_max_loss_pct=50.0, withdraw_half_of_daily_profit=False)
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    strat = AlwaysBuy(warmup=5, tp_up=2.0, sl_down=2.0)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)

    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.side == "buy"
    assert t.reason in {"sl", "tp", "eod"}


def test_metrics_reasonable_shape():
    df = generate_synthetic_ohlcv(days=30, timeframe="M5", seed=2)
    inst = _instrument()
    risk = RiskManager(starting_balance=10_000.0, max_leverage=100.0, instrument=inst)
    broker = PaperBroker(instrument=inst, spread_points=20, slippage_points=2)
    strat = get_strategy("trend_pullback_fib")
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    res = engine.run(df)
    m = compute_metrics(res, starting_balance=10_000.0)

    for key in ("trades", "win_rate", "profit_factor", "max_drawdown_pct",
                "sharpe_daily", "net_profit", "expectancy"):
        assert key in m
    assert m["trades"] >= 0
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["max_drawdown_pct"] <= 0.0 + 1e-9  # drawdown is <= 0 (reported as negative pct)
