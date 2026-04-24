"""Load OHLCV CSV produced by `scripts/fetch_mt5_history.py`."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_ohlcv_csv(path: str | Path) -> pd.DataFrame:
    """Expected columns: time, open, high, low, close, volume.

    `time` must be parseable as UTC.
    """
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError(f"{path}: missing 'time' column")

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")

    if "volume" not in df.columns:
        df["volume"] = 0.0

    return df[["open", "high", "low", "close", "volume"]].astype(float)
