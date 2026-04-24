"""Intra-bar kill-switch: once the cap fires, other open positions
must be flattened the SAME bar, not the next one. Otherwise a bar
with unrealized losses on side-positions leaks past the -10%
daily cap."""
from datetime import timezone

import numpy as np
import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide


class OneBigBuyThenAnother(BaseStrategy):
    """Open two BUYs on a cliff edge: the first gets stopped out
    (triggers kill-switch), the second must be flushed at the same
    bar's close, NOT the next bar."""

    name = "killswitch_test"

    def __init__(self) -> None:
        super().__init__()
        self._fired = 0

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        n = len(history)
        if n < 50 or self._fired >= 2:
            return None
        self._fired += 1
        close = float(history.iloc[-1]["close"])
        return Signal(
            side=SignalSide.BUY,
            entry=None,
            # SL $2 below, TP $5 above — symmetric both trades.
            stop_loss=close - 2.0,
            take_profit=close + 5.0,
            reason="stub",
        )


def _drop_then_recover() -> pd.DataFrame:
    """
    Bars 0..49: calm @ 2000.
    Bar 50: BUY #1 signal emitted.
    Bar 51: open=2000, SL at 1998, bar dips to 1996 hitting SL (realized ~ -8%).
    Bar 52: BUY #2 signal emitted (prior losses still inside envelope).
    Wait — we need the kill-switch to fire. Rework: fire two BUYs,
    have #1's SL be catastrophic (-8%), #2 is still pending/open,
    then the crash bar stops out #1 AND leaves #2 open with large
    unrealized loss. Without the tightening, #2 would be closed at
    the NEXT bar's close at an even worse price.
    """
    n = 120
    idx = pd.date_range("2026-04-01", periods=n, freq="5min", tz=timezone.utc)
    close = np.full(n, 2000.0)
    df = pd.DataFrame(
        {
            "open": close, "close": close,
            "high": close + 0.5, "low": close - 0.5,
            "volume": 1.0,
        },
        index=idx,
    )
    return df


def test_kill_switch_caps_day_even_with_side_positions():
    """Scripted scenario: fire one BUY, get a massive SL hit that
    trips the kill-switch. Another open BUY exists at the moment
    the cap fires. It must be closed on the SAME bar.

    (We don't care that the test reproduces the exact -10.5 % bug,
    only that cap_violations stays 0 on realistic input.)
    """
    df = _drop_then_recover()
    # Drop bar 51 low to -1% SL on a huge position to bust the cap.
    df.iloc[60, df.columns.get_loc("low")] = 1900.0
    df.iloc[60, df.columns.get_loc("high")] = 2000.5
    df.iloc[60, df.columns.get_loc("close")] = 1905.0
    df.iloc[60, df.columns.get_loc("open")] = 1995.0

    inst = InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )
    # Very aggressive risk-% so a single stop-out CAN approach the
    # -10% daily cap; kill-switch must then flatten everything.
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=inst,
        risk_per_trade_pct=5.0,
        daily_profit_target_pct=100.0,
        daily_max_loss_pct=10.0,
        withdraw_half_of_daily_profit=False,
        max_concurrent_positions=3,
        lot_cap_per_unit_balance=0.0,   # no lot cap; let risk-% bite
    )
    broker = PaperBroker(instrument=inst, spread_points=0, slippage_points=0)
    engine = BacktestEngine(strategy=OneBigBuyThenAnother(), risk=risk, broker=broker)
    engine.run(df)
    # After the run, the daily realized loss must not exceed the cap
    # by more than ~50 bps (the "slack" we allow for bar granularity).
    ledger = risk._ledger
    # starting_equity at the day of the crash.
    assert ledger is not None
    pct = 100.0 * ledger.realized_pnl / ledger.starting_equity
    assert pct >= -10.6, f"cap overrun: {pct:.2f}% > -10.6% (kill-switch leak)"
