"""Iter30 generalization+3x sweep harness.

Runs a programmatic sweep of ``adaptive_router`` configurations
through the rolling-window stability battery and ranks them by
``worst_score`` (consistency) and ``best_month_pct`` (the user's
3x-month gate).

Each trial is a small modification of a base config supplied via
``--base``. Modifications are described as a Python dict and applied
via deep-merge. The sweep budget is bounded by ``--max-trials``
(default 8) per micro-iteration to respect plan v3's anti-p-hacking
ratchet.

Output: a Markdown leaderboard plus a JSON dump per trial under
``artifacts/iter30/sweep/<label>/``.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import (
    DISQUALIFIED_SCORE,
    build_rolling_windows,
    evaluate_config,
    promotion_status,
    score_config,
)


def _deep_set(d: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _enumerate(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid.keys())
    out = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        out.append(dict(zip(keys, combo)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--base", required=True, type=Path,
                    help="Base config to extend (e.g., adaptive_v1.yaml).")
    ap.add_argument("--grid", action="append", required=True,
                    help='Grid axis: "dotted.path=v1,v2,v3". Repeatable.')
    ap.add_argument("--label", default="iter30-sweep")
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/iter30/sweep"))
    ap.add_argument("--n-windows", type=int, default=4)
    ap.add_argument("--research-days", type=int, default=30)
    ap.add_argument("--validation-days", type=int, default=14)
    ap.add_argument("--test-days", type=int, default=14)
    ap.add_argument("--step-days", type=int, default=14)
    ap.add_argument("--max-trials", type=int, default=8)
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(
        df,
        n_windows=args.n_windows,
        research_days=args.research_days,
        validation_days=args.validation_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    grid: dict[str, list[Any]] = {}
    for axis in args.grid:
        if "=" not in axis:
            raise SystemExit(f"--grid must be 'path=v1,v2,...': got {axis!r}")
        path, vals = axis.split("=", 1)
        grid[path.strip()] = [
            _coerce(v.strip()) for v in vals.split(",")
        ]
    combos = _enumerate(grid)
    if len(combos) > args.max_trials:
        raise SystemExit(
            f"grid has {len(combos)} combos but --max-trials={args.max_trials}; "
            "shrink the grid or raise --max-trials."
        )

    base_cfg = load_config(args.base)
    label_dir = args.out_dir / args.label
    label_dir.mkdir(parents=True, exist_ok=True)
    audit_path = args.out_dir / "audit.jsonl"

    print(f"# Iter30 sweep — label={args.label}  trials={len(combos)}")
    print(f"Dataset: {args.csv}  bars={len(df)}")
    print(f"Windows: {len(windows)}  R/V/T = {args.research_days}/{args.validation_days}/{args.test_days}d step={args.step_days}d")
    print(f"Base config: {args.base}")
    for w in windows:
        print(
            f"  {w.label}: R {w.research_span[0].date()}..{w.research_span[1].date()}  "
            f"V {w.validation_span[0].date()}..{w.validation_span[1].date()}  "
            f"T {w.test_span[0].date()}..{w.test_span[1].date()}"
        )
    print()

    rows: list[dict[str, Any]] = []
    for tid, params in enumerate(combos):
        cfg = copy.deepcopy(base_cfg)
        for path, value in params.items():
            _deep_set(cfg, path, value)
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=windows,
            config_path=args.base,
            audit_path=audit_path,
            label=f"{args.label}/trial-{tid:03d}",
            i_know_this_is_tournament_evaluation=True,
        )
        verdict = promotion_status(ev)
        row = score_config(ev)
        row["status"] = verdict.status
        row["reasons"] = verdict.reasons
        row["trial_id"] = tid
        row["params"] = params
        rows.append(row)
        worst = (
            f"{ev.worst_score:+.2f}" if ev.worst_score != DISQUALIFIED_SCORE else "DQ"
        )
        print(
            f"trial {tid:03d}  {verdict.status:12s}  "
            f"wins={ev.windows_passing}/{len(ev.windows)}  "
            f"worst={worst:7s}  best_month={ev.best_month_pct:+7.2f}% ({ev.best_month_label})  "
            f"params={params}"
        )
        # Per-trial detail.
        (label_dir / f"trial-{tid:03d}.json").write_text(
            json.dumps(
                {
                    "trial_id": tid,
                    "params": params,
                    "config_hash": ev.config_hash,
                    "status": verdict.status,
                    "reasons": verdict.reasons,
                    "best_month_pct": ev.best_month_pct,
                    "best_month_label": ev.best_month_label,
                    "windows_passing": ev.windows_passing,
                    "worst_score": ev.worst_score,
                    "mean_score": ev.mean_score,
                    "windows": [
                        {
                            "label": w.label,
                            "score": w.score if w.score != DISQUALIFIED_SCORE else None,
                            "passed": w.passed,
                            "val": w.val_metrics,
                            "test": w.test_metrics,
                        }
                        for w in ev.windows
                    ],
                    "full": ev.full_metrics,
                },
                indent=2,
                default=str,
            )
        )
    print()

    # Render leaderboard sorted by (windows_passing, worst_score, best_month_pct).
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            r["windows_passing"],
            r["worst_score"] if isinstance(r["worst_score"], (int, float)) else float("-inf"),
            r["best_month_pct"],
        ),
        reverse=True,
    )

    lines = [
        f"# Iter30 sweep leaderboard — {args.label}",
        f"trials: {len(rows)}",
        "",
        "| trial | status | wins | worst | best_month | full_ret | full_pf | full_cap | params |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows_sorted:
        worst = (
            f"{r['worst_score']:+.2f}"
            if isinstance(r["worst_score"], (int, float)) and r["worst_score"] != DISQUALIFIED_SCORE
            else "DQ"
        )
        params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        lines.append(
            f"| {r['trial_id']:03d} | {r['status']} | {r['windows_passing']}/{r['n_windows']} | "
            f"{worst} | {r['best_month_pct']:+.2f}% ({r['best_month_label']}) | "
            f"{r['full_return_pct']:+.2f}% | {r['full_profit_factor']:.2f} | "
            f"{r['full_cap_violations']} | `{params_str}` |"
        )
    lb = "\n".join(lines) + "\n"
    (label_dir / "leaderboard.md").write_text(lb)
    print(lb)
    print(f"Leaderboard saved: {label_dir / 'leaderboard.md'}")
    return 0


def _coerce(s: str) -> Any:
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "none"):
        return None
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


if __name__ == "__main__":
    raise SystemExit(main())
