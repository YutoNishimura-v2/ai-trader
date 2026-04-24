"""Parameter-sweep harness (plan v3 anti-p-hacking ratchet)."""
import json
from pathlib import Path

import pytest

from ai_trader.backtest.sweep import SweepConfig, run_sweep
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec


def _xau_usd() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def _cfg(sweep_id: str, grid: dict, max_trials: int = 20) -> SweepConfig:
    return SweepConfig(
        sweep_id=sweep_id,
        strategy_name="trend_pullback_fib",
        grid=grid,
        instrument=_xau_usd(),
        starting_balance=100_000.0,
        max_leverage=100.0,
        account_currency="JPY",
        fx=FixedFX.from_config({"USDJPY": 150.0}),
        risk_defaults=dict(
            risk_per_trade_pct=0.5,
            daily_profit_target_pct=30.0,
            daily_max_loss_pct=10.0,
            withdraw_half_of_daily_profit=False,
            max_concurrent_positions=1,
            lot_cap_per_unit_balance=1.0e-6,
        ),
        exec_defaults=dict(spread_points=12, slippage_points=2, commission_per_lot=0.0),
        max_trials=max_trials,
        objective="profit_factor",
    )


def test_sweep_runs_small_grid(tmp_path: Path):
    df = generate_synthetic_ohlcv(days=10, timeframe="M5", seed=11)
    cfg = _cfg("smoke", grid={"sl_atr_mult": [1.0, 2.0], "tp_rr": [1.5, 3.0]})
    res = run_sweep(cfg, df, artifacts_root=tmp_path)
    assert len(res.trials) == 4
    assert res.best is not None
    idx = (res.out_dir / "index.jsonl").read_text().splitlines()
    assert len(idx) == 4
    loaded = [json.loads(l) for l in idx]
    for row in loaded:
        assert "param_hash" in row
        assert "metrics" in row
        assert row["window_span"][0] <= row["window_span"][1]

    best_json = json.loads((res.out_dir / "best.json").read_text())
    assert best_json["strategy"] == "trend_pullback_fib"


def test_sweep_refuses_grid_bigger_than_cap(tmp_path: Path):
    df = generate_synthetic_ohlcv(days=3, timeframe="M5", seed=12)
    big_grid = {
        "sl_atr_mult": [1.0, 1.5, 2.0, 2.5],
        "tp_rr": [1.0, 1.5, 2.0, 2.5],
        "cooldown_bars": [1, 2, 4],
    }
    cfg = _cfg("too-big", grid=big_grid, max_trials=20)
    with pytest.raises(ValueError, match="max_trials=20"):
        run_sweep(cfg, df, artifacts_root=tmp_path)


def test_sweep_param_hash_is_deterministic(tmp_path: Path):
    df = generate_synthetic_ohlcv(days=5, timeframe="M5", seed=13)
    cfg = _cfg("hash", grid={"sl_atr_mult": [1.5]})
    r1 = run_sweep(cfg, df, artifacts_root=tmp_path / "a")
    r2 = run_sweep(cfg, df, artifacts_root=tmp_path / "b")
    assert r1.trials[0].param_hash == r2.trials[0].param_hash
