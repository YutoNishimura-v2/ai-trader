from __future__ import annotations

import pandas as pd

from scripts.iter29_adaptive_sim import _market_features, _simulate


def _expert_frame(days: pd.DatetimeIndex, expert: str, returns: list[float]) -> pd.DataFrame:
    out = pd.DataFrame(index=days)
    out.index.name = "day"
    out["pnl"] = [1000.0 * r for r in returns]
    out["trades"] = [1 if r != 0 else 0 for r in returns]
    out["wins"] = [1 if r > 0 else 0 for r in returns]
    out["losses"] = [1 if r < 0 else 0 for r in returns]
    out["ret_pct"] = returns
    out["equity_end"] = 100_000.0 + out["pnl"].cumsum()
    out["expert"] = expert
    return out


def test_recent_window_simulation_uses_prior_history_for_first_day() -> None:
    days = pd.date_range("2026-01-01", periods=8, freq="D", tz="UTC")
    experts = {
        "growth": _expert_frame(days, "growth", [1, 1, 1, -5, -5, -5, -5, -5]),
        "h4": _expert_frame(days, "h4", [0, 0, 0, 2, 2, 2, 2, 2]),
        "cash": _expert_frame(days, "cash", [0] * 8),
    }
    market = pd.DataFrame(index=days)
    market["mkt_ret_pct"] = 0.0
    market["range_pct"] = 1.0
    market["body_pct"] = 0.5
    market["range_body"] = 2.0
    market["up_day"] = 0

    result = _simulate(
        "rolling_winner",
        experts,
        market,
        100_000.0,
        policy="rolling_winner",
        lookback_days=3,
        min_score=-999.0,
        start_day=days[4],
    )

    # At the first simulated day (Jan 5), the Jan 4 growth loss is already
    # visible, so the controller selects h4 instead of blindly starting cash.
    assert result.daily.index[0] == days[4]
    assert result.daily.iloc[0]["expert"] == "h4"


def test_market_features_handles_naive_index() -> None:
    idx = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [1.0, 1.0, 1.0, 1.0],
        },
        index=idx,
    )

    features = _market_features(df)

    assert features.index.tz is not None
    assert "range_body" in features.columns
