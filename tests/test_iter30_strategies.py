"""Smoke tests for iter30 strategies."""
import numpy as np
import pandas as pd

from ai_trader.strategy.london_ny_orb import LondonNyOrb
from ai_trader.strategy.heikin_ashi_trend import HeikinAshiTrend, _heikin_ashi
from ai_trader.strategy.three_soldiers import ThreeSoldiers


def _flat_df(n=200):
    return pd.DataFrame({
        "open": [2000.0]*n, "high": [2000.5]*n,
        "low": [1999.5]*n, "close": [2000.0]*n,
        "volume": [1.0]*n,
    }, index=pd.date_range("2026-05-04T07:00:00Z", periods=n, freq="1min"))


def test_strategies_register():
    assert LondonNyOrb().name == "london_ny_orb"
    assert HeikinAshiTrend().name == "heikin_ashi_trend"
    assert ThreeSoldiers().name == "three_soldiers"


def test_no_signal_history_too_short():
    for cls in (LondonNyOrb, HeikinAshiTrend, ThreeSoldiers):
        s = cls()
        df = _flat_df(20)
        # Some strategies need prepare(); guard accordingly.
        try:
            s.prepare(df)
        except Exception:
            pass
        assert s.on_bar(df) is None


def test_heikin_ashi_helper():
    o = np.array([100.0, 101.0, 102.0, 103.0])
    h = np.array([100.5, 101.5, 102.5, 103.5])
    l = np.array([99.5, 100.5, 101.5, 102.5])
    c = np.array([100.5, 101.5, 102.5, 103.5])
    df = pd.DataFrame({"open":o, "high":h, "low":l, "close":c})
    ha_o, ha_h, ha_l, ha_c = _heikin_ashi(df)
    # First HA close = (o+h+l+c)/4 = (100+100.5+99.5+100.5)/4 = 100.125
    assert abs(ha_c[0] - 100.125) < 1e-9
    # Subsequent ha_open = (prev_ha_open + prev_ha_close)/2
    assert abs(ha_o[1] - ((100.25 + 100.125)/2)) < 1e-9


def test_orb_skip_low_volatility():
    """A flat day's range is below min_range_atr*ATR → no trade."""
    df = _flat_df(800)
    s = LondonNyOrb(open_hour=7, open_minute=0, range_minutes=15,
                    trade_window_hours=4, confirm_minutes=5,
                    min_range_atr=0.5, atr_period=14, weekdays=None)
    s.prepare(df)
    fired = False
    for i in range(s.min_history, len(df)):
        sig = s.on_bar(df.iloc[:i+1])
        if sig is not None:
            fired = True
            break
    assert not fired
