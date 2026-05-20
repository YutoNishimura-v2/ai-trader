#!/usr/bin/env python3
"""Mar/Apr balance sweep on Iter40 ``rollwin`` dual-pivot (8% risk).

Perturbs chop ``block_hours_utc`` and trend ``tp2_rr`` while keeping cap=0.
Sorts rows by ``min(Mar%, Apr%)`` descending, then ``worst_score`` from the
rolling harness.

Example::

    python3 scripts/iter42_rollwin_mar_apr_sweep.py --csv data/xauusd_m1_2026.csv
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
        default="config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml",
    )
    ap.add_argument(
        "--harness-top",
        type=int,
        default=8,
        metavar="N",
        help="After screening, run rolling harness only on top N by min(Mar,Apr) (0=skip)",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="Smaller grid (9 trials): block hours × tp2_rr coarse screen",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    base = load_config(args.base)

    if args.quick:
        blocks = [[13, 14], [12, 13, 14], [13, 14, 15]]
        tp2_list = [1.5, 1.6, 1.7]
    else:
        blocks = [
            [13, 14],
            [12, 13, 14],
            [13, 14, 15],
            [14, 15],
            [12, 13],
        ]
        tp2_list = [1.4, 1.5, 1.6, 1.7, 1.8]

    rows: list[dict] = []
    trial_cfgs: list[tuple[dict, dict]] = []
    for bh in blocks:
        for tp2 in tp2_list:
            c = copy.deepcopy(base)
            members = c["strategy"]["params"]["members"]
            members[0]["params"]["block_hours_utc"] = list(bh)
            members[1]["params"]["tp2_rr"] = float(tp2)
            m = _metrics(df, c)
            if m.get("cap_violations", 0) != 0:
                continue
            mon = m.get("monthly_returns") or {}
            mar = float(mon.get("2026-03", 0))
            apr = float(mon.get("2026-04", 0))
            row = {
                "block_hours_utc": bh,
                "trend_tp2_rr": tp2,
                "full_pct": round(float(m.get("return_pct", 0)), 2),
                "mar_pct": round(mar, 2),
                "apr_pct": round(apr, 2),
                "min_mar_apr": round(min(mar, apr), 2),
                "windows_passing": None,
                "worst_score": None,
            }
            rows.append(row)
            trial_cfgs.append((row, c))

    rows.sort(key=lambda r: (r["min_mar_apr"], r["full_pct"]), reverse=True)

    top = rows[: max(0, args.harness_top)] if args.harness_top else []
    top_set = set(id(r) for r in top)
    for row, cfg in trial_cfgs:
        if id(row) not in top_set:
            continue
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=windows,
            label="iter42-rollwin-sweep",
            i_know_this_is_tournament_evaluation=True,
        )
        sc = score_config(ev)
        ws = sc["worst_score"]
        row["windows_passing"] = ev.windows_passing
        row["worst_score"] = None if ws == float("-inf") else float(ws)

    rows.sort(
        key=lambda r: (
            r["min_mar_apr"],
            r["worst_score"] if r["worst_score"] is not None else -1e18,
        ),
        reverse=True,
    )
    print(json.dumps({"n_cap_clean": len(rows), "sorted": rows[:25]}, indent=2))


if __name__ == "__main__":
    main()
