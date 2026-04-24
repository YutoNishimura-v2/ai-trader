"""Run the bot against an MT5 demo account.

This is a thin CLI wrapper around ``LiveRunner`` that wires together
the live MT5 broker and an MT5 bar fetcher. Windows-only at runtime.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from ..broker.mt5_live import MT5LiveBroker
from ..config import load_config
from ..live.runner import LiveRunner
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy


def _make_mt5_fetcher(symbol: str, timeframe: str):  # pragma: no cover
    import MetaTrader5 as mt5  # type: ignore
    import pandas as pd

    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    }
    tf = tf_map[timeframe]

    def fetch(n: int) -> "pd.DataFrame":
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"}).set_index("time")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    return fetch


def main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args()

    cfg = load_config(args.config)

    inst_cfg = cfg["instrument"]
    instrument = InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
    )

    risk_cfg = cfg["risk"]
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        risk_per_trade_pct=float(risk_cfg["risk_per_trade_pct"]),
        daily_profit_target_pct=float(risk_cfg["daily_profit_target_pct"]),
        daily_max_loss_pct=float(risk_cfg["daily_max_loss_pct"]),
        withdraw_half_of_daily_profit=bool(risk_cfg.get("withdraw_half_of_daily_profit", True)),
        max_concurrent_positions=int(risk_cfg.get("max_concurrent_positions", 1)),
    )

    live_cfg = cfg.get("live", {})
    broker_cfg = cfg.get("broker", {})
    password_env = broker_cfg.get("password_env")
    password = os.environ.get(password_env) if password_env else None
    broker = MT5LiveBroker(
        instrument=instrument,
        magic=int(live_cfg.get("magic_number", 20260424)),
        comment=live_cfg.get("comment", "ai-trader-demo"),
        login=broker_cfg.get("account"),
        server=broker_cfg.get("server"),
        password=password,
    )

    strat_cfg = cfg["strategy"]
    strategy = get_strategy(strat_cfg["name"], **strat_cfg.get("params", {}))

    fetcher = _make_mt5_fetcher(instrument.symbol, inst_cfg["timeframe"])
    runner = LiveRunner(
        strategy=strategy,
        risk=risk,
        broker=broker,
        fetch_bars=fetcher,
        poll_seconds=int(live_cfg.get("poll_seconds", 5)),
    )
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
