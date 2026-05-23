#!/usr/bin/env python3
"""Resolve Wave Y simulation config path from a UTC timestamp.

Calendar policy (2026 research):
  - Jan–Apr: iter244 primary (train-slice 4/4 pick)
  - May+: iter280 (first positive May OOS on Apr26–May20 slice)

Example::

    python3 scripts/wave_y_resolve_config.py
    python3 scripts/wave_y_resolve_config.py --at 2026-05-15T12:00:00Z
    python3 scripts/wave_y_resolve_config.py --at 2026-03-01 --print-path
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ROOT / "config/simulation/wave_y_iter244_primary.yaml"
MAY_POSITIVE = ROOT / "config/simulation/wave_y_iter280_may_positive.yaml"
MAY_LEAST_LOSS = ROOT / "config/simulation/wave_y_iter268_may_sleeve.yaml"

# (year, month) inclusive start → config path
_POLICY: list[tuple[tuple[int, int], Path]] = [
    ((2026, 5), MAY_POSITIVE),
    ((2026, 1), PRIMARY),
]


def resolve_config_path(at: datetime) -> Path:
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    else:
        at = at.astimezone(timezone.utc)
    key = (at.year, at.month)
    chosen = PRIMARY
    for (y, m), path in sorted(_POLICY, reverse=True):
        if (key[0], key[1]) >= (y, m):
            chosen = path
            break
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--at", type=str, default=None, help="ISO UTC timestamp (default: now)")
    ap.add_argument("--print-path", action="store_true")
    ap.add_argument("--policy", choices=["positive_may", "least_loss_may"], default="positive_may")
    args = ap.parse_args()
    at = (
        datetime.fromisoformat(args.at.replace("Z", "+00:00"))
        if args.at
        else datetime.now(timezone.utc)
    )
    global _POLICY
    if args.policy == "least_loss_may":
        _POLICY = [((2026, 5), MAY_LEAST_LOSS), ((2026, 1), PRIMARY)]
    path = resolve_config_path(at)
    if args.print_path:
        print(path)
    else:
        print(f"at={at.isoformat()} → {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
