from __future__ import annotations

import pandas as pd
import pytest

from scripts.iter29_adaptive_sim import (
    _simulate,
    _policy_metrics,
)


def _expert_frame(days: pd.DatetimeIndex, name: str, returns: list[float]) -> pd.DataFrame:
    df = pd.DataFrame(index=days)
    df.index.name = "day"
    df["pnl"] = returns
    df["trades"] = [0 if name == "cash" else 1 for _ in returns]
    df["wins"] = [1 if r > 0 and name != "cash" else 0 for r in returns]
    df["losses"] = [1 if r < 0 and name != "cash" else 0 for r in returns]
    df["ret_pct"] = returns
    df["equity_end"] = 100_000.0 * (1.0 + df["ret_pct"].cumsum() / 100.0)
    df["expert"] = name
    return df


def _market(days: pd.DatetimeIndex) -> pd.DataFrame:
    df = pd.DataFrame(index=days)
    df["mkt_ret_pct"] = [0.0, 1.0, 1.0, -1.0, 0.5, 0.2, 0.1][: len(days)]
    df["range_pct"] = 2.0
    df["body_pct"] = 0.5
    df["range_body"] = 4.0
    df["up_day"] = (df["mkt_ret_pct"] > 0).astype(int)
    df["abs_ret_3d"] = df["mkt_ret_pct"].abs().rolling(3, min_periods=1).mean()
    df["range_3d"] = df["range_pct"].rolling(3, min_periods=1).mean()
    df["trend_3d"] = df["mkt_ret_pct"].rolling(3, min_periods=1).sum().abs()
    return df


def test_rolling_winner_policy_uses_only_prior_days() -> None:
    days = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    expert_daily = {
        "a": _expert_frame(days, "a", [1.0, 1.0, -10.0, -10.0]),
        "b": _expert_frame(days, "b", [0.0, 0.0, 5.0, 5.0]),
        "cash": _expert_frame(days, "cash", [0.0, 0.0, 0.0, 0.0]),
    }

    result = _simulate(
        "rolling_winner",
        expert_daily,
        _market(days),
        100_000.0,
        policy="rolling_winner",
        lookback_days=2,
        min_score=-999.0,
    )

    assert list(result.daily["expert"]) == [
        "cash",  # no trailing evidence yet; causal policy starts flat
        "a",  # only day 1 is known
        "a",  # day 3 loss is not visible before choosing day 3
        "b",  # day 3 is now known, so b becomes best
    ]


def test_first_week_policy_observes_then_selects_current_month_state() -> None:
    days = pd.date_range("2026-01-01", periods=7, freq="D", tz="UTC")
    expert_daily = {
        "growth": _expert_frame(days, "growth", [2.0] * len(days)),
        "defensive": _expert_frame(days, "defensive", [0.5] * len(days)),
        "cash": _expert_frame(days, "cash", [0.0] * len(days)),
    }
    market = _market(days)
    market["mkt_ret_pct"] = [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0]

    result = _simulate(
        "first_week_observe",
        expert_daily,
        market,
        100_000.0,
        policy="first_week_observe",
        warmup_days=5,
    )

    assert list(result.daily["expert"][:5]) == ["cash"] * 5
    assert result.daily["expert"].iloc[5] == "growth"
    assert result.daily["expert"].iloc[6] == "growth"


def test_policy_metrics_counts_cash_days_and_switches() -> None:
    days = pd.date_range("2026-01-01", periods=2, freq="D", tz="UTC")
    daily = pd.DataFrame(index=days)
    daily["expert"] = ["cash", "growth"]
    daily["pnl"] = [0.0, 1_000.0]
    daily["equity"] = [100_000.0, 101_000.0]
    daily["ret_pct"] = [0.0, 1.0]
    daily["trades"] = [0, 2]

    metrics = _policy_metrics(daily, 100_000.0)

    assert metrics["return_pct"] == pytest.approx(1.0)
    assert metrics["active_days"] == 1
    assert metrics["cash_days"] == 1
    assert metrics["switches"] == 1

