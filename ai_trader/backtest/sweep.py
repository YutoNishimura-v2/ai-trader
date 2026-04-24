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
