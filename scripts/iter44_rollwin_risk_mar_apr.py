#!/usr/bin/env python3
"""Mar/Apr-first sweep: risk_per_trade_pct on adaptive dual-pivot rollwin template.

Scores each trial on **min(March%, April%)**, **worst_score**, and harness
window passes (same CSV + rolling battery as iter32).

Example::

    python3 scripts/iter44_rollwin_risk_mar_apr.py --csv data/xauusd_m1_2026.csv
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
from ai_trader.research.stability import build_rolling_windows, evaluate_config, score_config


def _metrics(df, cfg: dict) -> dict:
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
        default="config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r9_tp9_balanced.yaml",
        help="Template with dual-pivot strategy (risk block overridden per trial)",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    tpl = load_config(args.base)

    rp_grid = [7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0]
    rows: list[dict] = []

    for rp in rp_grid:
        c = copy.deepcopy(tpl)
        rc = c.setdefault("risk", {})
        rc["risk_per_trade_pct"] = float(rp)
        rc["min_risk_per_trade_pct"] = max(1.5, round(rp * 0.25, 2))
        rc["max_risk_per_trade_pct"] = min(12.0, round(rp + 2.0, 2))
        m = _metrics(df, c)
        caps = int(m.get("cap_violations", 0) or 0)
        if caps != 0 or m.get("ruin_flag"):
            rows.append(
                {
                    "risk_per_trade_pct": rp,
                    "skipped": True,
                    "reason": "cap_or_ruin",
                    "cap_violations": caps,
                }
            )
            continue
        mon = m.get("monthly_returns") or {}
        mar = float(mon.get("2026-03", 0))
        apr = float(mon.get("2026-04", 0))
        ev = evaluate_config(
            c,
            full_df=df,
            windows=windows,
            label="iter44-risk-marapr",
            i_know_this_is_tournament_evaluation=True,
        )
        sc = score_config(ev)
        ws = sc["worst_score"]
        ws_out = None if ws == float("-inf") else float(ws)
        rows.append(
            {
                "risk_per_trade_pct": rp,
                "skipped": False,
                "full_pct": round(float(m.get("return_pct", 0)), 2),
                "mar_pct": round(mar, 2),
                "apr_pct": round(apr, 2),
                "min_mar_apr": round(min(mar, apr), 2),
                "sum_mar_apr": round(mar + apr, 2),
                "windows_passing": ev.windows_passing,
                "worst_score": ws_out,
                "profit_factor": round(float(m.get("profit_factor", 0)), 3),
            }
        )

    rows_ok = [r for r in rows if not r.get("skipped")]
    rows_ok.sort(
        key=lambda r: (r["min_mar_apr"], r["worst_score"] if r["worst_score"] is not None else -1e18),
        reverse=True,
    )

    print(
        json.dumps(
            {
                "base_template": args.base,
                "sorted_by_min_mar_apr_then_worst_score": rows_ok,
                "all_trials": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
