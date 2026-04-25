"""ZigZag indicator: alternating pivots, threshold filter, causal
confirmation."""
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from ai_trader.indicators.zigzag import ZigZagPivot, ZigZagSeries, compute_zigzag


def _df(close: list[float], spread: float = 0.5) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    arr = np.array(close, dtype=float)
    df = pd.DataFrame(
        {
            "open": arr,
            "close": arr,
            "high": arr + spread / 2,
            "low": arr - spread / 2,
            "volume": 1.0,
        },
        index=idx,
    )
    return df


def test_zigzag_alternates_high_low():
    """Construct a clean zig-zag pattern and verify pivots
    alternate kind."""
    # 50 bars to warm ATR, then a bounded zig-zag with steps of ~5
    base = [100.0] * 50
    pattern = (
        list(np.linspace(100, 110, 20))   # up to 110
        + list(np.linspace(110, 102, 20))  # down to 102
        + list(np.linspace(102, 115, 20))  # up to 115
        + list(np.linspace(115, 100, 20))  # down to 100
        + list(np.linspace(100, 120, 20))  # up to 120
    )
    df = _df(base + pattern, spread=0.5)
    pivots = compute_zigzag(df, threshold_atr=2.0, atr_period=14)
    kinds = [p.kind for p in pivots]
    # Must strictly alternate.
    for a, b in zip(kinds, kinds[1:]):
        assert a != b, f"pivots not alternating: {kinds}"
    assert len(pivots) >= 3


def test_zigzag_confirm_iloc_strictly_after_pivot():
    df = _df([100.0] * 50 + list(np.linspace(100, 120, 30)) + list(np.linspace(120, 105, 30)), 0.5)
    pivots = compute_zigzag(df, threshold_atr=1.5, atr_period=14)
    for p in pivots:
        assert p.confirm_iloc > p.iloc, (
            f"pivot at iloc {p.iloc} confirmed at {p.confirm_iloc} (must be later)"
        )


def test_zigzag_threshold_filters_noise():
    """Pure noise within < threshold should produce no pivots."""
    rng = np.random.default_rng(0)
    n = 500
    # Tiny noise around 100 with std 0.05; ATR will be ~0.5; with
    # threshold_atr=10 → 5.0 threshold, far above the noise range.
    closes = (100 + rng.normal(0, 0.05, n)).tolist()
    df = _df(closes, spread=0.1)
    pivots = compute_zigzag(df, threshold_atr=10.0, atr_period=14)
    assert pivots == []


def test_zigzag_series_tail_uses_confirm_cutoff():
    """ZigZagSeries.confirmed_up_to(n) must only return pivots whose
    confirm_iloc < n. A pivot whose extreme bar is < n but whose
    confirmation bar is >= n must NOT appear (no lookahead)."""
    df = _df([100.0] * 50 + list(np.linspace(100, 120, 30)) + list(np.linspace(120, 100, 30)), 0.5)
    z = ZigZagSeries(df, threshold_atr=1.5, atr_period=14)
    # Full pivots:
    all_pivots = z.all
    if not all_pivots:
        pytest.skip("no pivots produced; pattern too small for current ATR")
    p0 = all_pivots[0]
    # Asking up to p0.iloc + 1 should NOT see p0 (its confirm_iloc > iloc).
    early = z.confirmed_up_to(p0.iloc + 1)
    assert p0 not in early
    # Asking up to p0.confirm_iloc + 1 should see p0.
    later = z.confirmed_up_to(p0.confirm_iloc + 1)
    assert p0 in later
