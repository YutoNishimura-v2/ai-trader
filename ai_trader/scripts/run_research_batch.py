"""Run a GOLD-only high-risk research batch across strategy families.

This is the "hundreds of refinements" harness requested in the
2026-04-25 GOLD-only revision. It deliberately runs only research and
validation windows; tournament evaluation remains a separate explicit
step.

The built-in ``gold_hrhr_v1`` preset is intentionally broad but still
pre-declared:

    python -m ai_trader.scripts.run_research_batch \
        --csv data/xauusd_m1_2026.csv --batch-id gold-v1 \
        --preset gold_hrhr_v1 --max-trials 120
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..backtest.engine import BacktestEngine
from ..backtest.metrics import compute_metrics
from ..backtest.splitter import load_recent_only_held_out
from ..backtest.sweep import _enumerate_grid, _partition
from ..broker.paper import PaperBroker
from ..config import load_config
from ..data.csv_loader import load_ohlcv_csv
from ..risk.fx import FixedFX
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy
from ..utils.logging import get_logger


def _preset_gold_hrhr_v1() -> list[dict[str, Any]]:
    """Pre-declared GOLD-only research batch.

    Keep grids compact per family, then let the batch breadth produce
    the larger search. All variants retain break-even-capable two-leg
    management where the underlying strategy supports it.
    """
    return [
        {
            "name": "news_fade_timing",
            "config": "config/news_fade_aggressive.yaml",
            "grid": {
                "delay_min": [3, 5],
                "window_min": [30, 60],
                "trigger_atr": [1.5, 2.0],
                "sl_atr_mult": [0.5],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 1,
        },
        {
            "name": "news_breakout_timing",
            "config": "config/news_breakout_aggressive.yaml",
            "grid": {
                "delay_min": [5, 10],
                "initial_range_min": [3, 5],
                "break_atr": [0.3, 0.6],
                "max_sl_atr": [1.5, 2.5],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 1,
        },
        {
            "name": "vwap_chop_candidate",
            "config": "config/vwap_reversion_aggressive.yaml",
            "grid": {
                "dev_mult": [2.0, 2.5],
                "htf_filter": ["none", "H1"],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 20,
        },
        {
            "name": "bb_scalper_candidate",
            "config": "config/bb_scalper_aggressive.yaml",
            "grid": {
                "bb_n": [40, 60],
                "bb_k": [2.0, 2.5],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 20,
        },
        {
            "name": "bos_structure_candidate",
            "config": "config/bos_retest_aggressive.yaml",
            "grid": {
                "swing_lookback": [4, 6],
                "min_legs": [1, 2],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 10,
        },
        {
            "name": "mtf_zigzag_candidate",
            "config": "config/mtf_zigzag_bos_aggressive.yaml",
            "grid": {
                "htf": ["M5"],
                "zigzag_threshold_atr": [0.5, 1.0],
                "retest_tolerance_atr": [0.5, 1.0],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 5,
        },
        {
            "name": "session_sweep_reclaim_candidate",
            "config": "config/session_sweep_reclaim_aggressive.yaml",
            "grid": {
                "trade_start_hour": [7, 12],
                "trade_end_hour": [16],
                "min_sweep_atr": [0.1, 0.2],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 10,
        },
        {
            "name": "squeeze_breakout_candidate",
            "config": "config/squeeze_breakout_aggressive.yaml",
            "grid": {
                "bb_n": [20, 40],
                "breakout_atr": [0.1, 0.2],
                "risk.risk_per_trade_pct": [2.0, 3.0],
            },
            "min_validation_trades": 10,
        },
    ]


def _build_instrument(cfg: dict[str, Any]) -> InstrumentSpec:
    inst_cfg = cfg["instrument"]
    return InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )


def _run_one(
    *,
    cfg: dict[str, Any],
    df,
    params: dict[str, Any],
) -> dict[str, Any]:
    strat_params, risk_overrides, exec_overrides = _partition(params)
    instrument = _build_instrument(cfg)
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None
    risk_cfg = cfg["risk"]
    exec_cfg = cfg["execution"]
    base_strategy_params = cfg.get("strategy", {}).get("params", {}) or {}
    risk_kwargs = {
        "risk_per_trade_pct": float(risk_cfg["risk_per_trade_pct"]),
        "daily_profit_target_pct": float(risk_cfg["daily_profit_target_pct"]),
        "daily_max_loss_pct": float(risk_cfg["daily_max_loss_pct"]),
        "withdraw_half_of_daily_profit": bool(risk_cfg.get("withdraw_half_of_daily_profit", True)),
        "max_concurrent_positions": int(risk_cfg.get("max_concurrent_positions", 1)),
        "lot_cap_per_unit_balance": float(risk_cfg.get("lot_cap_per_unit_balance", 0.0)),
    }
    risk_kwargs.update(risk_overrides)
    broker_kwargs = {
        "spread_points": int(exec_cfg["spread_points"]),
        "slippage_points": int(exec_cfg["slippage_points"]),
        "commission_per_lot": float(exec_cfg.get("commission_per_lot", 0.0)),
    }
    broker_kwargs.update(exec_overrides)

    strat = get_strategy(
        cfg["strategy"]["name"],
        **{**base_strategy_params, **strat_params},
    )
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        **risk_kwargs,
    )
    broker = PaperBroker(
        instrument=instrument,
        **broker_kwargs,
    )
    result = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    return compute_metrics(result, starting_balance=float(cfg["account"]["starting_balance"]))


def _score(row: dict[str, Any]) -> float:
    v = row["validation_metrics"]
    r = row["research_metrics"]
    min_trades = row["min_validation_trades"]
    trades = int(v.get("trades", 0))
    score = float(v.get("monthly_pct_mean", 0.0))
    # Recent performance gets an explicit boost/penalty.
    score += 0.5 * float(v.get("april_return_pct", 0.0))
    score += 0.25 * float(v.get("recent_14d_return_pct", 0.0))
    # High-risk guardrails.
    score += min(0.0, float(v.get("max_drawdown_pct", 0.0))) * 0.25
    if trades < min_trades:
        score -= 10_000.0 - trades
    if v.get("ruin_flag") or r.get("ruin_flag"):
        score -= 100_000.0
    if int(v.get("cap_violations", 0)) > 0 or int(r.get("cap_violations", 0)) > 0:
        score -= 50_000.0
    return score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--preset", default="gold_hrhr_v1", choices=["gold_hrhr_v1"])
    ap.add_argument("--max-trials", type=int, default=120)
    ap.add_argument("--research-days", type=int, default=60)
    ap.add_argument("--validation-days", type=int, default=14)
    ap.add_argument("--tournament-days", type=int, default=14)
    args = ap.parse_args()

    log = get_logger("ai_trader.research_batch")
    df = load_ohlcv_csv(args.csv)
    split = load_recent_only_held_out(
        df,
        research_days=args.research_days,
        validation_days=args.validation_days,
        tournament_days=args.tournament_days,
    )
    families = _preset_gold_hrhr_v1()
    combos_total = sum(len(_enumerate_grid(f["grid"])) for f in families)
    if combos_total > args.max_trials:
        raise SystemExit(
            f"preset expands to {combos_total} trials; raise --max-trials or shrink preset"
        )

    out_dir = Path("artifacts/research_batches") / args.batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    trial_id = 0
    log.info(
        "loaded %s bars; recent_only research=%s validation=%s tournament=held-out",
        len(df), len(split.research), len(split.validation),
    )

    for fam in families:
        cfg = load_config(fam["config"])
        for params in _enumerate_grid(fam["grid"]):
            r_metrics = _run_one(cfg=cfg, df=split.research, params=params)
            v_metrics = _run_one(cfg=cfg, df=split.validation, params=params)
            row = {
                "trial_id": trial_id,
                "family": fam["name"],
                "config": fam["config"],
                "strategy": cfg["strategy"]["name"],
                "params": params,
                "min_validation_trades": fam["min_validation_trades"],
                "research_metrics": r_metrics,
                "validation_metrics": v_metrics,
                "score": 0.0,
            }
            row["score"] = _score(row)
            rows.append(row)
            log.info(
                "trial=%s family=%s val_ret=%+.2f%% val_pf=%.2f val_trades=%s score=%.2f",
                trial_id, fam["name"],
                v_metrics.get("return_pct", 0.0),
                v_metrics.get("profit_factor", 0.0),
                v_metrics.get("trades", 0),
                row["score"],
            )
            trial_id += 1

    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    payload = {
        "batch_id": args.batch_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "preset": args.preset,
        "csv": str(args.csv),
        "split_mode": "recent_only",
        "research_days": args.research_days,
        "validation_days": args.validation_days,
        "tournament_days": args.tournament_days,
        "tournament_held_out": True,
        "leaderboard": ranked,
    }
    (out_dir / "summary.json").write_text(json.dumps(payload, indent=2, default=str))
    print("Top 10 GOLD-only research trials (tournament held out):")
    for row in ranked[:10]:
        vm = row["validation_metrics"]
        print(
            f"#{row['trial_id']:03d} {row['family']} score={row['score']:.2f} "
            f"val_ret={vm.get('return_pct', 0):+.2f}% "
            f"val_monthly={vm.get('monthly_pct_mean', 0):+.2f}% "
            f"val_pf={vm.get('profit_factor', 0):.2f} "
            f"trades={vm.get('trades', 0)} params={row['params']}"
        )
    print(f"\nwrote {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
