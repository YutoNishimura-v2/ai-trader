"""Daily- and monthly-shape metrics (plan v3 §A.3).

User direction 2026-04-24: "all that matters is being profitable
by the end of the month." These metrics make that the primary
scoreboard, alongside cap-violation verification.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from ai_trader.backtest.engine import BacktestEngine, BacktestResult, ClosedTradeRecord
from ai_trader.backtest.metrics import (
    _daily_realized_pnl,
    _monthly_returns,
    compute_metrics,
)


def _mk_result(trades: list[tuple[str, float]]) -> BacktestResult:
    """Build a BacktestResult from (iso_close_time, pnl) pairs."""
    records = []
    for i, (t, p) in enumerate(trades):
        close_t = datetime.fromisoformat(t.replace("Z", "+00:00"))
        records.append(
            ClosedTradeRecord(
                open_time=close_t, close_time=close_t, side="buy",
                lots=0.1, entry=2000.0, exit=2000.0 + p / 10.0,
                pnl=p, reason="tp",
            )
        )
    eq = pd.Series(
        [100_000.0 + sum(p for _, p in trades[:i + 1]) for i in range(len(trades))],
        index=pd.DatetimeIndex([r.close_time for r in records]),
        name="equity",
    )
    return BacktestResult(
        equity_curve=eq,
        trades=records,
        final_balance=100_000.0 + sum(p for _, p in trades),
        withdrawn_total=0.0,
    )


def test_daily_pnl_groups_by_utc_day():
    # Two trades day 1, one day 2, one day 3.
    result = _mk_result([
        ("2026-04-01T10:00:00Z", 1000.0),
        ("2026-04-01T15:00:00Z", -500.0),
        ("2026-04-02T09:00:00Z", 200.0),
        ("2026-04-03T12:00:00Z", -300.0),
    ])
    daily = _daily_realized_pnl(result, starting_balance=100_000.0)
    assert list(daily["pnl_account"]) == pytest.approx([500.0, 200.0, -300.0])
    # Start-of-day equity rolls forward.
    assert list(daily["start_equity"]) == pytest.approx([100_000.0, 100_500.0, 100_700.0])
    # Percentages match.
    assert daily["pct"].iloc[0] == pytest.approx(500.0 / 100_000.0 * 100)
    assert daily["pct"].iloc[1] == pytest.approx(200.0 / 100_500.0 * 100)


def test_monthly_returns_compound_within_month():
    result = _mk_result([
        ("2026-04-01T10:00:00Z", 10_000.0),   # +10%
        ("2026-04-02T10:00:00Z", 11_000.0),   # +10% on 110k
        ("2026-05-01T10:00:00Z", -12_100.0),  # -10% on 121k
    ])
    monthly = _monthly_returns(result, starting_balance=100_000.0)
    assert len(monthly) == 2
    # April: (1.1)(1.1) - 1 = 0.21 → 21%
    assert monthly.iloc[0] == pytest.approx(21.0)
    # May: -10%
    assert monthly.iloc[1] == pytest.approx(-10.0)


def test_compute_metrics_includes_daily_and_monthly_fields():
    result = _mk_result([
        ("2026-04-01T10:00:00Z", 5_000.0),
        ("2026-04-02T10:00:00Z", -1_000.0),
    ])
    m = compute_metrics(result, starting_balance=100_000.0)
    for key in (
        "best_day_pct", "worst_day_pct", "trading_days",
        "daily_target_hits", "daily_max_loss_hits", "cap_violations",
        "monthly_pct_mean", "monthly_pct_max", "months_count",
    ):
        assert key in m
    assert m["trading_days"] == 2
    assert m["best_day_pct"] == pytest.approx(5.0, abs=1e-6)
    assert m["worst_day_pct"] < 0
    assert m["daily_target_hits"] == 0    # neither day hit 30%
    assert m["daily_max_loss_hits"] == 0  # neither day hit -10%
    assert m["min_equity_pct"] == pytest.approx(104.0)
    assert m["max_equity_pct"] == pytest.approx(105.0)
    assert m["ruin_flag"] is False
    assert "monthly_returns" in m
    assert m["monthly_returns"]["2026-04"] == pytest.approx(4.0)


def test_no_cap_violations_on_normal_trace():
    """Cap violation means the kill-switch failed. On a sane backtest
    where losses stay above -10.5% per day, cap_violations must be 0."""
    result = _mk_result([
        ("2026-04-01T10:00:00Z", -9_000.0),   # -9% — under the cap
        ("2026-04-02T10:00:00Z", 2_000.0),
    ])
    m = compute_metrics(result, starting_balance=100_000.0)
    assert m["cap_violations"] == 0
    assert m["daily_max_loss_hits"] == 0  # -9% is inside the cap


def test_hitting_the_cap_is_counted():
    result = _mk_result([
        ("2026-04-01T10:00:00Z", -10_500.0),  # -10.5%
    ])
    m = compute_metrics(result, starting_balance=100_000.0)
    assert m["daily_max_loss_hits"] == 1


def test_recent_return_and_ruin_metrics():
    result = _mk_result([
        ("2026-04-01T10:00:00Z", 10_000.0),
        ("2026-04-20T10:00:00Z", -85_000.0),
        ("2026-04-25T10:00:00Z", 5_000.0),
    ])
    m = compute_metrics(result, starting_balance=100_000.0)
    assert m["min_equity_pct"] == pytest.approx(25.0)
    assert m["ruin_flag"] is True
    assert m["recent_14d_return_pct"] == pytest.approx(-80_000.0 / 110_000.0 * 100.0)
    assert m["recent_30d_return_pct"] == pytest.approx(-70.0)
