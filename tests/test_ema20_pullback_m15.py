"""Tests for Ema20PullbackM15 (iter29)."""
import numpy as np
import pandas as pd

from ai_trader.strategy.base import SignalSide
from ai_trader.strategy.ema20_pullback_m15 import Ema20PullbackM15, _ema


def test_strategy_registers():
    s = Ema20PullbackM15()
    assert s.name == "ema20_pullback_m15"
    assert s.params["ema_period"] == 20
    assert s.params["confirm_bars"] == 2


def test_ema_helper_matches_pandas():
    arr = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    out = _ema(arr, period=3)
    # First value seeded; subsequent values follow EMA recurrence.
    expected = pd.Series(arr).ewm(span=3, adjust=False).mean().to_numpy()
    np.testing.assert_allclose(out, expected, rtol=1e-12)


def test_no_signal_when_history_too_short():
    s = Ema20PullbackM15()
    df = pd.DataFrame({
        "open": [2000.0] * 30, "high": [2000.5] * 30,
        "low": [1999.5] * 30, "close": [2000.0] * 30,
        "volume": [1.0] * 30,
    }, index=pd.date_range("2026-05-04T07:00:00Z", periods=30, freq="1min"))
    s.prepare(df)
    assert s.on_bar(df) is None


def _build_uptrend_m1(n_m15: int = 60, slope_per_m15: float = 0.5,
                     pullback_at: int = 50) -> pd.DataFrame:
    """Build M1 OHLC where the M15-resampled close trends up by `slope_per_m15`
    per closed M15 bar, with a deliberate pullback dipping the LOW into the
    EMA at `pullback_at`. After the pullback bar, prices stay near the
    pullback close so the strategy's entry on the NEXT M1 bar is close
    to the pullback close (synthetic continuity)."""
    n_m1 = n_m15 * 15
    base = 2000.0
    rows = []
    rng = np.random.default_rng(42)
    pullback_close = None
    for k in range(n_m1):
        m15_idx = k // 15
        # Linear trend baseline.
        c_base = base + slope_per_m15 * m15_idx
        # Tiny intra-bar noise.
        noise = float(rng.normal(0.0, 0.05))
        o = c_base + noise
        c = c_base + 0.05 + noise
        h = max(o, c) + 0.10
        l = min(o, c) - 0.10
        # At the pullback M15, dip the lows of M1 bars in that M15 window
        # so the M15 LOW touches near the EMA20.
        if m15_idx == pullback_at:
            ema_est = base + slope_per_m15 * (pullback_at - 9.5)
            l = ema_est - 0.20
            o = ema_est + 0.30
            c = ema_est + 0.40  # close back above EMA
            h = max(o, c) + 0.05
            if k % 15 == 14:
                pullback_close = c
        # Next M15 bar (51): keep prices NEAR the pullback close so the
        # strategy's M1-level entry, taken on the first bar of M15=51,
        # remains realistic (not jumped up to the trend-extrapolated value).
        if m15_idx == pullback_at + 1 and pullback_close is not None:
            base_pb = pullback_close + 0.05
            o = base_pb + noise
            c = base_pb + 0.05 + noise
            h = max(o, c) + 0.05
            l = min(o, c) - 0.05
        rows.append((o, h, l, c, 1.0))
    idx = pd.date_range("2026-05-04T07:00:00Z", periods=n_m1, freq="1min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)
    return df


def test_uptrend_pullback_emits_long_signal():
    df = _build_uptrend_m1(n_m15=60, slope_per_m15=0.8, pullback_at=50)
    s = Ema20PullbackM15(
        ema_period=20, confirm_bars=2, touch_dollar=0.50,
        sl_buffer_dollar=1.0, tp_buffer_dollar=0.5,
        swing_lookback_bars=12, max_sl_dollar=8.0,
        cooldown_m15_bars=4, session=None, max_trades_per_day=10,
    )
    s.prepare(df)
    # Walk forward bar by bar; at the M15 close after the pullback,
    # we expect a BUY signal.
    fired = False
    for i in range(s.min_history, len(df)):
        sig = s.on_bar(df.iloc[: i + 1])
        if sig is not None:
            assert sig.side == SignalSide.BUY, sig
            assert sig.stop_loss < df["close"].iloc[i]
            fired = True
            break
    assert fired, "expected a long signal after the pullback"


def test_no_signal_when_no_trend():
    """Flat market → all M15 closes are not all above EMA → no signal."""
    n_m1 = 60 * 15
    base = 2000.0
    rng = np.random.default_rng(0)
    rows = []
    for k in range(n_m1):
        noise = float(rng.normal(0.0, 0.10))
        o = base + noise
        c = base - noise
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        rows.append((o, h, l, c, 1.0))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                      index=pd.date_range("2026-05-04T07:00:00Z", periods=n_m1, freq="1min"))
    s = Ema20PullbackM15(session=None, max_trades_per_day=10)
    s.prepare(df)
    fired = False
    for i in range(s.min_history, len(df)):
        sig = s.on_bar(df.iloc[: i + 1])
        if sig is not None:
            fired = True
            break
    # In a perfectly flat market, the alternation should usually NOT
    # produce a clean confirmed-trend pullback. Allow either no fire,
    # or tolerate at most one (random noise can occasionally satisfy).
    # Strict: assert no fire.
    assert not fired, "expected no signal in flat market"
