#!/usr/bin/env python3
"""Sweep range_adx_max at fixed trend_adx_min=24 on kr046_km20 ADX stack.

Requires range_adx_max < 24 so transition band exists.

Example::

    python3 scripts/iter53_ram_fixed_tam24_sweep.py --csv data/xauusd_m1_2026.csv
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
        default="config/research_aspiration_200/adaptive_triple_keltner_overlap_adx1924_kr046_km20.yaml",
    )
    ap.add_argument("--trend-adx-min", type=float, default=24.0)
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    tpl = load_config(args.base)

    tam = float(args.trend_adx_min)
    rows: list[dict] = []

    for ram in [17.0, 18.0, 19.0, 20.0, 21.0, 22.0]:
        if ram >= tam:
            continue
        c = copy.deepcopy(tpl)
        c["strategy"]["params"]["range_adx_max"] = float(ram)
        c["strategy"]["params"]["trend_adx_min"] = tam

        m = _metrics(df, c)
        if int(m.get("cap_violations", 0) or 0) != 0 or m.get("ruin_flag"):
            rows.append({"range_adx_max": ram, "skipped": True})
            continue

        ev = evaluate_config(
            c,
            full_df=df,
            windows=windows,
            label="iter53-ram",
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
                "range_adx_max": ram,
                "trend_adx_min": tam,
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
    ok.sort(
        key=lambda r: (
            r["worst_score"] if r.get("worst_score") is not None else 999.0,
            -r["min_mar_apr"],
        ),
    )

    print(json.dumps({"base": args.base, "trend_adx_min": tam, "trials": rows, "sorted": ok}, indent=2))


if __name__ == "__main__":
    main()
