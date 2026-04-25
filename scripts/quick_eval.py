"""Quick eval helper: run any config across (a) full window, (b) the
recent_only research/validation/tournament split, and print the
key metrics in one shot. Used during the ultimate-stack iteration.

Not part of the package — lives under /scripts to keep the
``ai_trader`` package boundary clean.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.backtest.splitter import load_recent_only_held_out
from ai_trader.backtest.sweep import risk_kwargs_from_config
from ai_trader.broker.paper import PaperBroker
from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy


def _build(cfg: dict[str, Any]):
    inst_cfg = cfg["instrument"]
    instrument = InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None
    risk_cfg = cfg["risk"]
    # NOTE: pre-2026-04-25 quick_eval did NOT pass dynamic-risk
    # kwargs to RiskManager, silently ignoring the meta-risk layer.
    # Use risk_kwargs_from_config() to wire ALL knobs (dynamic_risk,
    # confidence floor/ceiling, drawdown throttle, etc.).
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        **risk_kwargs_from_config(risk_cfg),
    )
    exec_cfg = cfg["execution"]
    broker = PaperBroker(
        instrument=instrument,
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )
    strat = get_strategy(cfg["strategy"]["name"], **cfg["strategy"].get("params", {}))
    return strat, risk, broker, float(cfg["account"]["starting_balance"])


def _run(df, cfg) -> dict:
    strat, risk, broker, sb = _build(cfg)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    return compute_metrics(res, starting_balance=sb)


_FIELDS = (
    "trades", "profit_factor", "return_pct", "max_drawdown_pct",
    "min_equity_pct", "monthly_pct_mean", "monthly_pct_max",
    "monthly_pct_min", "best_day_pct", "worst_day_pct",
    "cap_violations", "ruin_flag", "april_return_pct",
    "march_return_pct",
)


def _fmt(m: dict) -> str:
    return " ".join(f"{k}={m.get(k)}" for k in _FIELDS)


def _fmt_jpy(m: dict, starting_balance: float) -> str:
    """Account-currency-native one-liner. Account is JPY at HFM Katana.

    Returns final balance, net P&L, daily P&L extremes, and per-month
    P&L deltas in ¥. Useful when the user wants to see absolute money,
    not just percentages.
    """
    net = float(m.get("net_profit", 0.0))
    final = float(m.get("final_balance", 0.0)) + float(m.get("withdrawn_total", 0.0))
    bd_pct = float(m.get("best_day_pct", 0.0))
    wd_pct = float(m.get("worst_day_pct", 0.0))
    # Per-month JPY deltas: turn the % series into ¥ on a fresh-balance basis
    # (close to compound from starting_balance month by month).
    monthly = m.get("monthly_returns") or {}
    bal = starting_balance
    parts = []
    for k in sorted(monthly.keys()):
        pct = float(monthly[k])
        delta_yen = bal * (pct / 100.0)
        parts.append(f"{k}={pct:+.2f}%/¥{int(round(delta_yen)):+,}")
        bal *= (1.0 + pct / 100.0)
    monthly_str = " ".join(parts)
    return (
        f"start=¥{starting_balance:,.0f} -> final=¥{final:,.0f} "
        f"(net=¥{net:+,.0f}) "
        f"best_day≈¥{starting_balance * bd_pct/100:+,.0f} "
        f"worst_day≈¥{starting_balance * wd_pct/100:+,.0f}\n"
        f"  monthly: {monthly_str}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--research-days", type=int, default=60)
    ap.add_argument("--validation-days", type=int, default=14)
    ap.add_argument("--tournament-days", type=int, default=14)
    ap.add_argument("--also-7d", action="store_true")
    ap.add_argument("--full-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    cfg = load_config(args.config)
    sb = float(cfg["account"]["starting_balance"])
    ccy = cfg["account"].get("currency", "USD")

    print(f"# Account: {ccy}, starting balance = {sb:,.0f}\n")

    full = _run(df, cfg)
    print("== FULL ==")
    print(_fmt(full))
    print(_fmt_jpy(full, sb))

    if args.full_only:
        if args.json:
            print(json.dumps({"full": full}, default=str))
        return 0

    split = load_recent_only_held_out(
        df,
        research_days=args.research_days,
        validation_days=args.validation_days,
        tournament_days=args.tournament_days,
        i_know_this_is_tournament_evaluation=True,
    )
    rm = _run(split.research, cfg)
    vm = _run(split.validation, cfg)
    tm = _run(split.tournament, cfg)
    print(f"\n== RESEARCH ({len(split.research)} bars) ==")
    print(_fmt(rm))
    print(_fmt_jpy(rm, sb))
    print(f"\n== VALIDATION ({len(split.validation)} bars) ==")
    print(_fmt(vm))
    print(_fmt_jpy(vm, sb))
    print(f"\n== TOURNAMENT 14d ({len(split.tournament)} bars, HELD OUT) ==")
    print(_fmt(tm))
    print(_fmt_jpy(tm, sb))

    if args.also_7d:
        split7 = load_recent_only_held_out(
            df,
            research_days=args.research_days,
            validation_days=args.validation_days,
            tournament_days=7,
            i_know_this_is_tournament_evaluation=True,
        )
        tm7 = _run(split7.tournament, cfg)
        print(f"\n== TOURNAMENT 7d ({len(split7.tournament)} bars) ==")
        print(_fmt(tm7))
        print(_fmt_jpy(tm7, sb))

    if args.json:
        print(json.dumps({
            "full": full, "research": rm, "validation": vm, "tournament_14d": tm,
        }, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
