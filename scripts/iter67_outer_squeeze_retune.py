#!/usr/bin/env python3
"""Retune Iter66 outer `regime_router` + squeeze trend leg for cap discipline.

Base: ``config/research_aspiration_200/regime_outer_dual_pivot_squeeze_tr_r8.yaml``
(Iter66 — originally cap-heavy).

Grid patches:
  - ``squeeze_breakout`` member ``risk_multiplier``
  - outer ``regime_risk_multipliers.trend``
  - squeeze ``tp2_rr``, ``break_atr``, ``cooldown_bars``

Sort: cap=0 first, then harness wins, then min(Mar,Apr), then worst_score.
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


def _squeeze_member(cfg: dict) -> dict:
    for m in cfg["strategy"]["params"]["members"]:
        if m.get("name") == "squeeze_breakout":
            return m
    raise KeyError("squeeze_breakout")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/regime_outer_dual_pivot_squeeze_tr_r8.yaml",
    )
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    ap.add_argument("--quick", action="store_true", help="smaller grid (~8 trials)")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    wins = build_rolling_windows(df)
    base = load_config(args.base)

    sq_rms = [0.16, 0.22, 0.30] if args.quick else [0.14, 0.20, 0.26, 0.32, 0.38]
    tr_mults = [0.45, 0.60] if args.quick else [0.40, 0.55, 0.70, 0.85]
    tp2s = [1.7, 2.0] if args.quick else [1.6, 1.85, 2.05, 2.25]
    breaks = [0.22, 0.28] if args.quick else [0.18, 0.22, 0.26, 0.30]
    cools = [24] if args.quick else [18, 24, 32]

    rows: list[tuple] = []
    for sq_rm in sq_rms:
        for trm in tr_mults:
            for tp2 in tp2s:
                for ba in breaks:
                    for cool in cools:
                        cfg = copy.deepcopy(base)
                        rr = cfg["strategy"]["params"].setdefault("regime_risk_multipliers", {})
                        rr["trend"] = float(trm)
                        sm = _squeeze_member(cfg)
                        sm["risk_multiplier"] = float(sq_rm)
                        sm["params"]["tp2_rr"] = float(tp2)
                        sm["params"]["break_atr"] = float(ba)
                        sm["params"]["cooldown_bars"] = int(cool)

                        ev = evaluate_config(
                            cfg,
                            full_df=df,
                            windows=wins,
                            label="iter67-grid",
                            i_know_this_is_tournament_evaluation=True,
                        )
                        sc = score_config(ev)
                        min_ma = min(ev.mar_return_pct or 0.0, ev.apr_return_pct or 0.0)
                        ws = float(sc["worst_score"]) if sc["worst_score"] != float("-inf") else float("-inf")
                        rows.append(
                            (
                                ev.full_cap_violations,
                                sc["windows_passing"],
                                min_ma,
                                ws,
                                ev.mar_return_pct or 0.0,
                                ev.apr_return_pct or 0.0,
                                ev.full_metrics.get("return_pct", 0.0),
                                sq_rm,
                                trm,
                                tp2,
                                ba,
                                cool,
                            )
                        )

    def key(r):
        cap, wp, min_ma, ws, *_ = r
        ws_f = ws if ws > float("-inf") else -1e9
        return (cap, -wp, -min_ma, -ws_f)

    rows.sort(key=key)
    print(f"bars={len(df)} trials={len(rows)}")
    print("cap | wins | worst_sc | min(M,A) | Mar | Apr | full% | sq_rm tr_mult tp2 break cool")
    print("-" * 100)
    for r in rows[:30]:
        cap, wp, min_ma, ws, mar, apr, full, sq_rm, trm, tp2, ba, cool = r
        ws_s = f"{ws:.4f}" if ws > float("-inf") else "-inf"
        print(
            f"{cap:3d} | {wp}/4 | {ws_s:>8} | {min_ma:7.2f} | {mar:6.2f} | {apr:6.2f} | "
            f"{full:7.1f} | {sq_rm} {trm} {tp2} {ba} {cool}"
        )

    cap0 = [r for r in rows if r[0] == 0]
    print(f"\ncap_clean rows: {len(cap0)} / {len(rows)}")


if __name__ == "__main__":
    main()
