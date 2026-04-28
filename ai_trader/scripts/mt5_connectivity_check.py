"""End-to-end MT5 connectivity smoke test (Windows + MetaTrader 5 terminal).

Run on the Beeks VPS after installing ``ai-trader[live]``::

    python -m ai_trader.scripts.mt5_connectivity_check --config config\\live_local.yaml

Verifies Python can attach to the terminal, log in, select the configured symbol,
and download recent rates. Exits 0 on success.

This module cannot run in Linux CI: the ``MetaTrader5`` wheel is Windows-only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import load_config
from ..risk.manager import InstrumentSpec
from .broker_config import (
    build_mt5_broker_from_config,
    ensure_password_if_required,
)


def main() -> int:  # pragma: no cover — requires MT5 runtime
    ap = argparse.ArgumentParser(description="MT5 initialize + rates smoke test")
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

    live_cfg = cfg.get("live", {}) or {}
    broker_cfg = cfg.get("broker", {}) or {}
    ensure_password_if_required(broker_cfg)

    broker = build_mt5_broker_from_config(
        instrument=instrument,
        live_cfg=live_cfg,
        broker_cfg=broker_cfg,
    )

    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as e:
        print(
            "MetaTrader5 package not installed. On Windows: pip install -e \".[live]\"",
            file=sys.stderr,
        )
        raise SystemExit(2) from e

    broker.connect()
    try:
        ai = mt5.account_info()
        if ai is None:
            raise RuntimeError("account_info() returned None — not logged in?")
        sym = instrument.symbol
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            raise RuntimeError(f"no tick for {sym} — add symbol to Market Watch")

        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
        }
        tf = tf_map[str(inst_cfg["timeframe"])]
        rates = mt5.copy_rates_from_pos(sym, tf, 0, 50)
        if rates is None or len(rates) == 0:
            raise RuntimeError("copy_rates_from_pos returned no bars")

        print("MT5 connectivity OK")
        print(f"  login={ai.login} server={ai.server} balance={ai.balance} {ai.currency}")
        print(f"  symbol={sym} bid={tick.bid} ask={tick.ask}")
        print(f"  bars_fetched={len(rates)} timeframe={inst_cfg['timeframe']}")
        return 0
    finally:
        broker.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
