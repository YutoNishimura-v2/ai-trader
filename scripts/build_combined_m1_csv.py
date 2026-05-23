#!/usr/bin/env python3
"""Merge train + OOS M1 CSVs into one deduplicated file (gitignored).

Example::

    python3 scripts/build_combined_m1_csv.py \\
        --out data/xauusd_m1_2026_combined.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARTS = [
    ROOT / "data/xauusd_m1_2026.csv",
    ROOT / "data/xauusd_m1_2026_oos.csv",
    ROOT / "data/xauusd_m1_2026_oos_tail.csv",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "data/xauusd_m1_2026_combined.csv")
    ap.add_argument("parts", nargs="*", type=Path, help="CSV paths (default: train+oos+tail)")
    args = ap.parse_args()
    paths = args.parts or DEFAULT_PARTS
    frames = []
    for p in paths:
        if not p.exists():
            print(f"skip missing {p}")
            continue
        df = pd.read_csv(p, parse_dates=["time"], index_col="time")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        frames.append(df)
        print(f"  {p.name}: {len(df)} bars {df.index.min()} .. {df.index.max()}")
    if not frames:
        raise SystemExit("no input CSVs found")
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index_label="time")
    print(f"wrote {args.out} ({len(out)} bars) {out.index.min()} .. {out.index.max()}")


if __name__ == "__main__":
    main()
