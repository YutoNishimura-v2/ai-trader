"""The DD metric must be invariant to the half-profit sweep.

A user-initiated withdrawal / sweep is NOT a loss. The equity curve
used to compute max_drawdown must therefore include the ledger
balance + open unrealized + withdrawn_total, not just the trading
balance.
"""
from datetime import timezone

import numpy as np
import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.broker.paper import PaperBroker
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide


class SmallWinsEveryFewBars(BaseStrategy):
    """Deterministic test helper: fire a BUY every N bars with a
    small TP and tight SL, so the backtest produces a steadily
    accumulating series of winning trades. No drawdown should be
    visible in the total-equity curve."""

    name = "small_wins_test_only"

    def __init__(self, every: int = 20, warmup: int = 5) -> None:
        super().__init__()
        self.every = every
        self.warmup = warmup

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        n = len(history)
        if n < self.warmup or n % self.every != 0:
            return None
        close = float(history.iloc[-1]["close"])
        return Signal(
            side=SignalSide.BUY,
            entry=None,
            stop_loss=close - 5.0,
            take_profit=close + 1.0,
            reason="test-small-win",
        )


def _climbing_ohlcv() -> pd.DataFrame:
    """A monotonically rising price series — guarantees every BUY TP
    fills. Daily close moves up enough to trigger the half-profit
    sweep if the strategy is winning."""
    n = 60 * 24 * 10  # 10 days of M1
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    # Drift up 0.1 per minute so a 1-unit TP is reliably within one
    # bar's range. High/low widened so SLs and TPs both fit.
    close = 2000.0 + np.arange(n) * 0.1
    df = pd.DataFrame(
        {
            "open": close,
            "close": close + 0.05,
            "high": close + 2.0,
            "low": close - 2.0,
            "volume": 1.0,
        },
        index=idx,
    )
    return df


def test_max_drawdown_does_not_charge_withdrawal_sweep():
    df = _climbing_ohlcv()
    inst = InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        min_lot=0.01, lot_step=0.01,
    )
    risk = RiskManager(
        starting_balance=10_000.0,
        max_leverage=100.0,
        instrument=inst,
        daily_profit_target_pct=1000.0,   # disable target kill
        daily_max_loss_pct=1000.0,        # disable loss kill
        withdraw_half_of_daily_profit=True,
        max_concurrent_positions=1,
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=SmallWinsEveryFewBars(), risk=risk, broker=broker)
    result = engine.run(df)
    assert result.withdrawn_total > 0, "test setup: withdrawal sweep should have fired"
    m = compute_metrics(result, starting_balance=10_000.0)
    # The strategy only wins; max DD must be ~0, not a large negative
    # figure driven by the sweep.
    assert m["max_drawdown_pct"] > -1.0, (
        f"sweep leaking into DD: {m['max_drawdown_pct']}% (withdrawn={result.withdrawn_total})"
    )
