"""Run the bot against an MT5 demo account.

This is a thin CLI wrapper around ``LiveRunner`` that wires together
the live MT5 broker and an MT5 bar fetcher. Windows-only at runtime.

Credentials (recommended on VPS):

- Put account number and server in YAML under ``broker:``.
- Put secrets only in environment variables referenced by the YAML:

  - ``broker.password_env`` → MT5 investor/trader password
  - ``broker.mt5_login_env`` (optional) overrides ``broker.account``
  - ``broker.mt5_terminal_path_env`` (optional) → path to ``terminal64.exe``

Example::

    set AI_TRADER_MT5_PASSWORD=your_mt5_password
    python -m ai_trader.scripts.run_demo --config config/live_demo_hfm.template.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..config import load_config
from ..live.runner import LiveRunner
from ..risk.fx import FixedFX
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy
from .broker_config import build_mt5_broker_from_config, ensure_password_if_required


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
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )

    account_ccy = cfg["account"].get("currency", "USD")
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None

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
        lot_cap_per_unit_balance=float(risk_cfg.get("lot_cap_per_unit_balance", 0.0)),
        account_currency=account_ccy,
        fx=fx,
        dynamic_risk_enabled=bool(risk_cfg.get("dynamic_risk_enabled", False)),
        min_risk_per_trade_pct=(
            float(risk_cfg["min_risk_per_trade_pct"])
            if risk_cfg.get("min_risk_per_trade_pct") is not None
            else None
        ),
        max_risk_per_trade_pct=(
            float(risk_cfg["max_risk_per_trade_pct"])
            if risk_cfg.get("max_risk_per_trade_pct") is not None
            else None
        ),
        confidence_risk_floor=float(risk_cfg.get("confidence_risk_floor", 0.75)),
        confidence_risk_ceiling=float(risk_cfg.get("confidence_risk_ceiling", 1.5)),
        drawdown_soft_limit_pct=float(risk_cfg.get("drawdown_soft_limit_pct", 12.0)),
        drawdown_hard_limit_pct=float(risk_cfg.get("drawdown_hard_limit_pct", 25.0)),
        drawdown_soft_multiplier=float(risk_cfg.get("drawdown_soft_multiplier", 0.7)),
        drawdown_hard_multiplier=float(risk_cfg.get("drawdown_hard_multiplier", 0.4)),
    )

    live_cfg = cfg.get("live", {})
    broker_cfg = cfg.get("broker", {}) or {}
    ensure_password_if_required(broker_cfg)
    broker = build_mt5_broker_from_config(
        instrument=instrument,
        live_cfg=live_cfg,
        broker_cfg=broker_cfg,
    )

    strat_cfg = cfg["strategy"]
    strategy = get_strategy(strat_cfg["name"], **strat_cfg.get("params", {}))

    fetcher = _make_mt5_fetcher(instrument.symbol, inst_cfg["timeframe"])
    max_it = live_cfg.get("max_iterations")
    runner = LiveRunner(
        strategy=strategy,
        risk=risk,
        broker=broker,
        fetch_bars=fetcher,
        poll_seconds=int(live_cfg.get("poll_seconds", 5)),
        max_iterations=int(max_it) if max_it is not None else None,
    )
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
