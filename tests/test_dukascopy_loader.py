"""Dukascopy loader unit tests.

These tests run entirely offline. We build a synthetic .bi5 payload
byte-for-byte and verify decoding + resampling.
"""
from __future__ import annotations

import lzma
import struct
from datetime import datetime, timezone

import pandas as pd
import pytest

from ai_trader.data.dukascopy import (
    _decode_bi5,
    ticks_to_ohlcv,
)


def _make_bi5(records: list[tuple[int, int, int, float, float]]) -> bytes:
    body = b"".join(struct.pack(">IIIff", *r) for r in records)
    return lzma.compress(body, format=lzma.FORMAT_ALONE)


def test_decode_bi5_basic():
    hour = datetime(2024, 4, 15, 10, tzinfo=timezone.utc)
    raw = _make_bi5(
        [
            (0,      2_369_500, 2_369_100, 0.5, 0.3),
            (60_000, 2_370_100, 2_369_700, 0.5, 0.3),
            (90_000, 2_369_800, 2_369_400, 0.5, 0.3),
        ]
    )
    df = _decode_bi5(raw, decimal_factor=1000.0, hour_start=hour)
    assert len(df) == 3
    assert df.iloc[0]["ask"] == pytest.approx(2369.5)
    assert df.iloc[0]["bid"] == pytest.approx(2369.1)
    # Timestamps are relative to the hour_start.
    assert df.iloc[1]["time"] == pd.Timestamp("2024-04-15 10:01:00", tz="UTC")
    assert df.iloc[2]["time"] == pd.Timestamp("2024-04-15 10:01:30", tz="UTC")


def test_decode_bi5_empty_payload():
    df = _decode_bi5(b"", decimal_factor=1000.0, hour_start=datetime(2024, 4, 15, 10, tzinfo=timezone.utc))
    assert df.empty


def test_ticks_to_ohlcv_resamples_mid():
    ts = pd.to_datetime(
        [
            "2024-04-15 10:00:15",
            "2024-04-15 10:00:45",
            "2024-04-15 10:02:10",
            "2024-04-15 10:04:00",
            "2024-04-15 10:06:00",  # spills into next 5-min bar
        ],
        utc=True,
    )
    ticks = pd.DataFrame(
        {
            "ask": [2000.5, 2001.5, 2002.5, 2001.0, 2003.0],
            "bid": [2000.1, 2001.1, 2002.1, 2000.6, 2002.6],
            "ask_vol": [0.1, 0.1, 0.1, 0.1, 0.1],
            "bid_vol": [0.1, 0.1, 0.1, 0.1, 0.1],
        },
        index=ts,
    )
    ticks.index.name = "time"
    ohlc = ticks_to_ohlcv(ticks, freq="5min")
    assert len(ohlc) == 2
    first = ohlc.iloc[0]
    # mid of first tick = (2000.5 + 2000.1)/2 = 2000.3
    assert first["open"] == pytest.approx(2000.3)
    assert first["high"] == pytest.approx(2002.3)  # mid of 3rd tick
    assert first["low"] == pytest.approx(2000.3)
    assert first["close"] == pytest.approx(2000.8)  # mid of 4th tick
    assert first["volume"] == pytest.approx(0.8)    # 4 ticks × 0.2
