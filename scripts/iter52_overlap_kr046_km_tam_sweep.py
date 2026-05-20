#!/usr/bin/env python3
"""Sweep kelt_mult and/or trend_adx_min on Iter51 overlap+ADX19 stack (kr046).

Base: adaptive_triple_keltner_overlap_adx1924_kr046_km19.yaml

Example::

    python3 scripts/iter52_overlap_kr046_km_tam_sweep.py --csv data/xauusd_m1_2026.csv --mode km
    python3 scripts/iter52_overlap_kr046_km_tam_sweep.py --csv data/xauusd_m1_2026.csv --mode tam
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


def _one(df, windows, cfg: dict, label: str) -> dict | None:
    m = _metrics(df, cfg)
    if int(m.get("cap_violations", 0) or 0) != 0 or m.get("ruin_flag"):
        return None
    ev = evaluate_config(
        cfg,
        full_df=df,
        windows=windows,
        label=label,
        i_know_this_is_tournament_evaluation=True,
    )
    fm = ev.full_metrics
    sc = score_config(ev)
    ws = sc["worst_score"]
    mon = fm.get("monthly_returns") or {}
    mar = float(mon.get("2026-03", 0))
    apr = float(mon.get("2026-04", 0))
    return {
        "mar_pct": round(mar, 2),
        "apr_pct": round(apr, 2),
        "min_mar_apr": round(min(mar, apr), 2),
        "full_pct": round(float(fm.get("return_pct", 0)), 2),
        "windows_passing": ev.windows_passing,
        "worst_score": None if ws == float("-inf") else float(ws),
        "profit_factor": round(float(fm.get("profit_factor", 0)), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/adaptive_triple_keltner_overlap_adx1924_kr046_km19.yaml",
    )
    ap.add_argument("--mode", choices=["km", "tam", "both"], default="both")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    tpl = load_config(args.base)

    results: list[dict] = []

    def run_km():
        for km in [1.75, 1.8, 1.85, 1.9, 1.95, 2.0]:
            c = copy.deepcopy(tpl)
            c["strategy"]["params"]["members"][0]["params"]["kelt_mult"] = float(km)
            out = _one(df, windows, c, "iter52-km")
            row = {"sweep": "kelt_mult", "kelt_mult": km, "result": out}
            results.append(row)

    def run_tam():
        ram = float(tpl["strategy"]["params"]["range_adx_max"])
        for tam in [23.0, 24.0, 25.0]:
            if tam <= ram:
                continue
            c = copy.deepcopy(tpl)
            c["strategy"]["params"]["trend_adx_min"] = float(tam)
            out = _one(df, windows, c, "iter52-tam")
            row = {"sweep": "trend_adx_min", "trend_adx_min": tam, "result": out}
            results.append(row)

    if args.mode in ("km", "both"):
        run_km()
    if args.mode in ("tam", "both"):
        run_tam()

    ok = []
    for r in results:
        if r.get("result") is not None:
            x = dict(r)
            x.update(r["result"])
            del x["result"]
            ok.append(x)

    ok.sort(
        key=lambda r: (
            r["worst_score"] if r["worst_score"] is not None else 999.0,
            -r["min_mar_apr"],
        ),
    )

    print(json.dumps({"base": args.base, "mode": args.mode, "trials": results, "sorted_cap_clean": ok}, indent=2))


if __name__ == "__main__":
    main()
