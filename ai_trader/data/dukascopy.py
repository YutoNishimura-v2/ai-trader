"""Dukascopy historical tick / minute-bar loader (cross-platform).

Dukascopy publishes free tick-level historical data for a broad set
of symbols including XAUUSD and BTCUSD. Unlike the ``MetaTrader5``
package, this is a plain HTTPS download so it runs on any OS —
useful for Phase 2 research before we have access to HFM data.

Notes and caveats:
- Spread on Dukascopy's retail feed is materially wider than
  HFM Katana's. For strategy research this is fine (costs are re-
  modeled pessimistically by the engine). For final promotion
  decisions we want HFM's own history, but that's Phase 3.
- Month is 0-indexed in the URL (January = 00, December = 11).
  Day and hour are 0-indexed too.
- The binary format is LZMA-compressed fixed-width records:
  ``>IIIff`` = (ms_of_hour, ask_raw, bid_raw, ask_volume, bid_volume)
  where prices are divided by ``DECIMAL_FACTOR`` (1000 for gold and
  most forex majors; 100 for JPY pairs; 100000000 for BTCUSD).
- Some hours legitimately have zero ticks (weekends, very illiquid
  sessions). The downloader returns an empty tick frame for those.

Resampling to OHLCV happens here so the rest of the framework
stays timeframe-agnostic.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import lzma
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from ..utils.logging import get_logger


_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
_REC_STRUCT = struct.Struct(">IIIff")


@dataclass(frozen=True)
class InstrumentDukascopy:
    """Mapping from our instrument name to Dukascopy's URL symbol +
    the price decimal factor used to decode the binary file."""

    symbol_url: str        # Dukascopy path segment, e.g. "XAUUSD"
    decimal_factor: float  # divide raw int by this to get price


_INSTRUMENTS: dict[str, InstrumentDukascopy] = {
    "XAUUSD": InstrumentDukascopy(symbol_url="XAUUSD", decimal_factor=1000.0),
    # BTCUSD on Dukascopy uses decimal_factor=10 (confirmed empirically:
    # a Jan 2026 bar with raw ask=895406 resolves to $89,540.6, which
    # matches the actual BTCUSD price range.)
    "BTCUSD": InstrumentDukascopy(symbol_url="BTCUSD", decimal_factor=10.0),
}


def _hour_url(symbol: str, dt: datetime) -> str:
    """URL for the hourly .bi5 file covering ``dt``.

    Month is 0-indexed in Dukascopy's URL scheme.
    """
    return (
        f"{_BASE_URL}/{symbol}/"
        f"{dt.year:04d}/{(dt.month - 1):02d}/{dt.day:02d}/"
        f"{dt.hour:02d}h_ticks.bi5"
    )


def _cache_path(cache_dir: Path, symbol: str, dt: datetime) -> Path:
    return (
        cache_dir
        / symbol
        / f"{dt.year:04d}"
        / f"{dt.month:02d}"
        / f"{dt.day:02d}"
        / f"{dt.hour:02d}.bi5"
    )


def _http_get(url: str, *, retries: int = 6, timeout: float = 30.0) -> bytes:
    """Fetch with retries + exponential backoff.

    Dukascopy sometimes returns 200 with empty body (no ticks that
    hour); that's a valid empty result and is propagated to the
    caller. 404 also means "no data for this hour". 503 / timeouts
    are transient and backed off."""
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "ai-trader/0.1"})
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except HTTPError as e:
            if e.code == 404:
                return b""
            last_err = e
            time.sleep(min(60.0, 2.0 * (2**attempt)))
        except (URLError, TimeoutError) as e:
            last_err = e
            time.sleep(min(60.0, 2.0 * (2**attempt)))
    assert last_err is not None
    raise last_err


def _decode_bi5(raw: bytes, decimal_factor: float, hour_start: datetime) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["time", "ask", "bid", "ask_vol", "bid_vol"])
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        return pd.DataFrame(columns=["time", "ask", "bid", "ask_vol", "bid_vol"])
    if not data:
        return pd.DataFrame(columns=["time", "ask", "bid", "ask_vol", "bid_vol"])

    count = len(data) // _REC_STRUCT.size
    if count == 0:
        return pd.DataFrame(columns=["time", "ask", "bid", "ask_vol", "bid_vol"])

    arr = np.frombuffer(
        data[: count * _REC_STRUCT.size],
        dtype=np.dtype(">u4,>u4,>u4,>f4,>f4"),
    )
    ms = arr["f0"].astype(np.int64)
    ask = arr["f1"].astype(np.float64) / decimal_factor
    bid = arr["f2"].astype(np.float64) / decimal_factor
    av = arr["f3"].astype(np.float64)
    bv = arr["f4"].astype(np.float64)

    base_ns = int(hour_start.timestamp() * 1_000_000_000)
    times = pd.to_datetime(base_ns + ms * 1_000_000, utc=True)
    return pd.DataFrame(
        {"time": times, "ask": ask, "bid": bid, "ask_vol": av, "bid_vol": bv}
    )


def _download_hour(
    symbol: str,
    dt: datetime,
    cache_dir: Path | None,
) -> bytes:
    url = _hour_url(symbol, dt)
    if cache_dir is not None:
        cp = _cache_path(cache_dir, symbol, dt)
        if cp.exists():
            # Any cached file (including zero-byte "known empty") is
            # authoritative; never re-hit the network.
            return cp.read_bytes()
    raw = _http_get(url)
    if cache_dir is not None:
        cp = _cache_path(cache_dir, symbol, dt)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(raw)
    return raw


def _hour_range(start: datetime, end: datetime) -> Iterable[datetime]:
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur <= end:
        yield cur
        cur += timedelta(hours=1)


def fetch_ticks(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    cache_dir: Path | str | None = "data/cache/dukascopy",
    max_workers: int = 8,
) -> pd.DataFrame:
    """Download tick data for ``symbol`` in ``[start, end]``.

    Returns a DataFrame indexed by tick timestamp with columns
    (ask, bid, ask_vol, bid_vol). Hours with zero ticks contribute
    nothing.
    """
    if symbol not in _INSTRUMENTS:
        raise KeyError(f"unknown Dukascopy instrument {symbol!r}")
    spec = _INSTRUMENTS[symbol]
    log = get_logger("ai_trader.data.duka")

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    hours = list(_hour_range(start_utc, end_utc))

    cache = Path(cache_dir) if cache_dir else None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)

    log.info("downloading %s hours of %s (%s .. %s)", len(hours), symbol, start_utc, end_utc)

    def worker(hr: datetime) -> pd.DataFrame:
        raw = _download_hour(spec.symbol_url, hr, cache)
        return _decode_bi5(raw, spec.decimal_factor, hr)

    frames: list[pd.DataFrame] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, df in enumerate(ex.map(worker, hours)):
            if not df.empty:
                frames.append(df)
            if (i + 1) % 200 == 0:
                log.info("progress: %s / %s hours", i + 1, len(hours))

    if not frames:
        return pd.DataFrame(columns=["ask", "bid", "ask_vol", "bid_vol"])
    out = pd.concat(frames, ignore_index=True)
    out = out.set_index("time").sort_index()
    return out


def ticks_to_ohlcv(ticks: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """Resample tick data to OHLCV on the mid-price.

    Mid = (ask + bid) / 2. Volume is the sum of ``ask_vol + bid_vol``.
    Spread information is discarded; the engine models it separately.
    """
    if ticks.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    mid = (ticks["ask"] + ticks["bid"]) / 2.0
    vol = ticks["ask_vol"] + ticks["bid_vol"]
    ohlc = mid.resample(freq, label="left", closed="left").agg(["first", "max", "min", "last"])
    ohlc.columns = ["open", "high", "low", "close"]
    vsum = vol.resample(freq, label="left", closed="left").sum()
    out = ohlc.join(vsum.rename("volume"))
    out = out.dropna(subset=["open", "high", "low", "close"])
    out.index.name = "time"
    return out


def fetch_ohlcv(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "M5",
    *,
    cache_dir: Path | str | None = "data/cache/dukascopy",
    max_workers: int = 8,
) -> pd.DataFrame:
    """Convenience: ticks -> resampled OHLCV on a common timeframe."""
    tf_map = {
        "M1": "1min", "M5": "5min", "M15": "15min",
        "M30": "30min", "H1": "1h", "H4": "4h",
    }
    if timeframe not in tf_map:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    ticks = fetch_ticks(symbol, start, end, cache_dir=cache_dir, max_workers=max_workers)
    return ticks_to_ohlcv(ticks, freq=tf_map[timeframe])
