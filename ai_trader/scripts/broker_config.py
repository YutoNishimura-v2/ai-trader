"""YAML `broker:` section helpers for MT5 live scripts.

Pure configuration logic (no MetaTrader import) so it can be unit-tested on Linux.
"""
from __future__ import annotations

import os
from typing import Any

from ..broker.mt5_live import MT5LiveBroker
from ..risk.manager import InstrumentSpec


def env(name: str | None) -> str | None:
    if not name:
        return None
    return os.environ.get(name)


def login_from_broker_cfg(broker_cfg: dict[str, Any]) -> int | None:
    alt = broker_cfg.get("mt5_login_env")
    if alt:
        v = env(str(alt))
        if v is not None and str(v).strip():
            return int(str(v).strip())
    acct = broker_cfg.get("account")
    return int(acct) if acct is not None else None


def terminal_path_from_broker_cfg(broker_cfg: dict[str, Any]) -> str | None:
    tp = broker_cfg.get("mt5_terminal_path_env")
    if tp:
        return env(str(tp))
    raw = broker_cfg.get("terminal_path")
    return str(raw) if raw else None


def password_from_broker_cfg(broker_cfg: dict[str, Any]) -> str | None:
    pw_env = broker_cfg.get("password_env")
    if pw_env:
        pw = env(str(pw_env))
        if pw:
            return pw
    direct = broker_cfg.get("password")
    return str(direct) if direct else None


def build_mt5_broker_from_config(
    *,
    instrument: InstrumentSpec,
    live_cfg: dict[str, Any],
    broker_cfg: dict[str, Any],
) -> MT5LiveBroker:
    """Construct ``MT5LiveBroker`` from merged YAML (does not connect)."""
    return MT5LiveBroker(
        instrument=instrument,
        magic=int(live_cfg.get("magic_number", 20260424)),
        comment=str(live_cfg.get("comment", "ai-trader-demo")),
        terminal_path=terminal_path_from_broker_cfg(broker_cfg),
        login=login_from_broker_cfg(broker_cfg),
        server=broker_cfg.get("server"),
        password=password_from_broker_cfg(broker_cfg),
    )


def ensure_password_if_required(broker_cfg: dict[str, Any]) -> None:
    """Exit with SystemExit if ``password_env`` is set but missing in the environment."""
    pw_env = broker_cfg.get("password_env")
    if pw_env and password_from_broker_cfg(broker_cfg) is None:
        raise SystemExit(
            f"Missing environment variable {pw_env!r} (MT5 trading password). "
            "Do not commit passwords; set the variable on the VPS only."
        )
