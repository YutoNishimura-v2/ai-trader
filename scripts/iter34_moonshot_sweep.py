#!/usr/bin/env python3
"""Measure how close simulation-only configs get to +200%/month aspiration.

Runs full-period backtests on a CSV and prints monthly returns, best month,
full return, PF, ruin, cap violations. Optional small risk grid for the
squeeze moonshot template.

Example::

    python3 scripts/iter34_moonshot_sweep.py --csv data/xauusd_m1_2026.csv
"""
from __future__ import annotations

import argparse
import copy
import glob
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


def _instrument_from_cfg(cfg: dict) -> InstrumentSpec:
    inst = cfg["instrument"]
    return InstrumentSpec(
        symbol=inst["symbol"],
        contract_size=float(inst["contract_size"]),
        tick_size=float(inst["tick_size"]),
        tick_value=float(inst["tick_value"]),
        quote_currency=inst.get("quote_currency", "USD"),
        min_lot=float(inst.get("min_lot", 0.01)),
        lot_step=float(inst.get("lot_step", 0.01)),
        is_24_7=bool(inst.get("is_24_7", False)),
    )


def run_full(df, cfg: dict) -> dict:
    inst = _instrument_from_cfg(cfg)
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
        commission_per_lot=float(ex.get("commission_per_lot", 0.0)),
    )
    sp = cfg["strategy"].get("params") or {}
    strat = get_strategy(cfg["strategy"]["name"], **sp)
    sb = float(cfg["account"]["starting_balance"])
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    return compute_metrics(res, starting_balance=sb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument(
        "--risk-grid",
        action="store_true",
        help="Also sweep risk_per_trade_pct on moonshot_squeeze_lon_r15 template",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    root = Path("config/research_aspiration_200")
    paths = sorted(
        p
        for p in glob.glob(str(root / "moonshot*.yaml"))
        if Path(p).is_file()
    )
    rows: list[dict] = []
    for p in paths:
        cfg = load_config(p)
        m = run_full(df, cfg)
        monthly = m.get("monthly_returns") or {}
        best = max(monthly.values(), default=0.0)
        rows.append(
            {
                "path": p,
                "return_pct": round(float(m.get("return_pct", 0)), 2),
                "profit_factor": round(float(m.get("profit_factor", 0)), 3),
                "best_month_pct": round(float(best), 2),
                "trades": m.get("trades"),
                "ruin": m.get("ruin_flag"),
                "cap_violations": m.get("cap_violations"),
                "monthly": {k: round(float(v), 2) for k, v in sorted(monthly.items())},
            }
        )
        print(json.dumps(rows[-1]))

    if args.risk_grid:
        base = load_config(str(root / "moonshot_squeeze_lon_r15.yaml"))
        for rp in (8.0, 10.0, 12.0, 15.0, 18.0):
            c = copy.deepcopy(base)
            c["risk"]["risk_per_trade_pct"] = rp
            m = run_full(df, c)
            monthly = m.get("monthly_returns") or {}
            best = max(monthly.values(), default=0.0)
            rec = {
                "path": f"<grid squeeze r={rp}>",
                "risk_per_trade_pct": rp,
                "return_pct": round(float(m.get("return_pct", 0)), 2),
                "profit_factor": round(float(m.get("profit_factor", 0)), 3),
                "best_month_pct": round(float(best), 2),
                "trades": m.get("trades"),
                "ruin": m.get("ruin_flag"),
                "cap_violations": m.get("cap_violations"),
                "monthly": {k: round(float(v), 2) for k, v in sorted(monthly.items())},
            }
            print(json.dumps(rec))
            rows.append(rec)

    # stderr: headline
    best_row = max(rows, key=lambda r: r["best_month_pct"])
    print(
        "\n# best_month_pct:",
        best_row.get("best_month_pct"),
        best_row.get("path") or best_row.get("risk_per_trade_pct"),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
