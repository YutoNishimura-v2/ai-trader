#!/usr/bin/env python3
"""Forward held-out check for Wave Y headline configs.

Splits ``data/xauusd_m1_2026.csv`` at 2026-03-01 UTC:

- **jan-feb** — research / in-sample months (Jan–Feb 2026)
- **mar-apr** — held-out priority window (Mar–Apr 2026)

Runs rolling stability harness on each slice plus the full CSV.
Use this after micro-peel grids on iter244 are exhausted.

Example::

    python3 scripts/wave_y_heldout_validate.py
    python3 scripts/wave_y_heldout_validate.py --csv data/xauusd_m1_2026.csv
    python3 scripts/wave_y_heldout_validate.py --oos-csv data/xauusd_m1_2026_oos.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import (
    _run_one,
    build_rolling_windows,
    evaluate_config,
    mar_apr_returns,
    score_config,
)

_WINDOW_ATTEMPTS: tuple[dict, ...] = (
    {},
    dict(
        n_windows=2,
        research_days=14,
        validation_days=7,
        test_days=7,
        min_research_bars=2_000,
        min_validation_bars=500,
        min_test_bars=500,
    ),
    dict(
        n_windows=2,
        research_days=10,
        validation_days=5,
        test_days=5,
        min_research_bars=1_000,
        min_validation_bars=300,
        min_test_bars=300,
    ),
)


def _windows_for_slice(df: pd.DataFrame) -> list:
    for kwargs in _WINDOW_ATTEMPTS:
        try:
            return build_rolling_windows(df, **kwargs)
        except ValueError:
            continue
    return []

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGS = [
    "config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml",
    "config/research_aspiration_200/iter235_rollwin_handoff_wave_lowrisk.yaml",
    "config/research_aspiration_200/iter244_handoff_overlap_lunchblock.yaml",
    "config/research_aspiration_200/iter227_wave_x_wave_structure_223_tp35.yaml",
]
HOLDOUT_START = pd.Timestamp("2026-03-01", tz="UTC")
MAY_START = pd.Timestamp("2026-05-01", tz="UTC")


def _slice(df: pd.DataFrame, *, before: bool | None) -> pd.DataFrame:
    idx = df.index
    if before is True:
        return df.loc[idx < HOLDOUT_START].copy()
    if before is False:
        return df.loc[idx >= HOLDOUT_START].copy()
    return df


def _run_slice(
    name: str,
    df: pd.DataFrame,
    config_paths: list[Path],
) -> None:
    windows = _windows_for_slice(df)
    print(f"\n=== slice: {name} bars={len(df)} windows={len(windows)} ===")
    print(
        "config | ret% | PF | cap | Mar% | Apr% | wpass | worst_score"
    )
    print("-" * 95)
    for path in config_paths:
        cfg = load_config(path)
        if windows:
            ev = evaluate_config(
                cfg,
                full_df=df,
                windows=windows,
                label="wave-y-heldout",
                i_know_this_is_tournament_evaluation=True,
            )
            row = score_config(ev)
            mar = ev.mar_return_pct if ev.mar_return_pct is not None else float("nan")
            apr = ev.apr_return_pct if ev.apr_return_pct is not None else float("nan")
            wpass = f"{row['windows_passing']}/{row['n_windows']}"
            worst = row["worst_score"]
            ret = ev.full_metrics.get("return_pct", 0)
            pf = ev.full_metrics.get("profit_factor", 0)
            cap = ev.full_cap_violations
        else:
            m = _run_one(df, cfg)
            mar, apr = mar_apr_returns(m)
            mar = mar if mar is not None else float("nan")
            apr = apr if apr is not None else float("nan")
            wpass = "n/a"
            worst = "n/a"
            ret = m.get("return_pct", 0)
            pf = m.get("profit_factor", 0)
            cap = int(m.get("cap_violations", 0))
        print(
            f"{path.name} | {ret:.1f} | {pf:.3f} | {cap} | "
            f"{mar:.2f} | {apr:.2f} | {wpass} | {worst}"
        )


def _may_slice(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[df.index >= MAY_START].copy()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=ROOT / "data" / "xauusd_m1_2026.csv")
    ap.add_argument(
        "--oos-csv",
        type=Path,
        default=None,
        help="Post-training OOS file (e.g. data/xauusd_m1_2026_oos.csv from fetch_dukascopy)",
    )
    ap.add_argument("configs", nargs="*", help="Extra YAML paths")
    args = ap.parse_args()

    paths = [ROOT / p for p in DEFAULT_CONFIGS]
    for raw in args.configs:
        p = Path(raw)
        paths.append(p if p.is_absolute() else ROOT / p)

    if args.csv.exists():
        df = load_ohlcv_csv(args.csv)
        print(f"dataset: {df.index.min()} → {df.index.max()} ({len(df)} bars)")
        print(f"holdout split at {HOLDOUT_START.isoformat()}")
        for name, part in (
            ("full", df),
            ("jan-feb (in-sample)", _slice(df, before=True)),
            ("mar-apr (held-out)", _slice(df, before=False)),
        ):
            _run_slice(name, part, paths)
    else:
        print(f"skip --csv: missing {args.csv}", file=sys.stderr)

    if args.oos_csv is not None:
        if not args.oos_csv.exists():
            print(f"Missing --oos-csv {args.oos_csv}", file=sys.stderr)
            sys.exit(1)
        oos = load_ohlcv_csv(args.oos_csv)
        print(f"\n=== OOS extension: {oos.index.min()} → {oos.index.max()} ({len(oos)} bars) ===")
        _run_slice("oos full (post-Apr)", oos, paths)
        may = _may_slice(oos)
        if len(may) > 0:
            _run_slice("oos may-only (true OOS month)", may, paths)
        else:
            print("(no May bars in OOS file)")


if __name__ == "__main__":
    main()
