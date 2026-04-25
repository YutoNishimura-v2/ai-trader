"""Bounded parameter-sweep harness.

The iteration loop (plan v3 §Research methodology) caps the number
of parameter tries per iteration so that "improvement" doesn't
compound from p-hacking. Every attempted combination is hashed +
logged, so we can spot when a sweep has been re-run against the
same window.

Sweep outputs:

- ``artifacts/sweeps/<sweep_id>/index.jsonl``: one line per trial,
  with the params, the metrics, and a hash of (params, window-span).
- ``artifacts/sweeps/<sweep_id>/best.json``: the best trial by the
  chosen objective.

The sweep is intentionally simple: it enumerates a grid. Random /
Bayesian search can be added later; a grid is the safest default
because it's fully reproducible and auditable.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from ..broker.paper import PaperBroker
from ..risk.fx import FXConverter
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.base import BaseStrategy
from ..strategy.registry import get_strategy
from ..utils.logging import get_logger
from .engine import BacktestEngine, BacktestResult
from .metrics import compute_metrics


@dataclass
class SweepConfig:
    sweep_id: str
    strategy_name: str
    grid: dict[str, list[Any]]
    instrument: InstrumentSpec
    starting_balance: float
    max_leverage: float
    account_currency: str
    fx: FXConverter | None
    risk_defaults: dict[str, Any]
    exec_defaults: dict[str, Any]
    strategy_defaults: dict[str, Any] = field(default_factory=dict)
    max_trials: int = 20
    objective: str = "profit_factor"
    higher_is_better: bool = True


@dataclass
class Trial:
    trial_id: int
    params: dict[str, Any]
    metrics: dict[str, Any]
    param_hash: str


@dataclass
class SweepResult:
    sweep_id: str
    trials: list[Trial]
    best: Trial | None
    out_dir: Path


def _partition(params: dict[str, Any]) -> tuple[dict, dict, dict]:
    """Split a flat ``params`` dict into (strategy, risk, exec).

    Keys with a dotted prefix are routed:
      - ``risk.foo`` → risk override ``foo``
      - ``exec.foo`` → exec override ``foo``
      - ``strategy.foo`` → strategy kwarg ``foo``
      - everything else → strategy kwarg (backwards-compat default)
    """
    strat: dict[str, Any] = {}
    risk: dict[str, Any] = {}
    exec_: dict[str, Any] = {}
    for k, v in params.items():
        if k.startswith("risk."):
            risk[k[len("risk."):]] = v
        elif k.startswith("exec."):
            exec_[k[len("exec."):]] = v
        elif k.startswith("strategy."):
            strat[k[len("strategy."):]] = v
        else:
            strat[k] = v
    return strat, risk, exec_


def _enumerate_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    if not keys:
        return [{}]
    combos = list(itertools.product(*(grid[k] for k in keys)))
    return [dict(zip(keys, combo)) for combo in combos]


def _param_hash(params: dict[str, Any], window_span: tuple[str, str]) -> str:
    payload = json.dumps({"params": params, "span": window_span}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def run_sweep(
    cfg: SweepConfig,
    df: pd.DataFrame,
    *,
    artifacts_root: Path | str = "artifacts/sweeps",
) -> SweepResult:
    """Run a bounded grid sweep and persist per-trial results.

    Raises if the grid has more combinations than ``max_trials`` —
    the caller must either shrink the grid or raise the cap
    explicitly (this is the anti-p-hacking ratchet).
    """
    log = get_logger("ai_trader.sweep")
    out_dir = Path(artifacts_root) / cfg.sweep_id
    out_dir.mkdir(parents=True, exist_ok=True)

    combos = _enumerate_grid(cfg.grid)
    if len(combos) > cfg.max_trials:
        raise ValueError(
            f"grid has {len(combos)} combos but max_trials={cfg.max_trials}. "
            "Shrink the grid, or explicitly raise max_trials in the config."
        )

    window_span = (str(df.index[0]), str(df.index[-1]))
    index_path = out_dir / "index.jsonl"
    trials: list[Trial] = []

    with open(index_path, "w", encoding="utf-8") as idx_f:
        for i, params in enumerate(combos):
            strat_params, risk_overrides, exec_overrides = _partition(params)
            merged_strat = {**cfg.strategy_defaults, **strat_params}
            strat: BaseStrategy = get_strategy(cfg.strategy_name, **merged_strat)
            broker = PaperBroker(
                instrument=cfg.instrument,
                **{**cfg.exec_defaults, **exec_overrides},
            )
            risk = RiskManager(
                starting_balance=cfg.starting_balance,
                max_leverage=cfg.max_leverage,
                instrument=cfg.instrument,
                account_currency=cfg.account_currency,
                fx=cfg.fx,
                **{**cfg.risk_defaults, **risk_overrides},
            )
            engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
            result: BacktestResult = engine.run(df)
            metrics = compute_metrics(result, starting_balance=cfg.starting_balance)
            h = _param_hash(params, window_span)
            trial = Trial(trial_id=i, params=params, metrics=metrics, param_hash=h)
            trials.append(trial)
            row = {
                "trial_id": i,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sweep_id": cfg.sweep_id,
                "strategy": cfg.strategy_name,
                "params": params,
                "param_hash": h,
                "window_span": list(window_span),
                "metrics": metrics,
            }
            idx_f.write(json.dumps(row, default=str) + "\n")
            log.info(
                "trial %s/%s %s=%s %s=%.4f",
                i + 1, len(combos), cfg.strategy_name, h, cfg.objective,
                _safe_metric(metrics, cfg.objective),
            )

    best = _pick_best(trials, cfg.objective, cfg.higher_is_better)
    if best is not None:
        (out_dir / "best.json").write_text(
            json.dumps(
                {
                    "sweep_id": cfg.sweep_id,
                    "strategy": cfg.strategy_name,
                    "params": best.params,
                    "param_hash": best.param_hash,
                    "metrics": best.metrics,
                    "window_span": list(window_span),
                },
                indent=2,
                default=str,
            )
        )
    log.info("sweep %s: %s trials, best=%s", cfg.sweep_id, len(trials), best.trial_id if best else None)
    return SweepResult(sweep_id=cfg.sweep_id, trials=trials, best=best, out_dir=out_dir)


def _safe_metric(metrics: dict[str, Any], key: str) -> float:
    v = metrics.get(key, float("nan"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _pick_best(trials: list[Trial], objective: str, higher_is_better: bool) -> Trial | None:
    if not trials:
        return None
    def keyfn(t: Trial) -> float:
        v = _safe_metric(t.metrics, objective)
        # nan goes to the worst bucket.
        if v != v:  # nan check
            return float("-inf") if higher_is_better else float("inf")
        return v
    return max(trials, key=keyfn) if higher_is_better else min(trials, key=keyfn)


def _run_on_blocks(
    strat_name: str,
    strat_params: dict,
    risk_kwargs: dict,
    exec_kwargs: dict,
    instrument: InstrumentSpec,
    fx: FXConverter | None,
    account_currency: str,
    starting_balance: float,
    max_leverage: float,
    blocks: list,
    compute_metrics_fn,
) -> dict[str, float]:
    """Run the backtest engine independently on each block and
    aggregate metrics. Balance is RESET to starting_balance for
    each block, because cross-block continuity doesn't exist (the
    block boundaries are fake time jumps).

    Aggregation:
    - net_profit_pct_mean: average per-block return_pct
    - monthly_pct_mean:    average per-block monthly_pct_mean
      (already normalized to 'per month' inside each block)
    - trades:              sum
    - profit_factor:       (sum gross_profit) / (sum gross_loss)
    - max_drawdown_pct:    min (worst) across blocks
    - worst_day_pct:       min across blocks
    """
    import pandas as pd

    total_gp = 0.0
    total_gl = 0.0
    total_trades = 0
    total_wins = 0
    ret_pcts: list[float] = []
    dd_pcts: list[float] = []
    worst_days: list[float] = []
    best_days: list[float] = []
    monthly_means: list[float] = []
    monthly_maxes: list[float] = []
    monthly_mins: list[float] = []
    cap_violations = 0
    daily_target_hits = 0
    daily_max_loss_hits = 0

    for block in blocks:
        if len(block) < 100:
            continue
        strat = get_strategy(strat_name, **strat_params)
        risk = RiskManager(
            starting_balance=starting_balance,
            max_leverage=max_leverage,
            instrument=instrument,
            account_currency=account_currency,
            fx=fx,
            **risk_kwargs,
        )
        broker = PaperBroker(instrument=instrument, **exec_kwargs)
        engine = BacktestEngine(strategy=strat, risk=risk, broker=broker)
        r = engine.run(block)
        m = compute_metrics_fn(r, starting_balance=starting_balance)
        total_gp += m["gross_profit"]
        total_gl += m["gross_loss"]
        total_trades += m["trades"]
        total_wins += m["wins"]
        ret_pcts.append(m["return_pct"])
        dd_pcts.append(m["max_drawdown_pct"])
        worst_days.append(m["worst_day_pct"])
        best_days.append(m["best_day_pct"])
        monthly_means.append(m.get("monthly_pct_mean", 0.0))
        monthly_maxes.append(m.get("monthly_pct_max", 0.0))
        monthly_mins.append(m.get("monthly_pct_min", 0.0))
        cap_violations += m.get("cap_violations", 0)
        daily_target_hits += m.get("daily_target_hits", 0)
        daily_max_loss_hits += m.get("daily_max_loss_hits", 0)

    if total_trades == 0:
        return {
            "trades": 0, "profit_factor": 0.0, "return_pct": 0.0,
            "max_drawdown_pct": 0.0, "worst_day_pct": 0.0,
            "best_day_pct": 0.0, "monthly_pct_mean": 0.0,
            "monthly_pct_max": 0.0, "monthly_pct_min": 0.0,
            "win_rate": 0.0, "cap_violations": 0,
            "daily_target_hits": 0, "daily_max_loss_hits": 0,
            "blocks": len(blocks),
        }
    pf = (total_gp / total_gl) if total_gl > 0 else float("inf")
    return {
        "trades": total_trades,
        "wins": total_wins,
        "win_rate": total_wins / total_trades if total_trades else 0.0,
        "gross_profit": total_gp,
        "gross_loss": total_gl,
        "profit_factor": pf,
        "return_pct": float(sum(ret_pcts) / len(ret_pcts)) if ret_pcts else 0.0,
        "max_drawdown_pct": float(min(dd_pcts)) if dd_pcts else 0.0,
        "worst_day_pct": float(min(worst_days)) if worst_days else 0.0,
        "best_day_pct": float(max(best_days)) if best_days else 0.0,
        "monthly_pct_mean": float(sum(monthly_means) / len(monthly_means)) if monthly_means else 0.0,
        "monthly_pct_max": float(max(monthly_maxes)) if monthly_maxes else 0.0,
        "monthly_pct_min": float(min(monthly_mins)) if monthly_mins else 0.0,
        "cap_violations": cap_violations,
        "daily_target_hits": daily_target_hits,
        "daily_max_loss_hits": daily_max_loss_hits,
        "blocks": len(blocks),
    }


def risk_kwargs_from_config(risk_cfg: dict[str, Any]) -> dict[str, Any]:
    """Build RiskManager kwargs from a config risk section."""
    return {
        "risk_per_trade_pct": float(risk_cfg["risk_per_trade_pct"]),
        "daily_profit_target_pct": float(risk_cfg["daily_profit_target_pct"]),
        "daily_max_loss_pct": float(risk_cfg["daily_max_loss_pct"]),
        "withdraw_half_of_daily_profit": bool(risk_cfg.get("withdraw_half_of_daily_profit", True)),
        "max_concurrent_positions": int(risk_cfg.get("max_concurrent_positions", 1)),
        "lot_cap_per_unit_balance": float(risk_cfg.get("lot_cap_per_unit_balance", 0.0)),
        "dynamic_risk_enabled": bool(risk_cfg.get("dynamic_risk_enabled", False)),
        "min_risk_per_trade_pct": (
            float(risk_cfg["min_risk_per_trade_pct"])
            if risk_cfg.get("min_risk_per_trade_pct") is not None
            else None
        ),
        "max_risk_per_trade_pct": (
            float(risk_cfg["max_risk_per_trade_pct"])
            if risk_cfg.get("max_risk_per_trade_pct") is not None
            else None
        ),
        "confidence_risk_floor": float(risk_cfg.get("confidence_risk_floor", 0.75)),
        "confidence_risk_ceiling": float(risk_cfg.get("confidence_risk_ceiling", 1.5)),
        "drawdown_soft_limit_pct": float(risk_cfg.get("drawdown_soft_limit_pct", 12.0)),
        "drawdown_hard_limit_pct": float(risk_cfg.get("drawdown_hard_limit_pct", 25.0)),
        "drawdown_soft_multiplier": float(risk_cfg.get("drawdown_soft_multiplier", 0.7)),
        "drawdown_hard_multiplier": float(risk_cfg.get("drawdown_hard_multiplier", 0.4)),
    }
