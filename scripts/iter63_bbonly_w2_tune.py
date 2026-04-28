#!/usr/bin/env python3
"""Tune Iter62 `adaptive_quad_orb_bbonly_rollwin_r8` for higher worst_score.

The bottleneck is usually W2 (late Mar test): keep 4/4 harness + cap=0 while
raising min(val, test) return on the weakest window.
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


def _member_by_id(cfg: dict, mid: str) -> dict:
    for m in cfg["strategy"]["params"]["members"]:
        if m.get("id") == mid:
            return m
    raise KeyError(mid)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="config/research_aspiration_200/adaptive_quad_orb_bbonly_rollwin_r8.yaml",
    )
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    wins = build_rolling_windows(df)
    base = load_config(args.base)

    trials: list[tuple[str, dict]] = []

    def add(name: str, mutator):
        c = copy.deepcopy(base)
        mutator(c)
        trials.append((name, c))

    # Baseline reference
    add("baseline", lambda c: None)

    # ADX regime width
    for ram, tam in ((18.0, 26.0), (19.0, 25.0), (17.0, 27.0), (22.0, 24.0)):
        add(f"adx_ram{ram}_tam{tam}", lambda c, r=ram, t=tam: (
            c["strategy"]["params"].update({"range_adx_max": r, "trend_adx_min": t})
        ))

    # BB: less churn in transition
    add(
        "bb_cool14_mtd3",
        lambda c: _member_by_id(c, "tr_bb_only")["params"]["members"][0]["params"].update(
            {"cooldown_bars": 14, "max_trades_per_day": 3}
        ),
    )
    add(
        "bb_cool12_mtd2",
        lambda c: _member_by_id(c, "tr_bb_only")["params"]["members"][0]["params"].update(
            {"cooldown_bars": 12, "max_trades_per_day": 2}
        ),
    )

    # BB session
    add(
        "bb_session_lonny",
        lambda c: _member_by_id(c, "tr_bb_only")["params"]["members"][0]["params"].update(
            {"session": "london_or_ny"}
        ),
    )

    # ORB stricter + longer window
    add(
        "orb_strict",
        lambda c: _member_by_id(c, "lon_orb_tr")["params"].update(
            {
                "window_min": 180,
                "min_range_atr": 1.45,
                "min_break_atr": 0.26,
                "tp2_rr": 2.0,
            }
        ),
    )

    # Risk tilt: less BB, more ORB
    add(
        "risk_bb075_orb055",
        lambda c: (
            _member_by_id(c, "tr_bb_only").update({"risk_multiplier": 0.75}),
            _member_by_id(c, "lon_orb_tr").update({"risk_multiplier": 0.55}),
        ),
    )
    add(
        "risk_bb080_orb040",
        lambda c: (
            _member_by_id(c, "tr_bb_only").update({"risk_multiplier": 0.80}),
            _member_by_id(c, "lon_orb_tr").update({"risk_multiplier": 0.40}),
        ),
    )

    # Member order: ORB before BB ensemble (same YAML priority list)
    def _orb_first(c: dict) -> None:
        mems = c["strategy"]["params"]["members"]

        def _prio(m: dict) -> int:
            i = m.get("id")
            if i == "lon_orb_tr":
                return 0
            if i == "tr_bb_only":
                return 1
            return 2

        mems.sort(key=_prio)

    add("orb_first", _orb_first)

    rows: list[tuple] = []
    for name, cfg in trials:
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=wins,
            label=f"iter63-{name}",
            i_know_this_is_tournament_evaluation=True,
        )
        sc = score_config(ev)
        wp = sc["windows_passing"]
        cap = ev.full_cap_violations
        ws = float(sc["worst_score"]) if sc["worst_score"] != float("-inf") else float("-inf")
        min_ma = min(ev.mar_return_pct or 0.0, ev.apr_return_pct or 0.0)
        w2 = next((w for w in ev.windows if w.label == "W2"), None)
        w2_tpf = w2.test_metrics.get("profit_factor") if w2 else None
        rows.append(
            (
                wp,
                cap,
                ws,
                min_ma,
                ev.mar_return_pct or 0.0,
                ev.apr_return_pct or 0.0,
                ev.full_metrics.get("return_pct", 0.0),
                w2_tpf,
                name,
            )
        )

    def key(r):
        wp, cap, ws, min_ma, mar, apr, full, w2pf, name = r
        ws_ok = ws if ws > float("-inf") else -1e9
        return (-wp, cap, -ws_ok, -min_ma, -full, name)

    rows.sort(key=key)
    print(f"bars={len(df)} trials={len(trials)}")
    print("wins cap worst_sc min(M,A) Mar Apr full% W2_tst_PF name")
    print("-" * 95)
    for r in rows[: args.top]:
        wp, cap, ws, min_ma, mar, apr, full, w2pf, name = r
        ws_s = f"{ws:.4f}" if ws > float("-inf") else "-inf"
        w2s = f"{float(w2pf):.3f}" if w2pf is not None else "n/a"
        print(
            f"{wp}/4 {cap} {ws_s:>8} {min_ma:7.2f} {mar:6.2f} {apr:6.2f} "
            f"{full:7.1f} {w2s} {name}"
        )


if __name__ == "__main__":
    main()
