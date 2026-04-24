"""Metrics derived from a ``BacktestResult``.

Includes daily-P&L summaries because plan v3 (per 2026-04-24 user
direction) is explicit: "all that matters is being profitable by
the end of the month; a +30 % day lets you lose for two days
after." The daily kill-switch enforces the −10 % floor. So the
metrics we report must include:

- realized daily P&L distribution (min, max, mean)
- how often the +30 % target actually fires
- how often the −10 % cap actually fires
- monthly returns (the real scoreboard)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .engine import BacktestResult


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    return float(dd.min())


def _daily_sharpe(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return 0.0
    return float(daily.mean() / daily.std(ddof=1) * np.sqrt(365))


def _daily_realized_pnl(result: BacktestResult, starting_balance: float) -> pd.DataFrame:
    """Return a DataFrame with per-UTC-day columns:

    - ``pnl_account``: realized P&L for the day (account currency)
    - ``start_equity``: equity at start of day (balance before any
      of the day's trades)
    - ``pct``: pnl_account / start_equity * 100

    Uses ClosedTradeRecord.close_time + pnl from the result.
    """
    if not result.trades:
        return pd.DataFrame(columns=["pnl_account", "start_equity", "pct"])
    tr = pd.DataFrame(
        [{"close_time": t.close_time, "pnl": t.pnl} for t in result.trades]
    )
    tr["close_time"] = pd.to_datetime(tr["close_time"], utc=True)
    tr["day"] = tr["close_time"].dt.floor("1D")
    daily = tr.groupby("day")["pnl"].sum().rename("pnl_account").to_frame()
    # Compute start-of-day equity as a running sum, minus the day's
    # own realized P&L. Starting balance is the first baseline.
    daily = daily.sort_index()
    cumulative_prior = daily["pnl_account"].cumsum().shift(1).fillna(0.0)
    daily["start_equity"] = starting_balance + cumulative_prior
    daily["pct"] = 100.0 * daily["pnl_account"] / daily["start_equity"].replace(0, np.nan)
    return daily


def _monthly_returns(result: BacktestResult, starting_balance: float) -> pd.Series:
    """Compounded realized-only monthly returns (%)."""
    daily = _daily_realized_pnl(result, starting_balance)
    if daily.empty:
        return pd.Series(dtype=float, name="monthly_pct")
    # Compound through the month by rolling start-equity forward.
    daily["growth"] = 1.0 + daily["pnl_account"] / daily["start_equity"]
    # tz-naive period to avoid a pandas UserWarning; semantics
    # unchanged because we only use the period as a groupby key.
    idx_naive = daily.index.tz_convert("UTC").tz_localize(None) if daily.index.tz is not None else daily.index
    daily["month"] = idx_naive.to_period("M")
    monthly = daily.groupby("month")["growth"].prod().apply(lambda g: (g - 1) * 100.0)
    monthly.name = "monthly_pct"
    return monthly


def compute_metrics(result: BacktestResult, starting_balance: float) -> dict[str, Any]:
    trades = result.trades
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    net_profit = result.final_balance - starting_balance + result.withdrawn_total

    daily = _daily_realized_pnl(result, starting_balance)
    monthly = _monthly_returns(result, starting_balance)

    daily_hit_target = int((daily["pct"] >= 30.0).sum()) if not daily.empty else 0
    daily_hit_max_loss = int((daily["pct"] <= -10.0).sum()) if not daily.empty else 0
    # Cap-violation days: more severe than the kill-switch threshold.
    # If this is non-zero the kill-switch failed at its job.
    cap_violations = int((daily["pct"] < -10.5).sum()) if not daily.empty else 0

    metrics = {
        "trades": int(len(trades)),
        "wins": int(wins.size),
        "losses": int(losses.size),
        "win_rate": float(wins.size / len(trades)) if trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "net_profit": float(net_profit),
        "final_balance": float(result.final_balance),
        "withdrawn_total": float(result.withdrawn_total),
        "return_pct": float(net_profit / starting_balance * 100.0) if starting_balance else 0.0,
        "expectancy": float(pnls.mean()) if pnls.size else 0.0,
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "max_drawdown_pct": float(_max_drawdown(result.equity_curve) * 100.0),
        "sharpe_daily": _daily_sharpe(result.equity_curve),
        # --- daily-shape metrics (plan v3 §A.3) ---
        "trading_days": int(len(daily)),
        "best_day_pct": float(daily["pct"].max()) if not daily.empty else 0.0,
        "worst_day_pct": float(daily["pct"].min()) if not daily.empty else 0.0,
        "mean_day_pct": float(daily["pct"].mean()) if not daily.empty else 0.0,
        "median_day_pct": float(daily["pct"].median()) if not daily.empty else 0.0,
        "up_day_count": int((daily["pnl_account"] > 0).sum()) if not daily.empty else 0,
        "down_day_count": int((daily["pnl_account"] < 0).sum()) if not daily.empty else 0,
        "daily_target_hits": daily_hit_target,
        "daily_max_loss_hits": daily_hit_max_loss,
        "cap_violations": cap_violations,
        # --- monthly scoreboard ---
        "monthly_pct_median": float(monthly.median()) if len(monthly) else 0.0,
        "monthly_pct_mean": float(monthly.mean()) if len(monthly) else 0.0,
        "monthly_pct_min": float(monthly.min()) if len(monthly) else 0.0,
        "monthly_pct_max": float(monthly.max()) if len(monthly) else 0.0,
        "months_profitable": int((monthly > 0).sum()) if len(monthly) else 0,
        "months_count": int(len(monthly)),
    }
    return metrics
