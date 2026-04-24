"""Namespaced sweep grid keys (strategy./risk./exec.)."""
from pathlib import Path

import pytest

from ai_trader.backtest.sweep import SweepConfig, _partition, run_sweep
from ai_trader.data.synthetic import generate_synthetic_ohlcv
from ai_trader.risk.fx import FixedFX
from ai_trader.risk.manager import InstrumentSpec


def test_partition_routes_dotted_keys():
    s, r, e = _partition(
        {
            "sl_atr_mult": 1.5,              # bare → strategy
            "strategy.tp_rr": 2.0,           # explicit strategy
            "risk.risk_per_trade_pct": 1.0,  # risk override
            "exec.spread_points": 14,        # exec override
        }
    )
    assert s == {"sl_atr_mult": 1.5, "tp_rr": 2.0}
    assert r == {"risk_per_trade_pct": 1.0}
    assert e == {"spread_points": 14}


def test_partition_empty():
    s, r, e = _partition({})
    assert s == {} and r == {} and e == {}


def _xau_usd() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD", contract_size=100.0, tick_size=0.01, tick_value=1.0,
        quote_currency="USD", min_lot=0.01, lot_step=0.01,
    )


def test_sweep_applies_risk_override(tmp_path: Path):
    """Grid with a risk.* key: different risk-% values must produce
    different lot-sizing behaviour, visible as different trade counts
    or metrics on the same window."""
    df = generate_synthetic_ohlcv(days=30, timeframe="M5", seed=41)
    cfg = SweepConfig(
        sweep_id="partition-smoke",
        strategy_name="trend_pullback_fib",
        grid={"risk.risk_per_trade_pct": [0.1, 2.0]},
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
        max_trials=20,
        objective="profit_factor",
    )
    r = run_sweep(cfg, df, artifacts_root=tmp_path)
    assert len(r.trials) == 2
    # At least one metric should differ between the two trials,
    # proving the override actually made it through.
    m0, m1 = r.trials[0].metrics, r.trials[1].metrics
    differ = any(
        abs(float(m0.get(k, 0)) - float(m1.get(k, 0))) > 1e-9
        for k in ("net_profit", "trades", "gross_profit", "gross_loss")
    )
    assert differ, f"risk override had no effect: {m0=} {m1=}"
