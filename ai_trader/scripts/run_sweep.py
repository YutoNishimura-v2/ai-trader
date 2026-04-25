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
from ..backtest.splitter import (
    InterleavedSplit,
    load_interleaved_held_out,
    load_recent_held_out,
    load_recent_only_held_out,
    load_with_tournament_held_out,
)
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
    ap.add_argument("--strategy", default=None,
                    help="Override cfg['strategy']['name'] for this sweep.")
    ap.add_argument("--max-trials", type=int, default=20)
    ap.add_argument("--objective", default="profit_factor")
    # Plan v3 as of 2026-04-24: recent-regime performance dominates.
    # Default split is date-based: tournament = last N days,
    # validation = M days before that. --split-mode ratio restores
    # the proportional split.
    ap.add_argument(
        "--split-mode",
        default="recent",
        choices=["recent", "ratio", "interleaved", "recent_only"],
        help="recent=date-based tournament window (default); "
        "ratio=proportional split (legacy); "
        "interleaved=round-robin blocks (each role samples every regime); "
        "recent_only=all three windows from the tail, for 'current regime only'.",
    )
    ap.add_argument("--tournament-days", type=int, default=30)
    ap.add_argument("--validation-days", type=int, default=60)
    ap.add_argument("--research-days", type=int, default=30,
                    help="only used with --split-mode recent_only")
    ap.add_argument("--block-bars", type=int, default=5760,
                    help="only used with --split-mode interleaved (~4 days of M1)")
    ap.add_argument("--research-per-cycle", type=int, default=3,
                    help="only used with --split-mode interleaved")
    ap.add_argument("--validation-per-cycle", type=int, default=1,
                    help="only used with --split-mode interleaved")
    ap.add_argument("--tournament-per-cycle", type=int, default=1,
                    help="only used with --split-mode interleaved")
    # Weight each sweep trial by how much of its profit came from
    # the *validation* period — i.e. the recent regime. Defaults
    # off because it changes the objective; on for recent-regime
    # evaluation runs.
    ap.add_argument(
        "--score-on",
        default="research",
        choices=["research", "validation"],
        help="Which window's metrics the sweep ranks trials by.",
    )
    ap.add_argument(
        "--min-validation-trades",
        type=int,
        default=20,
        help="Trials with fewer trades than this on the scored "
        "window are demoted to the bottom of the ranking. 20 is "
        "the plan-v3 rule-of-thumb floor for trustable metrics.",
    )
    ap.add_argument(
        "--grid", action="append", default=[],
        help="Grid entry as key=v1,v2,v3 (strategy.*, risk.*, exec.* "
        "prefixes route the key). Repeatable. If given, replaces the "
        "built-in default grid entirely.",
    )
    ap.add_argument(
        "--max-research-dd-pct",
        type=float,
        default=None,
        help="Optional filter: demote trials whose research max_drawdown_pct "
        "is worse than -abs(this). E.g. --max-research-dd-pct 15 demotes "
        "anything with DD worse than -15 %%.",
    )
    args = ap.parse_args()

    log = get_logger("ai_trader.sweep")
    cfg = load_config(args.config)
    if args.strategy:
        # Preserve config-provided params if the strategy name matches;
        # otherwise reset params (strategy mismatch means old params are
        # invalid for the new strategy's constructor).
        existing = cfg.get("strategy", {})
        if existing.get("name") == args.strategy:
            cfg["strategy"] = {"name": args.strategy, "params": existing.get("params", {})}
        else:
            cfg["strategy"] = {"name": args.strategy, "params": {}}

    df = load_ohlcv_csv(args.csv)
    interleaved_split: InterleavedSplit | None = None
    if args.split_mode == "recent":
        split = load_recent_held_out(
            df, tournament_days=args.tournament_days,
            validation_days=args.validation_days,
        )
        split_desc = (
            f"recent (tournament_days={args.tournament_days}, "
            f"validation_days={args.validation_days})"
        )
    elif args.split_mode == "recent_only":
        split = load_recent_only_held_out(
            df,
            research_days=args.research_days,
            validation_days=args.validation_days,
            tournament_days=args.tournament_days,
        )
        split_desc = (
            f"recent_only (research={args.research_days}d, "
            f"validation={args.validation_days}d, tournament={args.tournament_days}d)"
        )
    elif args.split_mode == "interleaved":
        interleaved_split = load_interleaved_held_out(
            df,
            block_bars=args.block_bars,
            research_per_cycle=args.research_per_cycle,
            validation_per_cycle=args.validation_per_cycle,
            tournament_per_cycle=args.tournament_per_cycle,
        )
        split_desc = (
            f"interleaved (block={args.block_bars} bars, "
            f"R/V/T per cycle={args.research_per_cycle}/{args.validation_per_cycle}/"
            f"{args.tournament_per_cycle})"
        )
    else:
        split = load_with_tournament_held_out(df)
        split_desc = "ratio (0.75/0.17/held-out)"

    if interleaved_split is not None:
        log.info(
            "loaded %s bars; split=%s; research=%s blocks/%s bars validation=%s blocks/%s bars tournament=held-out",
            len(df), split_desc,
            len(interleaved_split.research), interleaved_split.research_bars,
            len(interleaved_split.validation), interleaved_split.validation_bars,
        )
    else:
        log.info(
            "loaded %s bars; split=%s; research=%s validation=%s tournament=held-out",
            len(df), split_desc, len(split.research), len(split.validation),
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
        dynamic_risk_enabled=bool(risk_cfg.get("dynamic_risk_enabled", False)),
        min_risk_per_trade_pct=(
            float(risk_cfg["min_risk_per_trade_pct"])
            if risk_cfg.get("min_risk_per_trade_pct") is not None
            else None
        ),
        max_risk_per_trade_pct=(
            float(risk_cfg["max_risk_per_trade_pct"])
            if risk_cfg.get("max_risk_per_trade_pct") is not None
            else None
        ),
        confidence_risk_floor=float(risk_cfg.get("confidence_risk_floor", 0.75)),
        confidence_risk_ceiling=float(risk_cfg.get("confidence_risk_ceiling", 1.5)),
        drawdown_soft_limit_pct=float(risk_cfg.get("drawdown_soft_limit_pct", 12.0)),
        drawdown_hard_limit_pct=float(risk_cfg.get("drawdown_hard_limit_pct", 25.0)),
        drawdown_soft_multiplier=float(risk_cfg.get("drawdown_soft_multiplier", 0.7)),
        drawdown_hard_multiplier=float(risk_cfg.get("drawdown_hard_multiplier", 0.4)),
    )
    exec_cfg = cfg["execution"]
    exec_defaults = dict(
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )

    # A deliberately modest grid. Plan v3 caps total trials; we stay
    # under the cap so adding a small dimension later is easy.
    # Override via --grid key=v1,v2[,v3] (repeatable).
    grid: dict[str, list] = {
        "sl_atr_mult": [1.0, 1.5, 2.0],
        "tp_rr": [1.5, 2.0, 3.0],
        "cooldown_bars": [6, 12],
    }
    if args.grid:
        grid = {}
        for spec in args.grid:
            if "=" not in spec:
                raise SystemExit(f"bad --grid spec (need key=v1,v2,...): {spec!r}")
            k, vs = spec.split("=", 1)
            values: list = []
            for v in vs.split(","):
                v = v.strip()
                try:
                    values.append(int(v))
                except ValueError:
                    try:
                        values.append(float(v))
                    except ValueError:
                        values.append(v)
            grid[k.strip()] = values

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
        strategy_defaults=cfg.get("strategy", {}).get("params", {}) or {},
        max_trials=args.max_trials,
        objective=args.objective,
        higher_is_better=True,
    )

    from ..backtest.sweep import _partition, _run_on_blocks

    per_trial_validation: list[dict] = []
    base_strategy_params = cfg.get("strategy", {}).get("params", {}) or {}

    if interleaved_split is not None:
        # Block-mode sweep: enumerate the grid manually so each trial
        # runs as N independent block-backtests on research and on
        # validation, with metrics aggregated.
        from ..backtest.sweep import _enumerate_grid, _param_hash, Trial, SweepResult
        combos = _enumerate_grid(grid)
        if len(combos) > args.max_trials:
            raise SystemExit(
                f"grid has {len(combos)} combos but max_trials={args.max_trials}. "
                "Shrink the grid or raise --max-trials."
            )
        out_dir = Path("artifacts/sweeps") / args.sweep_id
        out_dir.mkdir(parents=True, exist_ok=True)
        index_path = out_dir / "index.jsonl"
        span_str = f"interleaved-{len(interleaved_split.research)}r-{len(interleaved_split.validation)}v"
        trials_acc: list[Trial] = []
        with open(index_path, "w", encoding="utf-8") as idx_f:
            for i, params in enumerate(combos):
                strat_params, risk_overrides, exec_overrides = _partition(params)
                merged_strat = {**base_strategy_params, **strat_params}
                merged_risk = {**risk_defaults, **risk_overrides}
                merged_exec = {**exec_defaults, **exec_overrides}
                r_metrics = _run_on_blocks(
                    cfg["strategy"]["name"], merged_strat, merged_risk, merged_exec,
                    instrument, fx, sweep_cfg.account_currency,
                    sweep_cfg.starting_balance, sweep_cfg.max_leverage,
                    interleaved_split.research, compute_metrics,
                )
                v_metrics = _run_on_blocks(
                    cfg["strategy"]["name"], merged_strat, merged_risk, merged_exec,
                    instrument, fx, sweep_cfg.account_currency,
                    sweep_cfg.starting_balance, sweep_cfg.max_leverage,
                    interleaved_split.validation, compute_metrics,
                )
                h = _param_hash(params, (span_str, str(args.tournament_days)))
                trials_acc.append(Trial(trial_id=i, params=params, metrics=r_metrics, param_hash=h))
                idx_f.write(json.dumps({
                    "trial_id": i, "params": params, "param_hash": h,
                    "metrics": r_metrics,
                }, default=str) + "\n")
                log.info(
                    "trial %s/%s research_pf=%.3f val_pf=%.3f val_monthly=%.2f%% trades_r=%d trades_v=%d",
                    i + 1, len(combos),
                    r_metrics.get("profit_factor", 0),
                    v_metrics.get("profit_factor", 0),
                    v_metrics.get("monthly_pct_mean", 0),
                    r_metrics.get("trades", 0),
                    v_metrics.get("trades", 0),
                )
                per_trial_validation.append({
                    "trial_id": i, "params": params,
                    "research_metrics": r_metrics,
                    "validation_metrics": v_metrics,
                })
        result = SweepResult(sweep_id=args.sweep_id, trials=trials_acc, best=None, out_dir=out_dir)
    else:
        result = run_sweep(sweep_cfg, split.research)
        for trial in result.trials:
            strat_params, risk_overrides, exec_overrides = _partition(trial.params)
            merged_strat = {**base_strategy_params, **strat_params}
            strat = get_strategy(cfg["strategy"]["name"], **merged_strat)
            risk = RiskManager(
                starting_balance=sweep_cfg.starting_balance,
                max_leverage=sweep_cfg.max_leverage,
                instrument=instrument,
                account_currency=sweep_cfg.account_currency,
                fx=fx,
                **{**risk_defaults, **risk_overrides},
            )
            broker = PaperBroker(instrument=instrument, **{**exec_defaults, **exec_overrides})
            engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
            v_result = engine.run(split.validation)
            v_metrics = compute_metrics(v_result, starting_balance=sweep_cfg.starting_balance)
            per_trial_validation.append({
                "trial_id": trial.trial_id,
                "params": trial.params,
                "research_metrics": trial.metrics,
                "validation_metrics": v_metrics,
            })

    # Choose the winner by the user-selected window.
    # Any trial with fewer trades than --min-validation-trades on
    # the scored window is demoted (plan v3 lesson: zero-trades is
    # not a good score).
    floor = args.min_validation_trades

    dd_cap = abs(args.max_research_dd_pct) if args.max_research_dd_pct is not None else None

    def _score(row: dict) -> float:
        m = row["validation_metrics"] if args.score_on == "validation" else row["research_metrics"]
        trades = int(m.get("trades", 0))
        v = m.get(args.objective, float("nan"))
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = float("nan")
        if v != v or v == float("inf"):
            v = float("-inf")
        if trades < floor:
            v = float("-inf") + trades
        if dd_cap is not None:
            research_dd = abs(float(row["research_metrics"].get("max_drawdown_pct", 0.0)))
            if research_dd > dd_cap:
                v = float("-inf") + trades * 1e-9
        return v

    winner = max(per_trial_validation, key=_score) if per_trial_validation else None

    summary = {
        "sweep_id": args.sweep_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "csv": str(args.csv),
        "split_mode": args.split_mode,
        "score_on": args.score_on,
        "min_validation_trades": args.min_validation_trades,
        "tournament_held_out": True,
        "winner": winner,
        "trials": per_trial_validation,
    }
    if interleaved_split is not None:
        summary["research_blocks"] = len(interleaved_split.research)
        summary["validation_blocks"] = len(interleaved_split.validation)
        summary["research_bars"] = interleaved_split.research_bars
        summary["validation_bars"] = interleaved_split.validation_bars
    else:
        summary["research_bars"] = len(split.research)
        summary["validation_bars"] = len(split.validation)
        summary["research_range"] = [str(split.research.index[0]), str(split.research.index[-1])]
        summary["validation_range"] = [str(split.validation.index[0]), str(split.validation.index[-1])]
    summary_path = result.out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("wrote %s", summary_path)

    # Print a concise one-line comparison per trial, then the winner.
    for row in sorted(per_trial_validation, key=_score, reverse=True):
        rm = row["research_metrics"]; vm = row["validation_metrics"]
        print(
            f"trial={row['trial_id']:2d} params={row['params']} "
            f"research: PF={rm.get('profit_factor', 0):.2f} ret={rm.get('return_pct', 0):+.2f}% "
            f"DD={rm.get('max_drawdown_pct', 0):.2f}% trades={rm.get('trades', 0)}  "
            f"validation: PF={vm.get('profit_factor', 0):.2f} ret={vm.get('return_pct', 0):+.2f}% "
            f"DD={vm.get('max_drawdown_pct', 0):.2f}% trades={vm.get('trades', 0)}"
        )
    if winner is not None:
        print(f"\n[winner by {args.score_on}.{args.objective}] trial={winner['trial_id']} params={winner['params']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
