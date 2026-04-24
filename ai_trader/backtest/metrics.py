"""Metrics derived from a ``BacktestResult``."""
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


def compute_metrics(result: BacktestResult, starting_balance: float) -> dict[str, Any]:
    trades = result.trades
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    net_profit = result.final_balance - starting_balance + result.withdrawn_total

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
    }
    return metrics
