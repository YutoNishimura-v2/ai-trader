"""Iter29 adaptive-policy simulator.

This script tests the new thesis from the plan: instead of forcing one
static strategy to work in every market state, simulate a live-demo style
"review loop" that can switch among several strategy experts, downshift to
cash, or wait for regime confirmation.

Important: policies are causal. At day/week/month decision boundaries they
only see expert day returns and market features from dates that have already
closed. The optional oracle report is explicitly impossible hindsight and is
printed only as an upper-bound diagnostic.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.backtest.sweep import risk_kwargs_from_config
from ai_trader.broker.paper import PaperBroker
from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy


@dataclass(frozen=True)
class Expert:
    name: str
    config: Path | None


@dataclass(frozen=True)
class PolicyResult:
    name: str
    metrics: dict[str, Any]
    daily: pd.DataFrame
    decisions: pd.DataFrame


DEFAULT_EXPERTS = (
    "growth=config/iter28/v4_ext_a_dow_no_fri.yaml",
    "h4=config/iter28/_phaseA/4h_london.yaml",
    "defensive=config/iter9/ensemble_priceaction_v4_router.yaml",
    "cash=CASH",
)


def _instrument(cfg: dict[str, Any]) -> InstrumentSpec:
    inst_cfg = cfg["instrument"]
    return InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )


def _build(cfg: dict[str, Any]):
    instrument = _instrument(cfg)
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        **risk_kwargs_from_config(cfg["risk"]),
    )
    exec_cfg = cfg["execution"]
    broker = PaperBroker(
        instrument=instrument,
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )
    strat = get_strategy(cfg["strategy"]["name"], **cfg["strategy"].get("params", {}))
    return strat, risk, broker, float(cfg["account"]["starting_balance"])


def _run_config(df: pd.DataFrame, cfg: dict[str, Any]):
    strat, risk, broker, sb = _build(cfg)
    fills: list[dict[str, Any]] = []

    def log(msg: str) -> None:
        if "opened pos=" in msg:
            # BacktestEngine emits a compact fill log. Parse defensively;
            # if the format changes, policy metrics still work.
            m = re.search(
                r"opened pos=(?P<pos>\d+) .* group=(?P<group>\d+) "
                r"leg=(?P<leg>\d+) lots=(?P<lots>[0-9.]+) "
                r"comment='(?P<comment>.*)'$",
                msg,
            )
            if m:
                row = m.groupdict()
                row["lots"] = float(row["lots"])
                fills.append(row)

    res = BacktestEngine(strategy=strat, risk=risk, broker=broker, log=log).run(df)
    metrics = compute_metrics(res, starting_balance=sb)
    return res, metrics, fills, sb


def _daily_from_result(
    result,
    *,
    starting_balance: float,
    expert_name: str,
    fills: list[dict[str, Any]],
) -> pd.DataFrame:
    if result.equity_curve.empty:
        return pd.DataFrame()
    days = result.equity_curve.index.tz_convert("UTC").floor("1D").unique()
    out = pd.DataFrame(index=pd.DatetimeIndex(days, tz="UTC"))
    out.index.name = "day"

    tr = pd.DataFrame(
        [
            {
                "close_time": t.close_time,
                "pnl": float(t.pnl),
                "reason": t.reason,
                "group_id": t.group_id,
                "leg_index": t.leg_index,
            }
            for t in result.trades
        ]
    )
    if not tr.empty:
        tr["close_time"] = pd.to_datetime(tr["close_time"], utc=True)
        tr["day"] = tr["close_time"].dt.floor("1D")
        pnl = tr.groupby("day")["pnl"].sum()
        trades = tr.groupby("day").size()
        wins = tr[tr["pnl"] > 0].groupby("day").size()
        losses = tr[tr["pnl"] < 0].groupby("day").size()
    else:
        pnl = pd.Series(dtype=float)
        trades = pd.Series(dtype=int)
        wins = pd.Series(dtype=int)
        losses = pd.Series(dtype=int)

    out["pnl"] = pnl.reindex(out.index, fill_value=0.0)
    out["trades"] = trades.reindex(out.index, fill_value=0).astype(int)
    out["wins"] = wins.reindex(out.index, fill_value=0).astype(int)
    out["losses"] = losses.reindex(out.index, fill_value=0).astype(int)

    # Daily return is measured on the static expert's running account value.
    start_equity = starting_balance + out["pnl"].cumsum().shift(1).fillna(0.0)
    out["ret_pct"] = 100.0 * out["pnl"] / start_equity.replace(0, np.nan)
    out["equity_end"] = starting_balance + out["pnl"].cumsum()
    out["expert"] = expert_name

    fill_df = pd.DataFrame(fills)
    if not fill_df.empty and "comment" in fill_df:
        fill_df["member"] = fill_df["comment"].map(_member_from_comment)
        # Approximate attribution by close day is not available in fill logs,
        # but the expert/member columns are still useful in raw JSON output.
    return out


def _member_from_comment(comment: str) -> str:
    if comment.startswith("[") and "]" in comment:
        return comment[1:comment.index("]")]
    return comment.split()[0] if comment else "unknown"


def _market_features(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
        df = df.copy()
        df.index = idx
    g = df.groupby(df.index.floor("1D"))
    day = g.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    prev_close = day["close"].shift(1)
    day["mkt_ret_pct"] = (day["close"] / prev_close - 1.0) * 100.0
    day["range_pct"] = (day["high"] - day["low"]) / day["open"] * 100.0
    day["body_pct"] = (day["close"] - day["open"]).abs() / day["open"] * 100.0
    day["range_body"] = day["range_pct"] / day["body_pct"].replace(0, np.nan)
    day["up_day"] = (day["close"] > day["open"]).astype(int)
    day["abs_ret_3d"] = day["mkt_ret_pct"].abs().rolling(3, min_periods=1).mean()
    day["range_3d"] = day["range_pct"].rolling(3, min_periods=1).mean()
    day["trend_3d"] = day["mkt_ret_pct"].rolling(3, min_periods=1).sum().abs()
    return day


def parse_expert(s: str) -> Expert:
    if "=" not in s:
        raise ValueError(f"expert must be name=path or name=CASH: {s!r}")
    name, path = s.split("=", 1)
    name = name.strip()
    path = path.strip()
    if not name:
        raise ValueError(f"empty expert name in {s!r}")
    if path.upper() == "CASH":
        return Expert(name=name, config=None)
    return Expert(name=name, config=Path(path))


def load_experts(df: pd.DataFrame, experts: list[Expert]) -> tuple[dict[str, pd.DataFrame], dict[str, dict], float]:
    daily: dict[str, pd.DataFrame] = {}
    metrics: dict[str, dict] = {}
    starting_balance = 100_000.0
    for e in experts:
        if e.config is None:
            continue
        cfg = load_config(e.config)
        res, met, fills, sb = _run_config(df, cfg)
        starting_balance = sb
        daily[e.name] = _daily_from_result(res, starting_balance=sb, expert_name=e.name, fills=fills)
        metrics[e.name] = met
    if "cash" not in {e.name for e in experts}:
        experts.append(Expert("cash", None))
    if daily:
        all_days = next(iter(daily.values())).index
    else:
        all_days = pd.DatetimeIndex(df.index.tz_convert("UTC").floor("1D").unique(), tz="UTC")
    for e in experts:
        if e.config is None:
            d = pd.DataFrame(index=all_days)
            d.index.name = "day"
            d["pnl"] = 0.0
            d["trades"] = 0
            d["wins"] = 0
            d["losses"] = 0
            d["ret_pct"] = 0.0
            d["equity_end"] = starting_balance
            d["expert"] = e.name
            daily[e.name] = d
            metrics[e.name] = {
                "return_pct": 0.0,
                "trades": 0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "cap_violations": 0,
            }
    return daily, metrics, starting_balance


def _score(trailing: pd.DataFrame, *, mode: str = "risk_adjusted") -> float:
    if trailing.empty:
        return -1e9
    pnl = float(trailing["pnl"].sum())
    trades = int(trailing["trades"].sum())
    if mode == "expectancy":
        return pnl / max(trades, 1)
    rets = trailing["ret_pct"].astype(float)
    dd_penalty = abs(min(0.0, float(rets.min()))) * 0.35
    vol_penalty = float(rets.std(ddof=0) if len(rets) else 0.0) * 0.20
    silence_penalty = 1.0 if trades == 0 else 0.0
    return float(rets.sum() - dd_penalty - vol_penalty - silence_penalty)


def _simulate(
    name: str,
    expert_daily: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    starting_balance: float,
    *,
    policy: str,
    lookback_days: int = 5,
    min_score: float = 0.0,
    loss_stop_pct: float = -8.0,
    warmup_days: int = 5,
) -> PolicyResult:
    days = market.index.intersection(next(iter(expert_daily.values())).index)
    experts = [e for e in expert_daily.keys() if e != "cash"]
    cash_name = "cash" if "cash" in expert_daily else next(iter(expert_daily.keys()))

    rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    equity = starting_balance
    active = cash_name
    active_start_equity = equity
    month_active_days: dict[str, int] = {}

    for day in days:
        hist_days = [d for d in rows if d["day"] < day]
        month_key = _month_key(day)
        month_active_days.setdefault(month_key, 0)

        chosen = active
        reason = "continue"

        if policy == "static_growth":
            chosen, reason = "growth", "static"
        elif policy == "static_h4":
            chosen, reason = "h4", "static"
        elif policy == "static_defensive":
            chosen, reason = "defensive", "static"
        elif policy == "cash":
            chosen, reason = cash_name, "cash"
        elif policy == "rolling_winner":
            chosen, reason = _choose_rolling(expert_daily, experts, cash_name, day, lookback_days, min_score)
        elif policy == "expectancy_rotation":
            chosen, reason = _choose_rolling(
                expert_daily, experts, cash_name, day, lookback_days, min_score, score_mode="expectancy"
            )
        elif policy == "drawdown_switch":
            if active != cash_name and equity / active_start_equity - 1.0 <= loss_stop_pct / 100.0:
                chosen, reason = cash_name, f"loss_stop_{loss_stop_pct}"
            elif active == cash_name:
                chosen, reason = _choose_rolling(expert_daily, experts, cash_name, day, lookback_days, min_score)
                active_start_equity = equity
            else:
                chosen, reason = active, "active_not_stopped"
        elif policy == "first_week_observe":
            day_in_month = _trading_day_number(days, day)
            if day_in_month <= warmup_days:
                chosen, reason = cash_name, f"observe_day_{day_in_month}"
            else:
                chosen, reason = _choose_month_profile(
                    market, expert_daily, experts, cash_name, day, warmup_days
                )
        elif policy == "regime_map":
            chosen, reason = _choose_regime_map(market, day, cash_name)
        elif policy == "prove_it":
            base, why = _choose_rolling(expert_daily, experts, cash_name, day, lookback_days, min_score)
            recent_rows = pd.DataFrame(hist_days[-lookback_days:])
            if base != cash_name and not recent_rows.empty and recent_rows["pnl"].sum() > 0:
                chosen, reason = base, "proved_" + why
            else:
                chosen, reason = cash_name, "not_proved"
        else:
            raise ValueError(f"unknown policy {policy!r}")

        if chosen != active:
            active = chosen
            active_start_equity = equity

        eday = expert_daily[chosen].loc[day]
        # Convert the selected expert's static daily return into adaptive
        # account P&L on the controller's current equity. This preserves
        # compounding while using the expert as a return stream.
        ret_pct = float(eday["ret_pct"])
        pnl = equity * ret_pct / 100.0
        equity += pnl
        month_active_days[month_key] += 0 if chosen == cash_name else 1
        rows.append({
            "day": day,
            "expert": chosen,
            "reason": reason,
            "ret_pct": ret_pct,
            "pnl": pnl,
            "equity": equity,
            "trades": int(eday["trades"]),
        })
        decisions.append({
            "day": day.isoformat(),
            "expert": chosen,
            "reason": reason,
            "equity": equity,
            "ret_pct": ret_pct,
        })

    daily = pd.DataFrame(rows)
    if daily.empty:
        metrics = _policy_metrics(pd.DataFrame(), starting_balance)
    else:
        daily["day"] = pd.to_datetime(daily["day"], utc=True)
        daily = daily.set_index("day")
        metrics = _policy_metrics(daily, starting_balance)
    return PolicyResult(name=name, metrics=metrics, daily=daily, decisions=pd.DataFrame(decisions))


def _choose_rolling(
    expert_daily: dict[str, pd.DataFrame],
    experts: list[str],
    cash_name: str,
    day: pd.Timestamp,
    lookback_days: int,
    min_score: float,
    *,
    score_mode: str = "risk_adjusted",
) -> tuple[str, str]:
    scores = {}
    for e in experts:
        d = expert_daily[e]
        hist = d[d.index < day].tail(lookback_days)
        scores[e] = _score(hist, mode=score_mode)
    best = max(scores, key=scores.get)
    if scores[best] <= min_score:
        return cash_name, f"best_score_{scores[best]:.2f}_<=_{min_score}"
    return best, f"{score_mode}_{lookback_days}d_{scores[best]:.2f}"


def _trading_day_number(days: pd.DatetimeIndex, day: pd.Timestamp) -> int:
    day_month = _month_key(day)
    same = [d for d in days if _month_key(d) == day_month and d <= day]
    return len(same)


def _choose_month_profile(
    market: pd.DataFrame,
    expert_daily: dict[str, pd.DataFrame],
    experts: list[str],
    cash_name: str,
    day: pd.Timestamp,
    warmup_days: int,
) -> tuple[str, str]:
    day_month = _month_key(day)
    month_days = [d for d in market.index if _month_key(d) == day_month and d < day]
    obs = month_days[:warmup_days]
    if len(obs) < warmup_days:
        return cash_name, "insufficient_month_observation"
    m = market.loc[obs]
    ret = float(m["mkt_ret_pct"].sum())
    vol = float(m["range_pct"].mean())
    up_share = float(m["up_day"].mean())
    # Rules intentionally simple: April-like flat/high-chop behavior gets
    # h4/defensive; strong directional months get growth pivots.
    if abs(ret) < 3.0 and vol >= 1.2:
        return ("h4" if "h4" in experts else cash_name), f"chop_month ret={ret:.1f} vol={vol:.1f}"
    if ret > 3.0 or up_share > 0.62:
        return ("growth" if "growth" in experts else cash_name), f"up_month ret={ret:.1f} up={up_share:.2f}"
    if ret < -3.0:
        return ("defensive" if "defensive" in experts else cash_name), f"down_month ret={ret:.1f}"
    return cash_name, f"unclear_month ret={ret:.1f} vol={vol:.1f}"


def _choose_regime_map(market: pd.DataFrame, day: pd.Timestamp, cash_name: str) -> tuple[str, str]:
    hist = market[market.index < day].tail(3)
    if len(hist) < 2:
        return cash_name, "insufficient_regime"
    trend = float(hist["mkt_ret_pct"].sum())
    vol = float(hist["range_pct"].mean())
    chop = float(hist["range_body"].replace([np.inf, -np.inf], np.nan).mean())
    if vol > 1.6 and abs(trend) < 2.0:
        return "h4", f"volatile_chop vol={vol:.1f} trend={trend:.1f}"
    if abs(trend) > 4.0:
        return "growth", f"persistent_trend trend={trend:.1f}"
    if chop > 2.5:
        return "defensive", f"wicky_chop rb={chop:.1f}"
    return "cash", f"no_edge vol={vol:.1f} trend={trend:.1f} rb={chop:.1f}"


def _policy_metrics(daily: pd.DataFrame, starting_balance: float) -> dict[str, Any]:
    if daily.empty:
        return {}
    equity = daily["equity"].astype(float)
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max * 100.0
    wins = daily[daily["pnl"] > 0]["pnl"]
    losses = daily[daily["pnl"] < 0]["pnl"]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
    active_days = int((daily["expert"] != "cash").sum())
    switches = int((daily["expert"] != daily["expert"].shift()).sum() - 1)
    month_growth = daily["equity"].resample("ME").last().pct_change()
    first_month_end = daily["equity"].resample("ME").last()
    if not first_month_end.empty:
        month_start = first_month_end.shift(1)
        month_start.iloc[0] = starting_balance
        monthly = (first_month_end / month_start - 1.0) * 100.0
    else:
        monthly = pd.Series(dtype=float)
    return {
        "return_pct": float((equity.iloc[-1] / starting_balance - 1.0) * 100.0),
        "final_equity": float(equity.iloc[-1]),
        "profit_factor": float(profit_factor),
        "max_drawdown_pct": float(dd.min()) if len(dd) else 0.0,
        "min_equity_pct": float(equity.min() / starting_balance * 100.0),
        "trades": int(daily["trades"].sum()),
        "active_days": active_days,
        "cash_days": int(len(daily) - active_days),
        "switches": max(switches, 0),
        "best_day_pct": float(daily["ret_pct"].max()),
        "worst_day_pct": float(daily["ret_pct"].min()),
        "monthly_pct_mean": float(monthly.mean()) if len(monthly) else 0.0,
        "monthly_pct_min": float(monthly.min()) if len(monthly) else 0.0,
        "monthly_pct_max": float(monthly.max()) if len(monthly) else 0.0,
        "monthly_returns": {_month_key(k): float(v) for k, v in monthly.items()},
        "cap_violations": int((daily["ret_pct"] < -10.5).sum()),
        "ruin_flag": bool(equity.min() / starting_balance * 100.0 <= 25.0),
    }


def _month_key(ts: pd.Timestamp) -> str:
    """Return YYYY-MM without pandas' tz-aware Period warning."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return str(t.to_period("M"))


