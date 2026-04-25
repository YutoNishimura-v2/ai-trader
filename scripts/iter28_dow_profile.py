"""Profile per-day-of-week and per-hour PnL for a config.

Usage: python3 scripts/iter28_dow_profile.py --config <yaml> --csv <csv>
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.sweep import risk_kwargs_from_config
from ai_trader.broker.paper import PaperBroker
from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build(cfg):
    inst = cfg["instrument"]
    instrument = InstrumentSpec(
        symbol=inst["symbol"], contract_size=float(inst["contract_size"]),
        tick_size=float(inst["tick_size"]), tick_value=float(inst["tick_value"]),
        quote_currency=inst.get("quote_currency", "USD"),
        min_lot=float(inst.get("min_lot", 0.01)),
        lot_step=float(inst.get("lot_step", 0.01)),
        is_24_7=bool(inst.get("is_24_7", False)),
    )
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx, **risk_kwargs_from_config(cfg["risk"]),
    )
    ex = cfg["execution"]
    broker = PaperBroker(instrument=instrument, spread_points=int(ex["spread_points"]),
                        slippage_points=int(ex["slippage_points"]),
                        commission_per_lot=float(ex.get("commission_per_lot", 0.0)))
    strat = get_strategy(cfg["strategy"]["name"], **cfg["strategy"].get("params", {}))
    return strat, risk, broker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--window", default="full",
                    choices=["full", "research", "validation", "tournament"])
    args = ap.parse_args()
    cfg = load_config(args.config)
    df = load_ohlcv_csv(args.csv)
    if args.window != "full":
        from ai_trader.backtest.splitter import load_recent_only_held_out
        split = load_recent_only_held_out(df, validation_days=14, tournament_days=14)
        df = getattr(split, args.window)
    strat, risk, broker = build(cfg)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)

    dow_pnl = defaultdict(float); dow_n = defaultdict(int); dow_w = defaultdict(int); dow_l = defaultdict(int)
    hr_pnl = defaultdict(float);  hr_n  = defaultdict(int)
    for t in res.trades:
        d = t.open_time.weekday()
        h = t.open_time.hour
        dow_pnl[d] += t.pnl; dow_n[d] += 1
        if t.pnl > 0: dow_w[d] += 1
        elif t.pnl < 0: dow_l[d] += 1
        hr_pnl[h]  += t.pnl; hr_n[h]  += 1

    print(f"# {Path(args.config).stem}  ({len(res.trades)} trades, ¥{res.final_balance - 100_000:+,.0f})")
    print(f"\n## Per Day of Week")
    print(f"{'dow':<5} {'n':>5} {'wins':>5} {'losses':>7} {'pnl¥':>12} {'win%':>6}")
    for d in range(7):
        if dow_n[d] == 0:
            continue
        wp = 100*dow_w[d]/dow_n[d] if dow_n[d] else 0
        print(f"{DOW[d]:<5} {dow_n[d]:>5} {dow_w[d]:>5} {dow_l[d]:>7} {dow_pnl[d]:>12,.0f} {wp:>6.1f}")
    print(f"\n## Per Hour (UTC)")
    print(f"{'hr':>3} {'n':>5} {'pnl¥':>12} {'avg¥':>10}")
    for h in sorted(hr_n.keys()):
        avg = hr_pnl[h]/hr_n[h]
        print(f"{h:>3} {hr_n[h]:>5} {hr_pnl[h]:>12,.0f} {avg:>10,.0f}")


if __name__ == "__main__":
    main()
