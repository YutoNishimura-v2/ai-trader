#!/usr/bin/env python3
"""Search for (risk, tp2) with best calendar month >= 200% and cap_violations==0.

Extends the iter35 grid with higher risk% and tp2 — the +200% aspiration
only shows up at aggressive sizes on this 2026 M1 sample.

Example::

    python3 scripts/iter43_moonshot_200_sweep.py --csv data/xauusd_m1_2026.csv
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.broker.paper import PaperBroker
from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.backtest.sweep import risk_kwargs_from_config
from ai_trader.strategy.registry import get_strategy


def _run(df, cfg: dict) -> dict:
    i = cfg["instrument"]
    inst = InstrumentSpec(
        symbol=i["symbol"],
        contract_size=float(i["contract_size"]),
        tick_size=float(i["tick_size"]),
        tick_value=float(i["tick_value"]),
        quote_currency=i.get("quote_currency", "USD"),
        min_lot=float(i.get("min_lot", 0.01)),
        lot_step=float(i.get("lot_step", 0.01)),
    )
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=inst,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        **risk_kwargs_from_config(cfg["risk"]),
    )
    ex = cfg["execution"]
    broker = PaperBroker(
        instrument=inst,
        spread_points=int(ex["spread_points"]),
        slippage_points=int(ex["slippage_points"]),
    )
    sb = float(cfg["account"]["starting_balance"])
    strat = get_strategy(cfg["strategy"]["name"], **(cfg["strategy"].get("params") or {}))
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    return compute_metrics(res, starting_balance=sb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument(
        "--template",
        default="config/research_aspiration_200/moonshot_pivot_daily_r18_tp9.yaml",
    )
    ap.add_argument(
        "--target",
        type=float,
        default=200.0,
        help="Minimum best-month %% to record as hit",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    base = load_config(args.template)

    rp_grid = [
        6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0,
        16.0, 17.0, 18.0,
    ]
    tp2_grid = [8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0]

    hits_cap_clean: list[dict] = []
    hits_any: list[dict] = []

    for rp in rp_grid:
        for tp2 in tp2_grid:
            c = copy.deepcopy(base)
            c["risk"]["risk_per_trade_pct"] = float(rp)
            p = c["strategy"]["params"]
            p["tp2_rr"] = float(tp2)
            for k in ("htf", "adx_period", "adx_max"):
                p.pop(k, None)
            m = _run(df, c)
            mon = m.get("monthly_returns") or {}
            best = float(max(mon.values())) if mon else 0.0
            caps = int(m.get("cap_violations", 0) or 0)
            row = {
                "risk_per_trade_pct": rp,
                "tp2_rr": tp2,
                "best_month_pct": round(best, 2),
                "return_pct": round(float(m.get("return_pct", 0)), 2),
                "cap_violations": caps,
                "profit_factor": round(float(m.get("profit_factor", 0)), 3),
            }
            if best >= args.target:
                hits_any.append(row)
                if caps == 0 and not m.get("ruin_flag"):
                    hits_cap_clean.append(row)

    hits_any.sort(key=lambda r: (-r["best_month_pct"], r["cap_violations"]))
    hits_cap_clean.sort(key=lambda r: -r["best_month_pct"])

    print(
        json.dumps(
            {
                "target_best_month_pct": args.target,
                "n_month_ge_target": len(hits_any),
                "n_cap_clean_month_ge_target": len(hits_cap_clean),
                "top_any": hits_any[:12],
                "top_cap_clean": hits_cap_clean[:12],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
