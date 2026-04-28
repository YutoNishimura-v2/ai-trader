#!/usr/bin/env python3
"""Fine sweep: pivot_trend ``tp2_rr`` on ORB-strict BB-only quad (Iter63/64).

Base YAML: ``adaptive_quad_orb_bbonly_orb_strict_r8.yaml``.
Reports harness wins, cap, worst_score, min(Mar,Apr), Mar, Apr, full%.

Use to hunt Pareto improvements vs fixed points ``_trend_tp17`` / baseline 1.6R.
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import build_rolling_windows, evaluate_config, score_config


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/adaptive_quad_orb_bbonly_orb_strict_r8.yaml",
    )
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    wins = build_rolling_windows(df)
    base = load_config(args.base)

    tp2s = [
        1.56,
        1.57,
        1.58,
        1.59,
        1.60,
        1.61,
        1.62,
        1.63,
        1.64,
        1.65,
        1.66,
        1.67,
        1.68,
        1.69,
        1.70,
        1.71,
    ]

    rows: list[tuple] = []
    for tp2 in tp2s:
        cfg = copy.deepcopy(base)
        for m in cfg["strategy"]["params"]["members"]:
            if m.get("id") == "pivot_trend":
                m["params"]["tp2_rr"] = float(tp2)
                break
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=wins,
            label=f"iter65-tp2-{tp2}",
            i_know_this_is_tournament_evaluation=True,
        )
        sc = score_config(ev)
        min_ma = min(ev.mar_return_pct or 0.0, ev.apr_return_pct or 0.0)
        ws = float(sc["worst_score"]) if sc["worst_score"] != float("-inf") else float("-inf")
        rows.append(
            (
                tp2,
                sc["windows_passing"],
                ev.full_cap_violations,
                ws,
                min_ma,
                ev.mar_return_pct or 0.0,
                ev.apr_return_pct or 0.0,
                ev.full_metrics.get("return_pct", 0.0),
            )
        )

    # Pareto: maximize (min_ma, ws) among 4/4 cap=0
    cand = [r for r in rows if r[1] == 4 and r[2] == 0]

    def dominates(a, b) -> bool:
        """a dominates b if a >= b on both objectives (strictly > on one)."""
        _, _, _, ws_a, min_a, _, _, _ = a
        _, _, _, ws_b, min_b, _, _, _ = b
        if ws_a == float("-inf") or ws_b == float("-inf"):
            return False
        return (min_a >= min_b and ws_a >= ws_b) and (min_a > min_b or ws_a > ws_b)

    pareto: list[tuple] = []
    for a in cand:
        if not any(dominates(b, a) for b in cand):
            pareto.append(a)
    pareto.sort(key=lambda r: (-r[4], -r[3]))  # min_ma, then worst_score

    print(f"bars={len(df)} tp2_grid={len(tp2s)}")
    print("tp2 | wins | cap | worst_sc | min(M,A) | Mar | Apr | full%")
    print("-" * 85)
    for r in rows:
        tp2, wp, cap, ws, min_ma, mar, apr, full = r
        ws_s = f"{ws:.4f}" if ws > float("-inf") else "-inf"
        print(
            f"{tp2:4.2f} | {wp}/4 | {cap} | {ws_s:>8} | {min_ma:7.2f} | "
            f"{mar:6.2f} | {apr:6.2f} | {full:7.1f}"
        )

    print("\nPareto frontier (4/4, cap=0), maximize min(Mar,Apr) then worst_score:")
    for r in pareto:
        tp2, wp, cap, ws, min_ma, mar, apr, full = r
        ws_s = f"{ws:.4f}" if ws > float("-inf") else "-inf"
        print(f"  tp2={tp2:.2f} min_ma={min_ma:.2f} worst={ws_s} Mar={mar:.2f} Apr={apr:.2f} full={full:.1f}")


if __name__ == "__main__":
    main()
