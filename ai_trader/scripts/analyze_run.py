"""Print high-risk research diagnostics for a backtest run artifact.

The normal CLIs already print raw JSON metrics. This helper turns the
new ruin-control fields into a compact review table so aggressive
GOLD-only research can be compared quickly:

    python -m ai_trader.scripts.analyze_run artifacts/runs/....json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(v: Any) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact", type=Path, help="JSON produced by run_backtest/evaluate_tournament")
    args = ap.parse_args()

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", payload)
    print(f"artifact: {args.artifact}")
    print(f"strategy: {payload.get('strategy', 'n/a')}")
    print(f"config:   {payload.get('config', 'n/a')}")
    print()
    print("== Return / edge ==")
    print(f"return:        {_fmt_pct(metrics.get('return_pct'))}")
    print(f"monthly mean:  {_fmt_pct(metrics.get('monthly_pct_mean'))}")
    print(f"March:         {_fmt_pct(metrics.get('march_return_pct'))}")
    print(f"April:         {_fmt_pct(metrics.get('april_return_pct'))}")
    print(f"recent 14d:    {_fmt_pct(metrics.get('recent_14d_return_pct'))}")
    print(f"recent 30d:    {_fmt_pct(metrics.get('recent_30d_return_pct'))}")
    print(f"profit factor: {metrics.get('profit_factor', 'n/a')}")
    print(f"trades:        {metrics.get('trades', 'n/a')}")
    print(f"win rate:      {_fmt_pct(100.0 * float(metrics.get('win_rate', 0.0)))}")
    print()
    print("== Ruin / drawdown guardrail ==")
    print(f"max DD:        {_fmt_pct(metrics.get('max_drawdown_pct'))}")
    print(f"min equity:    {_fmt_num(metrics.get('min_equity'))} ({_fmt_pct(metrics.get('min_equity_pct'))})")
    print(f"max equity:    {_fmt_num(metrics.get('max_equity'))} ({_fmt_pct(metrics.get('max_equity_pct'))})")
    print(f"ruin flag:     {metrics.get('ruin_flag', 'n/a')}")
    print(f"worst day:     {_fmt_pct(metrics.get('worst_day_pct'))}")
    print(f"best day:      {_fmt_pct(metrics.get('best_day_pct'))}")
    print(f"target hits:   {metrics.get('daily_target_hits', 'n/a')}")
    print(f"loss hits:     {metrics.get('daily_max_loss_hits', 'n/a')}")
    print(f"cap violates:  {metrics.get('cap_violations', 'n/a')}")
    monthly = metrics.get("monthly_returns") or {}
    if monthly:
        print()
        print("== Monthly returns ==")
        for month, ret in sorted(monthly.items()):
            print(f"{month}: {_fmt_pct(ret)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
