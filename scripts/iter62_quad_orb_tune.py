#!/usr/bin/env python3
"""Tune Iter61 `adaptive_quad_orb_bb_keltner_rollwin_r8` for 4/4 harness wins.

W2 fails (late Mar test): profit_factor < 1 on test while validation is fine.
Grid perturbs London ORB filters / targets and ensemble + ORB risk multipliers.
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


def _patch_members(cfg: dict, patch: dict) -> dict:
    out = copy.deepcopy(cfg)
    members = out["strategy"]["params"]["members"]
    for m in members:
        mid = m.get("id")
        if mid in patch:
            m.update(patch[mid])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/adaptive_quad_orb_bb_keltner_rollwin_r8.yaml",
    )
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    ap.add_argument("--quick", action="store_true", help="smaller grid")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)
    base = load_config(args.base)

    # Hand-picked ORB variants (stricter Asian range / break → fewer marginal trades).
    orb_grid = [
        dict(window_min=150, min_range_atr=1.45, min_break_atr=0.26, tp2_rr=1.9),
        dict(window_min=180, min_range_atr=1.45, min_break_atr=0.26, tp2_rr=2.0),
        dict(window_min=180, min_range_atr=1.50, min_break_atr=0.28, tp2_rr=2.0),
        dict(window_min=150, min_range_atr=1.50, min_break_atr=0.26, tp2_rr=1.95),
    ]
    if args.quick:
        orb_grid = orb_grid[:2]

    ens_risks = [0.75, 0.85]
    orb_risks = [0.40, 0.50]

    rows: list[tuple[dict, object]] = []
    for ens_rm in ens_risks:
        for orb_rm in orb_risks:
            for orb in orb_grid:
                patch = {
                    "tr_mr_stack": {"risk_multiplier": ens_rm},
                    "lon_orb_tr": {
                        "risk_multiplier": orb_rm,
                        "params": {
                            **orb,
                            "tp1_rr": 1.0,
                            "leg1_weight": 0.5,
                            "max_sl_atr": 4.0,
                            "max_trades_per_day": 1,
                        },
                    },
                }
                cfg = _patch_members(base, patch)
                ev = evaluate_config(
                    cfg,
                    full_df=df,
                    windows=windows,
                    label="iter62-grid",
                    i_know_this_is_tournament_evaluation=True,
                )
                sc = score_config(ev)
                min_ma = min(
                    ev.mar_return_pct or 0.0,
                    ev.apr_return_pct or 0.0,
                )
                rows.append(
                    (
                        {
                            "ens_rm": ens_rm,
                            "orb_rm": orb_rm,
                            **{f"orb_{k}": v for k, v in orb.items()},
                        },
                        (sc["windows_passing"], min_ma, sc["worst_score"], ev),
                    )
                )

    # Sort: 4 wins first, then min(Mar,Apr), then worst_score, then full return.
    def sort_key(item: tuple) -> tuple:
        d, (_, min_ma, ws, ev) = item
        wins = item[1][0]
        ws_f = float(ws) if ws != float("-inf") else -1e9
        full_ret = float(ev.full_metrics.get("return_pct", 0.0))
        cap = int(ev.full_cap_violations)
        return (-wins, -min_ma, -ws_f, cap, -full_ret)

    rows.sort(key=sort_key)
    print("bars", len(df), "trials", len(rows))
    print(
        "wins | min(Mar,Apr) | worst_sc | full% | cap | Mar | Apr | ens_rm orb_rm orb..."
    )
    print("-" * 110)
    for d, (wins, min_ma, ws, ev) in rows[:25]:
        sc = score_config(ev)
        orb_bits = " ".join(
            f"{k}={d[k]}" for k in sorted(d) if k.startswith("orb_") and k != "orb_rm"
        )
        print(
            f"{wins}/4 | {min_ma:7.2f} | {sc['worst_score']:8.4f} | "
            f"{ev.full_metrics.get('return_pct', 0):6.1f} | {ev.full_cap_violations} | "
            f"{ev.mar_return_pct or 0:5.2f} | {ev.apr_return_pct or 0:5.2f} | "
            f"ens={d['ens_rm']:.2f} orb_rm={d['orb_rm']:.2f} {orb_bits}"
        )

    best = rows[0]
    d, (wins, min_ma, ws, ev) = best
    print("\n# best keys:", d)


if __name__ == "__main__":
    main()
