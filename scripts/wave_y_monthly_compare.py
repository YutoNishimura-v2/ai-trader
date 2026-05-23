#!/usr/bin/env python3
"""Per-calendar-month returns for Wave Y/Z headline configs.

Uses combined M1 CSV (train + OOS). Helps validate iter280 on **May** vs iter244.

Example::

    python3 scripts/build_combined_m1_csv.py
    python3 scripts/wave_y_monthly_compare.py --csv data/xauusd_m1_2026_combined.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import _run_one

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGS = [
    "config/research_aspiration_200/iter244_handoff_overlap_lunchblock.yaml",
    "config/research_aspiration_200/iter268_wavez_cap070.yaml",
    "config/research_aspiration_200/iter274_wavez_volgate_tiered.yaml",
    "config/research_aspiration_200/iter280_wavez_chop_overlap_only.yaml",
]


def _month_slices(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    months = df.index.to_period("M").unique()
    for per in sorted(months):
        mask = df.index.to_period("M") == per
        part = df.loc[mask]
        if len(part) > 500:
            out[str(per)] = part
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=ROOT / "data/xauusd_m1_2026_combined.csv")
    ap.add_argument("configs", nargs="*", help="Extra YAML paths")
    args = ap.parse_args()
    if not args.csv.exists():
        print(
            f"Missing {args.csv}. Run: python3 scripts/build_combined_m1_csv.py",
            file=sys.stderr,
        )
        sys.exit(1)

    df = load_ohlcv_csv(args.csv)
    months = _month_slices(df)
    paths = [ROOT / p for p in DEFAULT_CONFIGS]
    for raw in args.configs:
        p = Path(raw)
        paths.append(p if p.is_absolute() else ROOT / p)

    print(f"dataset {df.index.min()} .. {df.index.max()} ({len(df)} bars)")
    print(f"months: {', '.join(months.keys())}\n")

    may_key = "2026-05" if "2026-05" in months else None
    header = ["config"] + list(months.keys()) + (["may_full"] if may_key else [])
    print(" | ".join(header))
    print("-" * min(120, len(header) * 14))

    for path in paths:
        cfg = load_config(path)
        row = [path.name]
        for lab, part in months.items():
            m = _run_one(part, cfg)
            ret = float(m.get("return_pct", 0.0))
            row.append(f"{ret:+.1f}")
        if may_key:
            m_may = _run_one(months[may_key], cfg)
            row.append(f"{float(m_may.get('return_pct', 0)):+.1f}")
        print(" | ".join(row))


if __name__ == "__main__":
    main()
