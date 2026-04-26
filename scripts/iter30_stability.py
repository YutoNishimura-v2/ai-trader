"""Iter30 stability-harness CLI.

Runs one or more configs through the rolling-window battery defined
in :mod:`ai_trader.research.stability` and emits a Markdown
leaderboard sorted by ``worst_score`` desc, then ``windows_passing``
desc, then ``best_month_pct`` desc.

Usage::

    python3 scripts/iter30_stability.py \\
        --csv data/xauusd_m1_2026.csv \\
        --label iter30-baseline \\
        --config config/iter28/v4_ext_a_dow_no_fri.yaml \\
        --config config/iter29/v4_plus_h4_protector_conc1.yaml

Each test window is opened via the literal opt-in token tracked in
``stability._AUDIT_OPT_IN_TOKEN``; every opening is appended to the
``audit.jsonl`` file in the chosen ``--out-dir``. This produces an
auditable paper trail for "this window was opened exactly once per
config" — the discipline the project keeps drifting away from when
chasing peak headlines.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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


def _format_score(score: float) -> str:
    if not isinstance(score, (int, float)) or score == DISQUALIFIED_SCORE:
        return "DQ"
    return f"{score:+7.2f}"


def _format_pct(x: float) -> str:
    return f"{x:+7.2f}%"


def _render_leaderboard(rows: list[dict[str, Any]], window_labels: list[str]) -> str:
    """Render a Markdown leaderboard table.

    Columns: config | status | wins/N | worst_score | best_month |
    full_ret | full_pf | full_cap | per-window val_pf/test_pf/val_ret/test_ret/score
    """
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            r["windows_passing"],
            r["worst_score"] if isinstance(r["worst_score"], (int, float)) else float("-inf"),
            r["best_month_pct"],
        ),
        reverse=True,
    )

    head = [
        "| config | status | wins | worst | best_month | full_ret | full_pf | full_cap |"
    ]
    for w in window_labels:
        head[0] += f" {w} val_pf/ret | {w} test_pf/ret | {w} score |"
    sep = ["|" + "|".join(["---"] * (8 + 3 * len(window_labels))) + "|"]
    body: list[str] = []
    for r in rows_sorted:
        cells = [
            f"`{Path(r['config_path']).name if r['config_path'] else r['config_hash']}`",
            r.get("status", "?"),
            f"{r['windows_passing']}/{r['n_windows']}",
            (
                _format_score(r["worst_score"])
                if isinstance(r["worst_score"], float)
                else str(r["worst_score"])
            ),
            f"{r['best_month_pct']:+.2f}% ({r['best_month_label']})",
            _format_pct(r["full_return_pct"]),
            f"{r['full_profit_factor']:.2f}",
            str(r["full_cap_violations"]),
        ]
        for w in window_labels:
            val_pf = r.get(f"{w}_val_pf", 0.0)
            val_ret = r.get(f"{w}_val_ret", 0.0)
            test_pf = r.get(f"{w}_test_pf", 0.0)
            test_ret = r.get(f"{w}_test_ret", 0.0)
            score = r.get(f"{w}_score", "DQ")
            cells.append(f"{val_pf:.2f} / {val_ret:+.2f}%")
            cells.append(f"{test_pf:.2f} / {test_ret:+.2f}%")
            cells.append(_format_score(score) if isinstance(score, float) else str(score))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join(head + sep + body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument(
        "--config",
        action="append",
        required=True,
        help="One or more YAML configs to evaluate.",
    )
    ap.add_argument(
        "--label",
        default="iter30",
        help="Audit-log label (free-form string identifying the run).",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("artifacts/iter30/stability"),
    )
    ap.add_argument("--n-windows", type=int, default=4)
    ap.add_argument("--research-days", type=int, default=30)
    ap.add_argument("--validation-days", type=int, default=14)
    ap.add_argument("--test-days", type=int, default=14)
    ap.add_argument("--step-days", type=int, default=14)
    ap.add_argument(
        "--allow-audit-violation",
        action="store_true",
        help=(
            "Stamp audit log entries with audit_violation. Use only if you "
            "deliberately want to bypass the opt-in token (you almost "
            "never do)."
        ),
    )
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
    args.out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = args.out_dir / "audit.jsonl"
    label_dir = args.out_dir / args.label
    label_dir.mkdir(parents=True, exist_ok=True)

    print(f"# Iter30 stability — label={args.label}")
    print(f"Dataset: {args.csv}  bars={len(df)}  span={df.index[0]} .. {df.index[-1]}")
    print(f"Windows: {len(windows)}  R/V/T = {args.research_days}/{args.validation_days}/{args.test_days}d step={args.step_days}d")
    for w in windows:
        print(
            f"  {w.label}: research {w.research_span[0].date()}..{w.research_span[1].date()}  "
            f"validation {w.validation_span[0].date()}..{w.validation_span[1].date()}  "
            f"test {w.test_span[0].date()}..{w.test_span[1].date()}  "
            f"(R={len(w.research)} V={len(w.validation)} T={len(w.test)} bars)"
        )
    print()

    rows: list[dict[str, Any]] = []
    for cfg_path in args.config:
        cfg_path = Path(cfg_path)
        cfg = load_config(cfg_path)
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=windows,
            config_path=cfg_path,
            audit_path=audit_path,
            label=args.label,
            i_know_this_is_tournament_evaluation=not args.allow_audit_violation,
        )
        verdict = promotion_status(ev)
        row = score_config(ev)
        row["status"] = verdict.status
        row["reasons"] = verdict.reasons
        rows.append(row)

        # Per-config detail JSON.
        detail_path = label_dir / f"{cfg_path.stem}.json"
        detail_path.write_text(
            json.dumps(
                {
                    "config_path": str(cfg_path),
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
        # Live progress line.
        worst = (
            f"{ev.worst_score:+.2f}"
            if ev.worst_score != DISQUALIFIED_SCORE
            else "DQ"
        )
        print(
            f"{cfg_path.name:50s}  {verdict.status:12s}  "
            f"wins={ev.windows_passing}/{len(ev.windows)}  "
            f"worst={worst}  best_month={ev.best_month_pct:+.2f}% ({ev.best_month_label})"
        )
    print()

    window_labels = [w.label for w in windows]
    table = _render_leaderboard(rows, window_labels)
    out_md = label_dir / "leaderboard.md"
    out_md.write_text(table + "\n")
    print(table)
    print(f"\nLeaderboard saved: {out_md}")
    print(f"Audit log: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
