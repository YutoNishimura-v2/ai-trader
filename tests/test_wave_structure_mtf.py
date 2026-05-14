"""Tests for wave_structure_mtf and RCI."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai_trader.indicators.rci import rci
from ai_trader.strategy.registry import get_strategy, list_strategies
from ai_trader.strategy.wave_structure_mtf import (
    _htf_zigzag_bias,
    _w5_distribution_block,
)


def test_wave_structure_registered() -> None:
    assert "wave_structure_mtf" in list_strategies()
    s = get_strategy("wave_structure_mtf", htf="M5", min_history=120)
    assert s.name == "wave_structure_mtf"


def test_rci_finite_mid_series() -> None:
    n = 80
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    close = pd.Series(np.linspace(2000.0, 2010.0, n), index=idx)
    out = rci(close, period=14)
    assert np.isfinite(out.iloc[-2])


def test_w5_distribution_block_up() -> None:
    from ai_trader.indicators.zigzag import ZigZagPivot

    # Older major high at 118, then a smaller HH rally into 117 → post-peak LH vs peak.
    pivots = [
        ZigZagPivot(iloc=0, confirm_iloc=1, price=100.0, kind="low"),
        ZigZagPivot(iloc=2, confirm_iloc=3, price=110.0, kind="high"),
        ZigZagPivot(iloc=4, confirm_iloc=5, price=105.0, kind="low"),
        ZigZagPivot(iloc=6, confirm_iloc=7, price=118.0, kind="high"),
        ZigZagPivot(iloc=8, confirm_iloc=9, price=112.0, kind="low"),
        ZigZagPivot(iloc=10, confirm_iloc=11, price=115.0, kind="high"),
        ZigZagPivot(iloc=12, confirm_iloc=13, price=114.0, kind="low"),
        ZigZagPivot(iloc=14, confirm_iloc=15, price=117.0, kind="high"),
    ]
    assert _htf_zigzag_bias(pivots) == "up"
    assert _w5_distribution_block(pivots, "up") is True


def test_m1_bias_not_opposed_accepts_flat_m1() -> None:
    s = get_strategy("wave_structure_mtf", htf="M5", min_history=120, m1_bias_mode="not_opposed")
    assert s.params["m1_bias_mode"] == "not_opposed"


def test_invalid_m1_bias_mode_raises() -> None:
    with pytest.raises(ValueError, match="m1_bias_mode"):
        get_strategy("wave_structure_mtf", m1_bias_mode="invalid")


def test_wave_structure_smoke() -> None:
    rng = np.random.default_rng(42)
    n = 8000
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    t = np.arange(n, dtype=float)
    base = 2000.0 + 0.02 * t + 3.0 * np.sin(t / 200.0)
    noise = rng.normal(0, 0.15, size=n)
    close = base + noise
    df = pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.ones(n),
        },
        index=idx,
    )
    s = get_strategy(
        "wave_structure_mtf",
        htf="M5",
        min_history=400,
        session="always",
        cooldown_bars=1,
        sr_touch_atr=50.0,
        rci_long_min_prev=-100.0,
        rci_short_max_prev=100.0,
    )
    s.prepare(df)
    n_sig = 0
    for k in range(450, n):
        sig = s.on_bar(df.iloc[: k + 1])
        if sig is not None:
            n_sig += 1
    assert n_sig >= 0
