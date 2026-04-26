"""Stress-test a config across multiple tournament window lengths.

Runs the same config on:
  - full Jan-Apr,
  - 7d / 14d / 21d recent_only tournament,
  - per-month single-month evaluations,
  - interleaved-split research/validation/tournament,
and prints a single comparison table.

Used to convince ourselves that a candidate winner isn't a single
lucky window."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from ai_trader.backtest.engine import BacktestEngine
from ai_trader.backtest.metrics import compute_metrics
from ai_trader.backtest.splitter import (
    load_recent_only_held_out,
    load_interleaved_held_out,
)
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
    risk_cfg = cfg["risk"]
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        **risk_kwargs_from_config(risk_cfg),
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


def _run(df: pd.DataFrame, cfg: dict) -> dict:
    strat, risk, broker, sb = _build(cfg)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    return compute_metrics(res, starting_balance=sb)


def _summary(label: str, m: dict) -> str:
    return (
        f"{label:25s} trades={m['trades']:>4d}  "
        f"PF={m['profit_factor']:.2f}  "
        f"return={m['return_pct']:+7.2f}%  "
        f"DD={m['max_drawdown_pct']:+7.2f}%  "
        f"min_eq={m['min_equity_pct']:.1f}%  "
        f"cap_viol={m['cap_violations']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--csv", required=True, type=Path)
    args = ap.parse_args()

    df = load_ohlcv_csv(args.csv)
    cfg = load_config(args.config)

    print(f"== {args.config.name} ==\n")
    full = _run(df, cfg)
    print(_summary("full Jan-Apr", full))
    monthly = full.get("monthly_returns") or {}
    for k, v in sorted(monthly.items()):
        print(f"  monthly {k}: {v:+7.2f}%")
    print()

    # Per-month single-period eval (catch single-month sensitivity).
    df_idx = df.index
    if df_idx.tz is None:
        df_idx = df_idx.tz_localize("UTC")
    months = pd.Series(
        df_idx.tz_convert("UTC").tz_localize(None) if df_idx.tz is not None else df_idx
    ).dt.to_period("M").unique()
    for mo in months:
        start = mo.start_time.tz_localize("UTC")
        end = mo.end_time.tz_localize("UTC")
        mask = (df.index >= start) & (df.index <= end)
        sub = df.loc[mask]
        if len(sub) < 200:
            continue
        m = _run(sub, cfg)
        print(_summary(f"month {mo}", m))
    print()

    # Multiple recent_only tournament window lengths.
    for tdays in (7, 14, 21):
        try:
            split = load_recent_only_held_out(
                df,
                research_days=60,
                validation_days=14,
                tournament_days=tdays,
                i_know_this_is_tournament_evaluation=True,
            )
        except Exception as e:
            print(f"recent_only T={tdays}: skipped ({e})")
            continue
        m_v = _run(split.validation, cfg)
        m_t = _run(split.tournament, cfg)
        print(_summary(f"validation 14d (T={tdays}d)", m_v))
        print(_summary(f"tournament {tdays}d", m_t))
    print()

    # Interleaved split (anti-regime-bias check).
    try:
        ileaved = load_interleaved_held_out(
            df,
            block_bars=5760,
            research_per_cycle=3,
            validation_per_cycle=1,
            tournament_per_cycle=1,
            i_know_this_is_tournament_evaluation=True,
        )
    except Exception as e:
        print(f"interleaved: skipped ({e})")
        return 0
    # Run each role's blocks one by one and aggregate raw return
    # roughly (each block resets the account, so this is approximate).
    for role, blocks in [
        ("interleaved research", ileaved.research),
        ("interleaved validation", ileaved.validation),
        ("interleaved tournament", ileaved.tournament),
    ]:
        per_block = []
        for b in blocks:
            mb = _run(b, cfg)
            per_block.append(mb["return_pct"])
        if per_block:
            mean = sum(per_block) / len(per_block)
            pos = sum(1 for x in per_block if x > 0)
            print(
                f"{role:25s}  blocks={len(per_block)}  mean_ret_per_block={mean:+6.2f}%  positive={pos}/{len(per_block)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
