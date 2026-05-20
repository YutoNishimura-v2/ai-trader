#!/usr/bin/env python3
"""Compare a few YAML configs on Mar/Apr and the rolling stability battery.

Example::

    python3 scripts/iter32_compare_configs.py \\
        --csv data/xauusd_m1_2026.csv \\
        config/iter29/v4_plus_h4_protector_conc1.yaml \\
        config/iter30/adaptive_v55_v43b_dml5.yaml \\
        config/iter31/adaptive_v55_expectancy_sizing_b.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import (
    build_rolling_windows,
    evaluate_config,
    score_config,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True, help="OHLCV CSV path")
    ap.add_argument(
        "configs",
        nargs="+",
        help="YAML config paths",
    )
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    windows = build_rolling_windows(df)

    print(f"bars={len(df)} windows={len(windows)}")
    print(
        "config | full_ret% | PF | cap | Mar% | Apr% | wpass | worst_score"
    )
    print("-" * 100)
    for path in args.configs:
        cfg = load_config(path)
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=windows,
            label="iter32-compare",
            i_know_this_is_tournament_evaluation=True,
        )
        row = score_config(ev)
        print(
            f"{path} | {ev.full_metrics.get('return_pct', 0):.1f} | "
            f"{ev.full_metrics.get('profit_factor', 0):.3f} | "
            f"{ev.full_cap_violations} | "
            f"{(ev.mar_return_pct if ev.mar_return_pct is not None else float('nan')):.2f} | "
            f"{(ev.apr_return_pct if ev.apr_return_pct is not None else float('nan')):.2f} | "
            f"{row['windows_passing']}/{row['n_windows']} | "
            f"{row['worst_score']}"
        )


if __name__ == "__main__":
    main()
