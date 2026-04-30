#!/usr/bin/env python3
"""Pareto-style sweep: improve March without losing April on dual-pivot stack.

Starts from ``adaptive_dual_pivot_chop_moon_r9_tp9_aprilblock.yaml`` (good
April on 2026 sample) and sweeps ``pivot_trend`` knobs (TP2 RR, member
risk_multiplier, max_trades_per_day). Optionally perturbs chop
``max_trades_per_day``.

Prints cap-clean rows sorted by ``min(mar, apr)`` desc (Mar–Apr balance),
then by ``mar + apr``.

Usage::

    python3 scripts/iter38_mar_apr_pareto_sweep.py --csv data/xauusd_m1_2026.csv
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
        "--base",
        default="config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r9_tp9_aprilblock.yaml",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="Smaller grid (~75 trials): trend tp2 × rm × mtd only, chop_mtd fixed at 5",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    template = load_config(args.base)
    members = template["strategy"]["params"]["members"]
    assert members[1]["id"] == "pivot_trend"

    if args.quick:
        chop_mtd_opts = [5]
        trend_tp2 = [1.5, 1.6, 1.8, 2.0, 2.2]
        trend_rm = [0.5, 0.55, 0.6, 0.65, 0.7]
        trend_mtd = [2, 3, 4]
    else:
        chop_mtd_opts = [4, 5, 6]
        trend_tp2 = [1.5, 1.8, 2.0, 2.2, 2.5]
        trend_rm = [0.5, 0.55, 0.65, 0.75, 0.85]
        trend_mtd = [2, 3, 4]

    rows: list[dict] = []
    for cmtd in chop_mtd_opts:
        for tp2 in trend_tp2:
            for rm in trend_rm:
                for tmtd in trend_mtd:
                    cfg = copy.deepcopy(template)
                    chop = cfg["strategy"]["params"]["members"][0]["params"]
                    tr = cfg["strategy"]["params"]["members"][1]
                    chop["max_trades_per_day"] = int(cmtd)
                    tr["risk_multiplier"] = float(rm)
                    tr["params"]["tp2_rr"] = float(tp2)
                    tr["params"]["max_trades_per_day"] = int(tmtd)
                    m = _run(df, cfg)
                    if m.get("cap_violations", 0) != 0 or m.get("ruin_flag"):
                        continue
                    mon = m.get("monthly_returns") or {}
                    mar = float(mon.get("2026-03", float("nan")))
                    apr = float(mon.get("2026-04", float("nan")))
                    feb = float(mon.get("2026-02", float("nan")))
                    if not (mar == mar and apr == apr):
                        continue
                    bal = min(mar, apr)
                    rows.append(
                        {
                            "chop_max_trades": cmtd,
                            "trend_tp2_rr": tp2,
                            "trend_risk_mult": rm,
                            "trend_max_trades": tmtd,
                            "mar_pct": round(mar, 2),
                            "apr_pct": round(apr, 2),
                            "feb_pct": round(feb, 2) if feb == feb else None,
                            "min_mar_apr": round(bal, 2),
                            "sum_mar_apr": round(mar + apr, 2),
                            "full_pct": round(float(m.get("return_pct", 0)), 2),
                            "pf": round(float(m.get("profit_factor", 0)), 3),
                        }
                    )

    rows.sort(key=lambda r: (r["min_mar_apr"], r["sum_mar_apr"], r["apr_pct"]), reverse=True)
    print(
        json.dumps(
            {"cap_clean_trials": len(rows), "top_by_min_mar_apr": rows[:20]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
