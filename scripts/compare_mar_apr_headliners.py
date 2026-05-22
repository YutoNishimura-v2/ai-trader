#!/usr/bin/env python3
"""Compare Mar/Apr headline configs from HANDOFF (May 2026).

Requires ``data/xauusd_m1_2026.csv`` (see README fetch_dukascopy example).

Example::

    python3 scripts/compare_mar_apr_headliners.py
    python3 scripts/compare_mar_apr_headliners.py --csv data/xauusd_m1_2026.csv

Post-Apr OOS (short slice)::

    python3 scripts/wave_y_oos_compare.py --csv data/xauusd_m1_2026_oos.csv
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "data" / "xauusd_m1_2026.csv"
HEADLINERS = [
    "config/research_aspiration_200/adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml",
    "config/research_aspiration_200/iter235_rollwin_handoff_wave_lowrisk.yaml",
    "config/research_aspiration_200/iter244_handoff_overlap_lunchblock.yaml",
    "config/research_aspiration_200/iter227_wave_x_wave_structure_223_tp35.yaml",
    "config/research_aspiration_200/iter202_wave_w_zigzag195_tp22.yaml",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument(
        "extra_configs",
        nargs="*",
        help="Additional YAML paths (e.g. new iter231 probe)",
    )
    args = ap.parse_args()
    if not args.csv.exists():
        print(
            f"Missing {args.csv}. Fetch with:\n"
            "  python3 -m ai_trader.scripts.fetch_dukascopy \\\n"
            "    --symbol XAUUSD --timeframe M1 \\\n"
            "    --start 2026-01-01 --end 2026-04-24 \\\n"
            "    --out data/xauusd_m1_2026.csv",
            file=sys.stderr,
        )
        sys.exit(1)
    paths = [str(ROOT / p) for p in HEADLINERS]
    for raw in args.extra_configs:
        p = Path(raw)
        paths.append(str(p if p.is_absolute() else ROOT / p))
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "iter32_compare_configs.py"),
        "--csv",
        str(args.csv),
        *paths,
    ]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
