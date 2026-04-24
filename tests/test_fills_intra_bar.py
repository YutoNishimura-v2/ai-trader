"""Assert intra-bar hard TP/SL fills.

User concern 2026-04-24: 'you don't need to wait for the bar to
close for either entry or exit.'

Correct behaviour we want:
- Entry: fills at the NEXT bar's open after the signal bar closes.
  This is standard no-lookahead discipline — the strategy decides
  after bar N, and market orders fill at bar N+1 open.
- Exit: hard SL/TP fills INTRA-BAR at the SL/TP price, not at the
  bar's close. A bar whose low pierces SL closes the position at
  SL, regardless of where the bar closes.

These tests lock both invariants.
"""
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.broker.paper import PaperBroker
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.base import BaseStrategy, Signal, SignalSide


class FireOnceAt(BaseStrategy):
    name = "fire_once_at_bar_test"

    def __init__(self, at: int, sl_dist: float, tp_dist: float) -> None:
        super().__init__()
        self.at = at
        self.sl_dist = sl_dist
        self.tp_dist = tp_dist
        self._fired = False
        self.min_history = 0  # no warmup needed for this test strategy

    def on_bar(self, history: pd.DataFrame) -> Signal | None:
        if self._fired or len(history) <= self.at:
            return None
        if len(history) == self.at + 1:
            self._fired = True
            close = float(history.iloc[-1]["close"])
            return Signal(
                side=SignalSide.BUY, entry=None,
                stop_loss=close - self.sl_dist,
                take_profit=close + self.tp_dist,
                reason="stub",
            )
        return None


def _inst() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def _df(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-04-01", periods=len(bars), freq="1min", tz=timezone.utc)
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    return df


def test_tp_fills_intra_bar_not_at_close():
    """Signal at bar 49, fills at bar 50 open. Bar 51: high pierces
    TP at 2005 but closes lower at 2003. Exit price MUST be 2005,
    not 2003."""
    # Bars 0..48: flat 2000; bar 49 closes 2000 (signal); bar 50
    # opens at 2000 (fill); bar 51 spikes to 2006 then closes 2003.
    bars = [(2000.0, 2000.1, 1999.9, 2000.0)] * 50
    bars.append((2000.0, 2000.1, 1999.9, 2000.0))  # bar 50: fill
    bars.append((2000.5, 2006.0, 2000.3, 2003.0))  # bar 51: TP pierce
    df = _df(bars)

    strat = FireOnceAt(at=49, sl_dist=5.0, tp_dist=5.0)
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=_inst(),
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=_inst(), spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.reason == "tp"
    # Exit MUST be at TP level (2005.0), not bar close (2003.0).
    assert t.exit == pytest.approx(2005.0), f"TP fill price wrong: {t.exit}"


def test_sl_fills_intra_bar_not_at_close():
    """Bar pierces SL but recovers to close above it. Exit MUST be
    at SL price, not bar close."""
    bars = [(2000.0, 2000.1, 1999.9, 2000.0)] * 50
    bars.append((2000.0, 2000.1, 1999.9, 2000.0))   # bar 50: fill
    bars.append((2000.5, 2001.0, 1993.0, 2000.8))   # bar 51: SL pierce then recovery
    df = _df(bars)

    strat = FireOnceAt(at=49, sl_dist=5.0, tp_dist=5.0)
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=_inst(),
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=_inst(), spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.reason == "sl"
    # Exit MUST be at SL level (1995.0), not bar close (2000.8).
    assert t.exit == pytest.approx(1995.0), f"SL fill price wrong: {t.exit}"


def test_entry_fills_at_next_bar_open():
    """Signal emitted after bar N closes; the order fills at bar
    N+1's open price. This is the no-lookahead discipline.

    Gap-open construction: bar 49 closes flat at 2000, strategy
    fires there. Bar 50 gaps up to 2010 at open. Our entry price
    should be ~2010 (bar 50 open), never 2000 (bar 49 close).
    SL/TP are chosen so they don't fire on bar 50 or 51.
    """
    bars = [(2000.0, 2000.1, 1999.9, 2000.0)] * 50
    bars.append((2010.0, 2010.5, 2009.5, 2010.2))   # bar 50: gap-open
    bars.append((2010.2, 2011.0, 2009.0, 2010.0))   # bar 51: stays in range
    df = _df(bars)

    # SL well below the fill price to avoid triggering; TP well above.
    strat = FireOnceAt(at=49, sl_dist=5.0, tp_dist=50.0)
    risk = RiskManager(
        starting_balance=10_000.0, max_leverage=100.0, instrument=_inst(),
        risk_per_trade_pct=2.0,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=_inst(), spread_points=0, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    assert res.trades, "expected one trade"
    t = res.trades[0]
    # The trade closed at EOD (reason="eod") at the last bar's close.
    # The entry price must be bar 50's open (2010.0), not bar 49's
    # close (2000.0).
    assert t.entry == pytest.approx(2010.0), (
        f"entry should fill at bar 50 open=2010.0, got {t.entry}"
    )
