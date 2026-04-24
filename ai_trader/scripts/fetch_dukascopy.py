"""Fetch historical OHLCV from Dukascopy and write CSV.

Cross-platform (no MT5 / no Windows dependency). Used as a Phase 2
stand-in while HFM live data is not available.

    python -m ai_trader.scripts.fetch_dukascopy \\
        --symbol XAUUSD --timeframe M5 \\
        --start 2023-01-01 --end 2025-01-01 \\
        --out data/xauusd_m5_dukascopy.csv
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ..data.dukascopy import fetch_ohlcv
from ..utils.logging import get_logger


def _parse_dt(s: str) -> datetime:
    # Accept YYYY-MM-DD or full ISO.
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromisoformat(s).astimezone(timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD", choices=["XAUUSD", "BTCUSD"])
    ap.add_argument("--timeframe", default="M5",
                    choices=["M1", "M5", "M15", "M30", "H1", "H4"])
    ap.add_argument("--start", required=True, type=_parse_dt)
    ap.add_argument("--end", required=True, type=_parse_dt)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cache", default="data/cache/dukascopy", type=Path)
    ap.add_argument("--workers", default=8, type=int)
    args = ap.parse_args()

    log = get_logger("ai_trader.fetch")
    df = fetch_ohlcv(
        args.symbol, args.start, args.end,
        timeframe=args.timeframe,
        cache_dir=args.cache,
        max_workers=args.workers,
    )
    if df.empty:
        log.error("no data in the requested range")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=True)
    log.info("wrote %s bars to %s (%s .. %s)",
             len(df), args.out, df.index[0], df.index[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
