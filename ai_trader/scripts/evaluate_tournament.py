"""Evaluate a single strategy configuration on the TOURNAMENT window.

This is the "you're sure?" script. It reveals the held-out tournament
window exactly once per promotion attempt and writes the result to
``artifacts/tournament/<ts>-<label>.json``. Plan v3 discipline says:

- You must have already cleared the research AND validation gates
  with this exact parameter set on this exact data file.
- Calling this script counts as your single tournament evaluation
  for this strategy family on this data; use the result to inform
  the promotion decision, not to tune parameters.

Usage:

    python -m ai_trader.scripts.evaluate_tournament \\
        --config config/bb_scalper.yaml \\
        --csv data/xauusd_m1_2026.csv \\
        --strategy bb_scalper \\
        --label bb-scalper-2026-iter3 \\
        --tournament-days 6 --validation-days 34 \\
        --param bb_n=60 --param bb_k=2.5 --param tp_target=middle
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ..backtest.engine import BacktestEngine
from ..backtest.metrics import compute_metrics
from ..backtest.splitter import (
    load_interleaved_held_out,
    load_recent_held_out,
    load_recent_only_held_out,
)
from ..backtest.sweep import _partition
from ..broker.paper import PaperBroker
from ..config import load_config
from ..data.csv_loader import load_ohlcv_csv
from ..risk.fx import FixedFX
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy
from ..utils.logging import get_logger


def _parse_params(kv: list[str]) -> dict:
    out = {}
    for s in kv:
        if "=" not in s:
            raise SystemExit(f"bad --param spec: {s!r}")
        k, v = s.split("=", 1)
        v = v.strip()
        try:
            out[k.strip()] = int(v)
        except ValueError:
            try:
                out[k.strip()] = float(v)
            except ValueError:
                out[k.strip()] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--tournament-days", type=int, required=True)
    ap.add_argument("--validation-days", type=int, required=True)
    ap.add_argument("--research-days", type=int, default=30,
                    help="only used with --split-mode recent_only")
    ap.add_argument("--split-mode", default="recent",
                    choices=["recent", "recent_only"])
    ap.add_argument("--param", action="append", default=[])
    args = ap.parse_args()

    log = get_logger("ai_trader.tournament")
    cfg = load_config(args.config)

    df = load_ohlcv_csv(args.csv)
    if args.split_mode == "recent_only":
        split = load_recent_only_held_out(
            df,
            research_days=args.research_days,
            validation_days=args.validation_days,
            tournament_days=args.tournament_days,
            i_know_this_is_tournament_evaluation=True,
        )
    else:
        split = load_recent_held_out(
            df,
            tournament_days=args.tournament_days,
            validation_days=args.validation_days,
            i_know_this_is_tournament_evaluation=True,
        )
    if len(split.tournament) == 0:
        raise SystemExit("tournament window is empty; check --tournament-days")
    log.info(
        "tournament window: %s -> %s (%s bars)",
        split.tournament.index[0], split.tournament.index[-1], len(split.tournament),
    )

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

    params = _parse_params(args.param)
    strat_params, risk_overrides, exec_overrides = _partition(params)
    # Merge any config-provided base strategy params (e.g. ensemble's
    # members list) so we don't drop them when the CLI overrides only
    # risk/exec knobs.
    base_strategy_params = cfg.get("strategy", {}).get("params", {}) or {}
    strat_params = {**base_strategy_params, **strat_params}

    risk_cfg = cfg["risk"]
    exec_cfg = cfg["execution"]

    strat = get_strategy(args.strategy, **strat_params)
    risk_kwargs = dict(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        risk_per_trade_pct=float(risk_cfg["risk_per_trade_pct"]),
        daily_profit_target_pct=float(risk_cfg["daily_profit_target_pct"]),
        daily_max_loss_pct=float(risk_cfg["daily_max_loss_pct"]),
        withdraw_half_of_daily_profit=bool(risk_cfg.get("withdraw_half_of_daily_profit", True)),
        max_concurrent_positions=int(risk_cfg.get("max_concurrent_positions", 1)),
        lot_cap_per_unit_balance=float(risk_cfg.get("lot_cap_per_unit_balance", 0.0)),
    )
    risk_kwargs.update(risk_overrides)
    risk = RiskManager(**risk_kwargs)
    broker_kwargs = dict(
        instrument=instrument,
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )
    broker_kwargs.update(exec_overrides)
    broker = PaperBroker(**broker_kwargs)
    engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
    result = engine.run(split.tournament)
    metrics = compute_metrics(result, starting_balance=float(cfg["account"]["starting_balance"]))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path("artifacts/tournament")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}-{args.label}.json"
    payload = {
        "timestamp": ts,
        "strategy": args.strategy,
        "label": args.label,
        "config": str(args.config),
        "csv": str(args.csv),
        "params": params,
        "tournament_range": [str(split.tournament.index[0]), str(split.tournament.index[-1])],
        "tournament_bars": len(split.tournament),
        "metrics": metrics,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    log.info("wrote %s", out_path)
    print(json.dumps(metrics, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
