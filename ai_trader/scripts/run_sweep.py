"""Walk-forward parameter sweep on a real OHLCV CSV.

Runs a bounded grid sweep on the *research* window of a walk-forward
split, then verifies the sweep's best candidate on the validation
window. Tournament window is left held out (see splitter docstring).

    python -m ai_trader.scripts.run_sweep \\
        --config config/default.yaml \\
        --csv data/xauusd_m5_2024.csv \\
        --sweep-id seed-2024-v1
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ..backtest.engine import BacktestEngine
from ..backtest.metrics import compute_metrics
from ..backtest.splitter import load_with_tournament_held_out
from ..backtest.sweep import SweepConfig, run_sweep
from ..broker.paper import PaperBroker
from ..config import load_config
from ..data.csv_loader import load_ohlcv_csv
from ..risk.fx import FixedFX
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy
from ..utils.logging import get_logger


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--sweep-id", required=True)
    ap.add_argument("--max-trials", type=int, default=20)
    ap.add_argument("--objective", default="profit_factor")
    args = ap.parse_args()

    log = get_logger("ai_trader.sweep")
    cfg = load_config(args.config)

    df = load_ohlcv_csv(args.csv)
    split = load_with_tournament_held_out(df)
    log.info(
        "loaded %s bars; research=%s validation=%s tournament=held-out",
        len(df), len(split.research), len(split.validation),
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

    risk_cfg = cfg["risk"]
    risk_defaults = dict(
        risk_per_trade_pct=float(risk_cfg["risk_per_trade_pct"]),
        daily_profit_target_pct=float(risk_cfg["daily_profit_target_pct"]),
        daily_max_loss_pct=float(risk_cfg["daily_max_loss_pct"]),
        withdraw_half_of_daily_profit=bool(risk_cfg.get("withdraw_half_of_daily_profit", True)),
        max_concurrent_positions=int(risk_cfg.get("max_concurrent_positions", 1)),
        lot_cap_per_unit_balance=float(risk_cfg.get("lot_cap_per_unit_balance", 0.0)),
    )
    exec_cfg = cfg["execution"]
    exec_defaults = dict(
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )

    # A deliberately modest grid. Plan v3 caps total trials; we stay
    # under the cap so adding a small dimension later is easy.
    grid = {
        "sl_atr_mult": [1.0, 1.5, 2.0],
        "tp_rr": [1.5, 2.0, 3.0],
        "cooldown_bars": [6, 12],
    }  # 3 * 3 * 2 = 18 <= max_trials (20)

    sweep_cfg = SweepConfig(
        sweep_id=args.sweep_id,
        strategy_name=cfg["strategy"]["name"],
        grid=grid,
        instrument=instrument,
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        risk_defaults=risk_defaults,
        exec_defaults=exec_defaults,
        max_trials=args.max_trials,
        objective=args.objective,
        higher_is_better=True,
    )

    result = run_sweep(sweep_cfg, split.research)

    # Verify the best candidate on the validation window.
    validation_metrics: dict[str, float] = {}
    if result.best is not None and len(split.validation) > 0:
        strat = get_strategy(cfg["strategy"]["name"], **result.best.params)
        risk = RiskManager(
            starting_balance=sweep_cfg.starting_balance,
            max_leverage=sweep_cfg.max_leverage,
            instrument=instrument,
            account_currency=sweep_cfg.account_currency,
            fx=fx,
            **risk_defaults,
        )
        broker = PaperBroker(instrument=instrument, **exec_defaults)
        engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
        v_result = engine.run(split.validation)
        validation_metrics = compute_metrics(v_result, starting_balance=sweep_cfg.starting_balance)

    summary = {
        "sweep_id": args.sweep_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "csv": str(args.csv),
        "research_bars": len(split.research),
        "validation_bars": len(split.validation),
        "tournament_held_out": True,
        "best_trial_id": result.best.trial_id if result.best else None,
        "best_params": result.best.params if result.best else None,
        "best_research_metrics": result.best.metrics if result.best else None,
        "validation_metrics": validation_metrics,
    }
    summary_path = result.out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("wrote %s", summary_path)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
