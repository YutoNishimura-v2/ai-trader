#!/usr/bin/env python3
"""Smoke / validation backtest for ``zigzag_fib_mtf`` strategy.

Usage::

    python3 scripts/validate_zigzag_fib_mtf.py [--csv PATH]

Without ``--csv``, runs on deterministic synthetic M1 (15 days).
With ``--csv``, loads OHLCV (time, open, high, low, close, volume).

Example::

    python3 scripts/validate_zigzag_fib_mtf.py --csv data/xauusd_m1_2026.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.broker.paper import PaperBroker
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", help="M1 OHLCV CSV (optional)")
    ap.add_argument("--days", type=int, default=15, help="Synthetic days if no CSV")
    ap.add_argument("--risk-pct", type=float, default=1.0, dest="risk_pct")
    args = ap.parse_args()

    if args.csv:
        df = load_ohlcv_csv(args.csv)
    else:
        from datetime import datetime, timezone

        df = generate_synthetic_ohlcv(
            days=args.days,
            timeframe="M1",
            seed=202,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    inst = InstrumentSpec(
        symbol="XAUUSD",
        contract_size=100.0,
        tick_size=0.01,
        tick_value=1.0,
        quote_currency="USD",
        min_lot=0.01,
        lot_step=0.01,
    )
    strat = get_strategy(
        "zigzag_fib_mtf",
        zigzag_threshold_atr=1.2,
        fib_touch_atr=0.45,
        cooldown_bars=5,
        session="always",
    )
    risk = RiskManager(
        starting_balance=100_000.0,
        max_leverage=100.0,
        instrument=inst,
        risk_per_trade_pct=args.risk_pct,
        withdraw_half_of_daily_profit=False,
    )
    broker = PaperBroker(instrument=inst, spread_points=2, slippage_points=0)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    m = compute_metrics(res, starting_balance=100_000.0)
    print(f"bars={len(df)} trades={len(res.trades)}")
    print(f"return_pct={m.get('return_pct', 0):.2f} PF={m.get('profit_factor', 0):.3f}")
    if res.trades:
        t0 = res.trades[0]
        print(f"first_trade side={t0.side} pnl={getattr(t0, 'pnl', 'n/a')}")


if __name__ == "__main__":
    main()
