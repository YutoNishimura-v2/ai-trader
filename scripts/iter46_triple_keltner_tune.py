#!/usr/bin/env python3
"""Tune adaptive_triple_keltner_split_regimes (Iter45 stack): Mar/Apr vs worst_score.

Phase 1: full-sample backtest only (fast).
Phase 2: rolling harness only on top-K trials by min(Mar%, Apr%) with cap=0.

Example::

    python3 scripts/iter46_triple_keltner_tune.py --csv data/xauusd_m1_2026.csv --quick
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
        default="config/research_aspiration_200/adaptive_triple_keltner_split_regimes_r8.yaml",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="Smaller grid (~24 trials)",
    )
    ap.add_argument(
        "--harness-top",
        type=int,
        default=10,
        metavar="K",
        help="Run rolling harness on top K trials by min(Mar,Apr) after screening",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    base = load_config(args.base)

    if args.quick:
        kelt_rms = [0.35, 0.40, 0.45, 0.50]
        ram_list = [19.0, 20.0, 21.0]
        tam_list = [24.0, 25.0]
    else:
        kelt_rms = [0.32, 0.36, 0.40, 0.44, 0.48]
        ram_list = [18.0, 19.0, 20.0, 21.0, 22.0]
        tam_list = [23.0, 24.0, 25.0, 26.0, 27.0]

    screened: list[tuple[dict, dict]] = []
    for kr in kelt_rms:
        for ram in ram_list:
            for tam in tam_list:
                if tam <= ram:
                    continue
                c = copy.deepcopy(base)
                sp = c["strategy"]["params"]
                sp["range_adx_max"] = float(ram)
                sp["trend_adx_min"] = float(tam)
                sp["members"][0]["risk_multiplier"] = float(kr)

                m = _metrics(df, c)
                if int(m.get("cap_violations", 0) or 0) != 0 or m.get("ruin_flag"):
                    continue
                mon = m.get("monthly_returns") or {}
                mar = float(mon.get("2026-03", 0))
                apr = float(mon.get("2026-04", 0))
                row = {
                    "kelt_rm": kr,
                    "range_adx_max": ram,
                    "trend_adx_min": tam,
                    "mar_pct": round(mar, 2),
                    "apr_pct": round(apr, 2),
                    "min_mar_apr": round(min(mar, apr), 2),
                    "full_pct": round(float(m.get("return_pct", 0)), 2),
                    "windows_passing": None,
                    "worst_score": None,
                }
                screened.append((row, c))

    screened.sort(key=lambda t: -t[0]["min_mar_apr"])
    top_entries = screened[: max(0, args.harness_top)] if args.harness_top else []
    top_ids = {id(r) for r, _ in top_entries}

    for row, cfg in screened:
        if id(row) not in top_ids:
            continue
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=windows,
            label="iter46-keltner-tune",
            i_know_this_is_tournament_evaluation=True,
        )
        sc = score_config(ev)
        ws = sc["worst_score"]
        row["windows_passing"] = ev.windows_passing
        row["worst_score"] = None if ws == float("-inf") else float(ws)

    ok = [r for r, _ in screened]
    ok.sort(
        key=lambda r: (
            -r["min_mar_apr"],
            r["worst_score"] if r["worst_score"] is not None else 999.0,
        ),
    )

    print(
        json.dumps(
            {
                "n_cap_clean_screened": len(screened),
                "harness_top": args.harness_top,
                "top_by_min_mar_apr_then_lowest_worst_score": ok[:15],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
