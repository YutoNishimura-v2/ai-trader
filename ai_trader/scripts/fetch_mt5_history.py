"""Fetch OHLCV history from a running MT5 terminal.

Windows-only. Run this on the machine that has MetaTrader 5 and the
`MetaTrader5` Python package installed.

    python -m ai_trader.scripts.fetch_mt5_history \\
        --symbol XAUUSD --timeframe M5 --months 12 \\
        --out data/xauusd_m5.csv
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="M5",
                    choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    try:
        import MetaTrader5 as mt5  # type: ignore
        import pandas as pd
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "MetaTrader5 package is required. This script must run on "
            "Windows with the MT5 terminal installed."
        ) from exc

    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")

    try:
        utc_to = datetime.now(timezone.utc)
        utc_from = utc_to - timedelta(days=30 * args.months)
        rates = mt5.copy_rates_range(args.symbol, tf_map[args.timeframe], utc_from, utc_to)
        if rates is None or len(rates) == 0:
            raise SystemExit(f"no data for {args.symbol} {args.timeframe}")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        df = df[["time", "open", "high", "low", "close", "volume"]]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)
        print(f"wrote {len(df)} bars to {args.out}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
