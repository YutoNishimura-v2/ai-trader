#!/usr/bin/env python3
"""Refine Iter63 ORB-strict BB-only quad: tune London ORB execution params.

Base: ``adaptive_quad_orb_bbonly_orb_strict_r8.yaml`` (4/4, cap=0 on 2026 M1).
Goal: raise ``worst_score`` / ``min(Mar,Apr)`` without dropping below 4/4 wins.
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


def _orb(cfg: dict) -> dict:
    for m in cfg["strategy"]["params"]["members"]:
        if m.get("id") == "lon_orb_tr":
            return m["params"]
    raise KeyError("lon_orb_tr")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/adaptive_quad_orb_bbonly_orb_strict_r8.yaml",
    )
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument(
        "--full",
        action="store_true",
        help="larger ORB grid (retest × SL buffer × tp2)",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    wins = build_rolling_windows(df)
    base = load_config(args.base)

    trials: list[tuple[str, dict]] = []

    def add(name: str, mutator):
        c = copy.deepcopy(base)
        mutator(c)
        trials.append((name, c))

    add("baseline", lambda c: None)

    # Default: compact grid (~19 trials). Use --full for 4×3×3 ORB combos.
    if args.full:
        rt_vals = (0.55, 0.65, 0.75, 0.85)
        slb_vals = (0.22, 0.30, 0.38)
        tp2_vals = (1.85, 2.0, 2.15)
    else:
        rt_vals = (0.55, 0.65, 0.75)
        slb_vals = (0.22, 0.30)
        tp2_vals = (1.9, 2.0, 2.1)

    for rt in rt_vals:
        for slb in slb_vals:
            for tp2 in tp2_vals:
                add(
                    f"rt{rt}_slb{slb}_tp2{tp2}",
                    lambda c, rt=rt, slb=slb, tp2=tp2: _orb(c).update(
                        {
                            "retest_tolerance_atr": rt,
                            "sl_atr_buffer": slb,
                            "tp2_rr": tp2,
                        }
                    ),
                )

    rows: list[tuple] = []
    for name, cfg in trials:
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=wins,
            label=f"iter64-{name}",
            i_know_this_is_tournament_evaluation=True,
        )
        sc = score_config(ev)
        wp = sc["windows_passing"]
        cap = ev.full_cap_violations
        ws = float(sc["worst_score"]) if sc["worst_score"] != float("-inf") else float("-inf")
        min_ma = min(ev.mar_return_pct or 0.0, ev.apr_return_pct or 0.0)
        w2 = next((w for w in ev.windows if w.label == "W2"), None)
        w2_tpf = w2.test_metrics.get("profit_factor") if w2 else None
        rows.append((wp, cap, ws, min_ma, ev.mar_return_pct or 0.0, ev.apr_return_pct or 0.0, w2_tpf, name))

    def sort_key(r):
        wp, cap, ws, min_ma, mar, apr, w2pf, name = r
        ws_f = ws if ws > float("-inf") else -1e9
        w2f = float(w2pf) if w2pf is not None else 0.0
        return (-wp, cap, -ws_f, -w2f, -min_ma, name)

    rows.sort(key=sort_key)
    print(f"bars={len(df)} trials={len(trials)}")
    print("wins cap worst_sc min(M,A) Mar Apr W2_tst_PF name")
    print("-" * 90)
    for r in rows[: args.top]:
        wp, cap, ws, min_ma, mar, apr, w2pf, name = r
        ws_s = f"{ws:.4f}" if ws > float("-inf") else "-inf"
        w2s = f"{float(w2pf):.3f}" if w2pf is not None else "n/a"
        print(
            f"{wp}/4 {cap} {ws_s:>8} {min_ma:7.2f} {mar:6.2f} {apr:6.2f} {w2s} {name}"
        )


if __name__ == "__main__":
    main()
