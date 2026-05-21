#!/usr/bin/env python3
"""Iterations 69–100: exhaustive micro-grid on rollwin **pivot_chop** only.

Base merged config: ``adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml``.
Grid (32 points = iter 69 … 100):

  - ``adx_max``: 24, 26, 28, 30
  - ``block_hours_utc``: [13,14] or [13,14,15]
  - ``tp2_rr`` (chop member only): 7.5, 8.5, 9.5, 10.5

Writes ``config/research_aspiration_200/iter69_100_rollwin_chop_grid.jsonl`` (one JSON object per line).

Run::

    python3 scripts/iter69_100_rollwin_chop_grid.py --csv data/xauusd_m1_2026.csv
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import build_rolling_windows, evaluate_config, score_config


def _chop_params(cfg: dict) -> dict:
    for m in cfg["strategy"]["params"]["members"]:
        if m.get("id") == "pivot_chop":
            return m["params"]
    raise KeyError("pivot_chop")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml",
    )
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    ap.add_argument(
        "--out",
        default="config/research_aspiration_200/iter69_100_rollwin_chop_grid.jsonl",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    wins = build_rolling_windows(df)
    base = load_config(args.base)

    adx_vals = [24.0, 26.0, 28.0, 30.0]
    blocks = [[13, 14], [13, 14, 15]]
    tp2s = [7.5, 8.5, 9.5, 10.5]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    summ = out_path.with_suffix(".summary.json")
    if summ.exists():
        summ.unlink()

    iter_no = 69
    best = None
    for adx in adx_vals:
        for bh in blocks:
            for tp2 in tp2s:
                cfg = copy.deepcopy(base)
                p = _chop_params(cfg)
                p["adx_max"] = float(adx)
                p["block_hours_utc"] = list(bh)
                p["tp2_rr"] = float(tp2)

                ev = evaluate_config(
                    cfg,
                    full_df=df,
                    windows=wins,
                    label=f"iter{iter_no}",
                    i_know_this_is_tournament_evaluation=True,
                )
                sc = score_config(ev)
                min_ma = min(ev.mar_return_pct or 0.0, ev.apr_return_pct or 0.0)
                row = {
                    "iter": iter_no,
                    "adx_max": adx,
                    "block_hours_utc": bh,
                    "chop_tp2_rr": tp2,
                    "full_return_pct": ev.full_metrics.get("return_pct"),
                    "full_cap_violations": ev.full_cap_violations,
                    "mar_return_pct": ev.mar_return_pct,
                    "apr_return_pct": ev.apr_return_pct,
                    "min_mar_apr_pct": min_ma,
                    "windows_passing": sc["windows_passing"],
                    "n_windows": sc["n_windows"],
                    "worst_score": sc["worst_score"],
                    "profit_factor": ev.full_metrics.get("profit_factor"),
                }
                with open(out_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, default=str) + "\n")

                if ev.full_cap_violations == 0:
                    cand = (min_ma, sc["windows_passing"], sc["worst_score"], iter_no, row)
                    if best is None:
                        best = cand
                    else:
                        ws = float(sc["worst_score"]) if sc["worst_score"] != float("-inf") else -1e9
                        bws = float(best[2]) if best[2] != float("-inf") else -1e9
                        if (min_ma > best[0]) or (
                            abs(min_ma - best[0]) < 1e-6
                            and sc["windows_passing"] > best[1]
                        ) or (
                            abs(min_ma - best[0]) < 1e-6
                            and sc["windows_passing"] == best[1]
                            and ws > bws
                        ):
                            best = cand

                iter_no += 1

    assert iter_no == 101, f"expected 101, got {iter_no}"

    summary_path = out_path.with_suffix(".summary.json")
    summary = {
        "iters": "69-100",
        "n_rows": 32,
        "jsonl": str(out_path),
        "best_cap_clean_by_min_mar_apr_then_wins_then_worst_score": best[4] if best else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("wrote", out_path)
    print("summary", summary_path)
    if best:
        print("best", best[4])


if __name__ == "__main__":
    main()
