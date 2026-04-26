"""Iter29 trade-attribution helper.

Run one or more configs and break closed P&L down by causal tags that are
already present in order comments: ensemble member, pivot period, pivot level,
weekday, and close reason. This is deliberately a diagnostics script; it does
not alter backtest semantics or strategy behaviour.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.backtest.splitter import load_recent_only_held_out
from ai_trader.backtest.sweep import risk_kwargs_from_config
from ai_trader.broker.paper import PaperBroker
from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec, RiskManager
from ai_trader.strategy.registry import get_strategy


def _build(cfg: dict[str, Any]):
    inst_cfg = cfg["instrument"]
    instrument = InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )
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


def _run(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    strat, risk, broker, sb = _build(cfg)
    result = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    metrics = compute_metrics(result, starting_balance=sb)
    rows = []
    for t in result.trades:
        rows.append({
            "open_time": pd.Timestamp(t.open_time),
            "close_time": pd.Timestamp(t.close_time),
            "side": t.side,
            "lots": t.lots,
            "entry": t.entry,
            "exit": t.exit,
            "pnl": t.pnl,
            "reason": t.reason,
            "comment": t.comment,
            "member": _member(t.comment),
            "pivot_period": _period(t.comment),
            "pivot_level": _level(t.comment),
            "weekday": pd.Timestamp(t.close_time).day_name()[:3],
            "hour": pd.Timestamp(t.close_time).hour,
        })
    return pd.DataFrame(rows), metrics


def _member(comment: str) -> str:
    if comment.startswith("[") and "]" in comment:
        return comment[1:comment.index("]")]
    if "pivot-bounce" in comment:
        return "pivot_bounce"
    return comment.split()[0] if comment else "unknown"


def _period(comment: str) -> str:
    # Period is not in old comments; infer from ensemble member where possible.
    # New context-meta configs still keep comments compact, so member+level is
    # the stable attribution key.
    return "unknown"


def _level(comment: str) -> str:
    m = re.search(r"@(S1|S2|R1|R2)=", comment)
    return m.group(1) if m else "unknown"


def _summary(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(by, dropna=False)
    out = g.agg(
        trades=("pnl", "size"),
        pnl=("pnl", "sum"),
        wins=("pnl", lambda s: int((s > 0).sum())),
        losses=("pnl", lambda s: int((s < 0).sum())),
        avg=("pnl", "mean"),
    )
    out["win_rate"] = out["wins"] / out["trades"]
    return out.sort_values("pnl", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/iter29_attribution"))
    ap.add_argument("configs", nargs="+", type=Path)
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    split = load_recent_only_held_out(
        df,
        research_days=60,
        validation_days=14,
        tournament_days=14,
        i_know_this_is_tournament_evaluation=True,
    )
    windows = {"full": df, "research": split.research, "validation": split.validation, "tournament": split.tournament}
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for cfg_path in args.configs:
        cfg = load_config(cfg_path)
        label = cfg_path.stem
        print(f"\n# {label}")
        all_rows = []
        for wname, wdf in windows.items():
            trades, metrics = _run(wdf, cfg)
            if not trades.empty:
                trades["window"] = wname
                all_rows.append(trades)
            print(f"{wname:10s} return={metrics['return_pct']:+8.2f}% PF={metrics['profit_factor']:.2f} trades={metrics['trades']:4d} cap={metrics['cap_violations']}")
        merged = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
        merged.to_csv(args.out_dir / f"{label}_trades.csv", index=False)
        for by in (["window", "member"], ["window", "pivot_level"], ["window", "weekday"], ["window", "reason"]):
            s = _summary(merged, by)
            s.to_csv(args.out_dir / f"{label}_{'_'.join(by)}.csv")
            print(f"\nby {'/'.join(by)}")
            print(s.head(12).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
