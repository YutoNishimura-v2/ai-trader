"""Tests for MT5 broker YAML helpers (no MetaTrader runtime)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_trader.config import load_config
from ai_trader.risk.manager import InstrumentSpec
from ai_trader.scripts.broker_config import (
    build_mt5_broker_from_config,
    ensure_password_if_required,
    login_from_broker_cfg,
    password_from_broker_cfg,
)

FIXTURE = Path(__file__).resolve().parents[1] / "config" / "live_demo_hfm.template.yaml"


def test_login_env_overrides_yaml_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_TRADER_MT5_LOGIN", "99112233")
    monkeypatch.delenv("AI_TRADER_MT5_PASSWORD", raising=False)
    cfg = load_config(FIXTURE)
    bc = cfg["broker"]
    assert login_from_broker_cfg(bc) == 99112233


def test_missing_password_env_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_TRADER_MT5_PASSWORD", raising=False)
    monkeypatch.delenv("AI_TRADER_MT5_LOGIN", raising=False)
    cfg = load_config(FIXTURE)
    bc = cfg["broker"]
    assert password_from_broker_cfg(bc) is None
    with pytest.raises(SystemExit):
        ensure_password_if_required(bc)


def test_password_from_direct_yaml_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_TRADER_MT5_PASSWORD", raising=False)
    bc = {"password_env": None, "password": "secret"}
    assert password_from_broker_cfg(bc) == "secret"


def test_build_mt5_broker_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_TRADER_MT5_LOGIN", "111")
    monkeypatch.setenv("AI_TRADER_MT5_PASSWORD", "pw")
    cfg = load_config(FIXTURE)
    inst = InstrumentSpec(
        symbol="XAUUSD",
        contract_size=100.0,
        tick_size=0.01,
        tick_value=1.0,
        quote_currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        is_24_7=False,
    )
    b = build_mt5_broker_from_config(
        instrument=inst,
        live_cfg=cfg.get("live", {}) or {},
        broker_cfg=cfg["broker"],
    )
    assert b.login == 111
    assert b.password == "pw"
    assert b.server == "HFMarketsGlobal-Demo"
