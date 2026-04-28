#!/usr/bin/env python3
"""Sweep April-focused tweaks on adaptive_dual_pivot_chop_moon_r9_tp9.

Grid over optional pivot_chop ``block_hours_utc``, ``max_trades_per_day``,
and ``adx_max``. Keeps rows with cap_violations==0, sorts by April return.

Usage::

    python3 scripts/iter37_dual_pivot_april_sweep.py --csv data/xauusd_m1_2026.csv
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
        default="config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r9_tp9.yaml",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    template = load_config(args.base)
    members = template["strategy"]["params"]["members"]
    assert members[0]["id"] == "pivot_chop"

    block_options: list[list[int] | None] = [
        None,
        [13],
        [14],
        [12, 13],
        [13, 14],
        [14, 15],
        [8, 9, 13],
        [15, 16],
        [12, 13, 14],
    ]
    mtd_opts = [3, 4, 5, 6]
    adx_opts = [28.0, 30.0, 32.0, 35.0]

    rows: list[dict] = []
    for adx in adx_opts:
        for mtd in mtd_opts:
            for bh in block_options:
                cfg = copy.deepcopy(template)
                chop = cfg["strategy"]["params"]["members"][0]["params"]
                chop["adx_max"] = float(adx)
                chop["max_trades_per_day"] = int(mtd)
                if bh is None:
                    chop.pop("block_hours_utc", None)
                else:
                    chop["block_hours_utc"] = list(bh)
                m = _run(df, cfg)
                if m.get("cap_violations", 0) != 0 or m.get("ruin_flag"):
                    continue
                mon = m.get("monthly_returns") or {}
                apr = float(mon.get("2026-04", 0)) if "2026-04" in mon else float("nan")
                rows.append(
                    {
                        "adx_max": adx,
                        "max_trades_per_day": mtd,
                        "block_hours_utc": bh,
                        "apr_pct": round(apr, 2),
                        "feb_pct": round(float(mon.get("2026-02", 0)), 2) if "2026-02" in mon else None,
                        "full_pct": round(float(m.get("return_pct", 0)), 2),
                        "pf": round(float(m.get("profit_factor", 0)), 3),
                    }
                )

    rows.sort(key=lambda r: r["apr_pct"], reverse=True)
    print(json.dumps({"cap_clean_trials": len(rows), "top_by_april": rows[:12]}, indent=2))


if __name__ == "__main__":
    main()
