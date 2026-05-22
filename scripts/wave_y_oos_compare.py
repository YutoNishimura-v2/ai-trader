#!/usr/bin/env python3
"""Quick compare on post-training OOS CSV (short slice friendly).

Example::

    python3 scripts/wave_y_oos_compare.py --csv data/xauusd_m1_2026_oos.csv
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "wave_y_heldout_validate",
    ROOT / "scripts" / "wave_y_heldout_validate.py",
)
_wh = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_wh)

from ai_trader.data.csv_loader import load_ohlcv_csv

DEFAULT = [
    "config/simulation/wave_y_iter244_primary.yaml",
    "config/simulation/wave_y_iter235_tail.yaml",
    "config/simulation/wave_y_iter227_april_sleeve.yaml",
    "config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("configs", nargs="*", help="Extra YAML paths")
    args = ap.parse_args()
    if not args.csv.exists():
        print(f"Missing {args.csv}", file=sys.stderr)
        sys.exit(1)

    paths = [ROOT / p for p in DEFAULT]
    for raw in args.configs:
        p = Path(raw)
        paths.append(p if p.is_absolute() else ROOT / p)

    df = load_ohlcv_csv(args.csv)
    print(f"OOS dataset: {df.index.min()} → {df.index.max()} ({len(df)} bars)")
    _wh._run_slice("oos", df, paths)
    may = _wh._may_slice(df)
    if len(may) > 0:
        _wh._run_slice("may-only", may, paths)


if __name__ == "__main__":
    main()
