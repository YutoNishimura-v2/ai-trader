from pathlib import Path

from ai_trader.config import load_config


def test_default_config_loads():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "default.yaml")
    assert cfg["instrument"]["symbol"] == "XAUUSD"
    assert cfg["account"]["max_leverage"] == 100
    assert cfg["strategy"]["name"] == "trend_pullback_fib"


def test_demo_config_inherits_from_default():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "demo.yaml")
    assert cfg["instrument"]["symbol"] == "XAUUSD"  # inherited
    assert cfg["broker"]["kind"] == "mt5"           # override added by demo.yaml
