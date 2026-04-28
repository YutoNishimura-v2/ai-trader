#!/usr/bin/env python3
"""Sweep Keltner risk_multiplier on Iter50 overlap+ADX19/24 stack (cap-clean only).

Runs full harness per trial — keep grid small (~12 steps).

Example::

    python3 scripts/iter51_overlap_adx1924_rm_sweep.py --csv data/xauusd_m1_2026.csv
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.broker.paper import PaperBroker
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
        default="config/research_aspiration_200/adaptive_triple_keltner_overlap_adx1924_kr038_km19.yaml",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    tpl = load_config(args.base)

    krs = [round(0.30 + 0.02 * i, 2) for i in range(11)]  # 0.30 .. 0.50
    rows: list[dict] = []

    for kr in krs:
        c = copy.deepcopy(tpl)
        c["strategy"]["params"]["members"][0]["risk_multiplier"] = float(kr)
        m = _metrics(df, c)
        if int(m.get("cap_violations", 0) or 0) != 0 or m.get("ruin_flag"):
            rows.append({"kelt_rm": kr, "skipped": True, "reason": "cap_or_ruin"})
            continue
        ev = evaluate_config(
            c,
            full_df=df,
            windows=windows,
            label="iter51-adx1924-rm",
            i_know_this_is_tournament_evaluation=True,
        )
        fm = ev.full_metrics
        sc = score_config(ev)
        ws = sc["worst_score"]
        mon = fm.get("monthly_returns") or {}
        mar = float(mon.get("2026-03", 0))
        apr = float(mon.get("2026-04", 0))
        rows.append(
            {
                "kelt_rm": kr,
                "mar_pct": round(mar, 2),
                "apr_pct": round(apr, 2),
                "min_mar_apr": round(min(mar, apr), 2),
                "full_pct": round(float(fm.get("return_pct", 0)), 2),
                "windows_passing": ev.windows_passing,
                "worst_score": None if ws == float("-inf") else float(ws),
                "profit_factor": round(float(fm.get("profit_factor", 0)), 3),
            }
        )

    ok = [r for r in rows if not r.get("skipped")]
    ok.sort(key=lambda r: (r["worst_score"] if r["worst_score"] is not None else 999.0, -r["min_mar_apr"]))

    print(json.dumps({"base": args.base, "trials": rows, "sorted_by_worst_score_then_min_mar_apr": ok}, indent=2))


if __name__ == "__main__":
    main()
