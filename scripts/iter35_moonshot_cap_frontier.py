#!/usr/bin/env python3
"""Map risk % × TP2 RR for lone daily pivot moonshot (cap-clean frontier).

Scans a small grid on a CSV and prints rows with cap_violations==0.
Default template: research_aspiration_200/moonshot_pivot_daily_r18_tp9.yaml
with HTF gate stripped.

Example::

    python3 scripts/iter35_moonshot_cap_frontier.py --csv data/xauusd_m1_2026.csv
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
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    base = load_config(args.template)

    clean: list[dict] = []
    for rp in [4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0]:
        for tp2 in [5.0, 6.0, 7.0, 8.0, 9.0, 9.5, 10.0]:
            c = copy.deepcopy(base)
            c["risk"]["risk_per_trade_pct"] = float(rp)
            p = c["strategy"]["params"]
            p["tp2_rr"] = float(tp2)
            for k in ("htf", "adx_period", "adx_max"):
                p.pop(k, None)
            m = _run(df, c)
            if m.get("cap_violations", 0) != 0 or m.get("ruin_flag"):
                continue
            mon = m.get("monthly_returns") or {}
            best = max(mon.values()) if mon else 0.0
            clean.append(
                {
                    "risk_per_trade_pct": rp,
                    "tp2_rr": tp2,
                    "return_pct": round(float(m.get("return_pct", 0)), 2),
                    "best_month_pct": round(float(best), 2),
                    "profit_factor": round(float(m.get("profit_factor", 0)), 3),
                    "trades": m.get("trades"),
                }
            )

    clean.sort(key=lambda r: r["best_month_pct"], reverse=True)
    print(json.dumps({"cap_clean_count": len(clean), "top": clean[:15]}, indent=2))


if __name__ == "__main__":
    main()