def _oracle(expert_daily: dict[str, pd.DataFrame], starting_balance: float) -> PolicyResult:
    experts = [e for e in expert_daily if e != "cash"]
    days = next(iter(expert_daily.values())).index
    rows = []
    equity = starting_balance
    for day in days:
        best = max(experts, key=lambda e: float(expert_daily[e].loc[day]["ret_pct"]))
        ret = float(expert_daily[best].loc[day]["ret_pct"])
        pnl = equity * ret / 100.0
        equity += pnl
        rows.append({"day": day, "expert": best, "reason": "hindsight", "ret_pct": ret, "pnl": pnl, "equity": equity, "trades": int(expert_daily[best].loc[day]["trades"])})
    daily = pd.DataFrame(rows).set_index(pd.to_datetime([r["day"] for r in rows], utc=True))
    daily.index.name = "day"
    for col in ("expert", "reason", "ret_pct", "pnl", "equity", "trades"):
        daily[col] = [r[col] for r in rows]
    return PolicyResult("oracle_hindsight", _policy_metrics(daily, starting_balance), daily, pd.DataFrame(rows))


def _print_summary(label: str, metrics: dict[str, Any]) -> None:
    print(
        f"{label:24s} return={metrics.get('return_pct', 0):+8.2f}% "
        f"PF={metrics.get('profit_factor', 0):>5.2f} "
        f"DD={metrics.get('max_drawdown_pct', 0):+7.2f}% "
        f"min_eq={metrics.get('min_equity_pct', 0):6.1f}% "
        f"trades={metrics.get('trades', 0):4d} "
        f"active={metrics.get('active_days', 0):3d} "
        f"cash={metrics.get('cash_days', 0):3d} "
        f"switch={metrics.get('switches', 0):3d} "
        f"cap={metrics.get('cap_violations', 0)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--expert", action="append", default=list(DEFAULT_EXPERTS),
                    help="Expert as name=config.yaml or name=CASH. Defaults include growth/h4/defensive/cash.")
    ap.add_argument("--lookback-days", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--loss-stop-pct", type=float, default=-8.0)
    ap.add_argument("--warmup-days", type=int, default=5)
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/iter29_adaptive"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    experts = [parse_expert(x) for x in args.expert]
    expert_daily, expert_metrics, sb = load_experts(df, experts)
    market = _market_features(df)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("# Static experts")
    for name, met in expert_metrics.items():
        if name == "cash":
            continue
        print(
            f"{name:12s} return={met.get('return_pct', 0):+8.2f}% "
            f"PF={met.get('profit_factor', 0):5.2f} "
            f"DD={met.get('max_drawdown_pct', 0):+7.2f}% "
            f"trades={met.get('trades', 0):4d}"
        )
    print()

    policies = [
        "static_growth",
        "static_h4",
        "static_defensive",
        "rolling_winner",
        "expectancy_rotation",
        "drawdown_switch",
        "first_week_observe",
        "regime_map",
        "prove_it",
        "cash",
    ]
    results = []
    for p in policies:
        res = _simulate(
            p,
            expert_daily,
            market,
            sb,
            policy=p,
            lookback_days=args.lookback_days,
            min_score=args.min_score,
            loss_stop_pct=args.loss_stop_pct,
            warmup_days=args.warmup_days,
        )
        results.append(res)
        _print_summary(p, res.metrics)
        res.daily.to_csv(args.out_dir / f"{p}_daily.csv")
        res.decisions.to_csv(args.out_dir / f"{p}_decisions.csv", index=False)

    oracle = _oracle(expert_daily, sb)
    _print_summary("oracle_hindsight", oracle.metrics)
    oracle.daily.to_csv(args.out_dir / "oracle_hindsight_daily.csv")

    payload = {
        "experts": expert_metrics,
        "policies": {r.name: r.metrics for r in results},
        "oracle_hindsight": oracle.metrics,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(payload, indent=2, default=str))
    if args.json:
        print(json.dumps(payload, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
