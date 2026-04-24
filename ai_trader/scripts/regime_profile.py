"""Per-month regime profile for a CSV OHLCV series.

User direction (2026-04-24): the market became "extremely difficult"
since March. This script quantifies the regime shift so claims about
regime change stop being narrative and start being data.

Per month we report:

- ``ret_pct``: first-open to last-close % change (direction + magnitude)
- ``up_day_pct``: share of days closing higher than their open
- ``realized_vol``: annualised std of daily log returns
- ``adx14_median``: median 14-period ADX on daily aggregation
  (trend strength; > 25 is classically "trending")
- ``range_to_body``: median (daily range / |close - open|); high
  means lots of indecision intraday

These are plain diagnostics; the bot doesn't use them. They exist
so we can look at a single table and answer "was month X trending
or ranging, and how hard?"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.csv_loader import load_ohlcv_csv


def _adx(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    high = daily["high"]; low = daily["low"]; close = daily["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr_ = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=daily.index).ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=daily.index).ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def profile(df: pd.DataFrame) -> pd.DataFrame:
    daily = df.resample("1D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last"
    }).dropna()
    daily["body"] = (daily["close"] - daily["open"]).abs()
    daily["range"] = daily["high"] - daily["low"]
    daily["up_day"] = (daily["close"] > daily["open"]).astype(int)
    daily["log_ret"] = np.log(daily["close"] / daily["close"].shift(1))
    daily["adx14"] = _adx(daily, period=14)
    daily["range_to_body"] = daily["range"] / daily["body"].replace(0, np.nan)

    month = daily.index.to_period("M")
    rows = []
    for m, g in daily.groupby(month):
        if len(g) < 5:
            continue
        ret_pct = 100.0 * (g["close"].iloc[-1] / g["open"].iloc[0] - 1.0)
        rv = g["log_ret"].std(ddof=1) * np.sqrt(252) * 100.0
        rows.append({
            "month": str(m),
            "days": len(g),
            "ret_pct": float(ret_pct),
            "up_day_pct": float(100.0 * g["up_day"].mean()),
            "realized_vol_pct": float(rv),
            "adx14_median": float(g["adx14"].median()),
            "range_to_body_median": float(g["range_to_body"].median()),
        })
    return pd.DataFrame(rows).set_index("month")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None, help="optional markdown table out")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    prof = profile(df)
    # Print with wider float format.
    with pd.option_context(
        "display.float_format", lambda x: f"{x:+.2f}" if abs(x) >= 0.01 else f"{x:.2f}",
        "display.max_rows", None,
    ):
        print(prof.to_string())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        lines = ["| month | days | ret% | up-day% | realized vol% | adx14 med | range/body med |",
                 "|---|---|---|---|---|---|---|"]
        for m, row in prof.iterrows():
            lines.append(
                f"| {m} | {int(row['days'])} | {row['ret_pct']:+.2f} | "
                f"{row['up_day_pct']:.1f} | {row['realized_vol_pct']:.1f} | "
                f"{row['adx14_median']:.1f} | {row['range_to_body_median']:.2f} |"
            )
        args.out.write_text("\n".join(lines) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
