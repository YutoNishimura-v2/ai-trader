"""Tests for ``ai_trader/research/stability.py``."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ai_trader.research import stability
from ai_trader.research.stability import (
    DISQUALIFIED_SCORE,
    Window,
    build_rolling_windows,
    compute_best_month,
    evaluate_config,
    generalization_score,
    promotion_status,
    score_config,
)


def _synthetic_m1(days: int, *, freq: str = "1min", seed: int = 0) -> pd.DataFrame:
    """Build a minimal M1 OHLCV frame for ``days`` calendar days."""
    rng = np.random.default_rng(seed)
    n = days * 24 * 60
    idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC")
    rets = rng.normal(0, 0.0001, size=n)
    price = 2000 * np.exp(np.cumsum(rets))
    df = pd.DataFrame(
        {
            "open": price,
            "high": price * 1.0002,
            "low": price * 0.9998,
            "close": price,
            "volume": 1.0,
        },
        index=idx,
    )
    return df


def test_build_rolling_windows_layout() -> None:
    df = _synthetic_m1(days=120)
    windows = build_rolling_windows(
        df,
        n_windows=4,
        research_days=30,
        validation_days=14,
        test_days=14,
        step_days=14,
        min_research_bars=1_000,
        min_validation_bars=500,
        min_test_bars=500,
    )
    assert len(windows) == 4
    # Test slices are non-overlapping and ordered ascending.
    for i in range(len(windows) - 1):
        assert windows[i].test_span[1] <= windows[i + 1].test_span[0]
    # Each window has the documented contiguous layout.
    for w in windows:
        assert w.research_span[1] == w.validation_span[0]
        assert w.validation_span[1] == w.test_span[0]


def test_build_rolling_windows_drops_short_slices() -> None:
    """With far too-aggressive minimum bar counts, the harness raises."""
    df = _synthetic_m1(days=70)
    with pytest.raises(ValueError):
        build_rolling_windows(
            df,
            n_windows=4,
            min_research_bars=10**9,
            min_validation_bars=10**9,
            min_test_bars=10**9,
        )


def test_generalization_score_disqualifies_on_cap_viol() -> None:
    val = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 2.0, "return_pct": 5.0}
    test = {"cap_violations": 1, "ruin_flag": False, "profit_factor": 2.0, "return_pct": 5.0}
    assert generalization_score(val, test) == DISQUALIFIED_SCORE


def test_generalization_score_disqualifies_on_ruin() -> None:
    val = {"cap_violations": 0, "ruin_flag": True, "profit_factor": 2.0, "return_pct": 5.0}
    test = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 2.0, "return_pct": 5.0}
    assert generalization_score(val, test) == DISQUALIFIED_SCORE


def test_generalization_score_disqualifies_on_pf_below_one() -> None:
    val = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 0.9, "return_pct": 5.0}
    test = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 2.0, "return_pct": 5.0}
    assert generalization_score(val, test) == DISQUALIFIED_SCORE


def test_generalization_score_disqualifies_on_sign_mismatch() -> None:
    val = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 1.5, "return_pct": -1.0}
    test = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 1.5, "return_pct": 5.0}
    assert generalization_score(val, test) == DISQUALIFIED_SCORE


def test_generalization_score_returns_min_when_passing() -> None:
    val = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 2.0, "return_pct": 12.0}
    test = {"cap_violations": 0, "ruin_flag": False, "profit_factor": 1.4, "return_pct": 7.5}
    assert generalization_score(val, test) == pytest.approx(7.5)


def test_compute_best_month() -> None:
    metrics = {"monthly_returns": {"2026-01": -3.0, "2026-02": 11.0, "2026-03": 5.0}}
    val, label = compute_best_month(metrics)
    assert val == pytest.approx(11.0)
    assert label == "2026-02"


def test_compute_best_month_empty() -> None:
    val, label = compute_best_month({"monthly_returns": {}})
    assert val == 0.0
    assert label == ""


def _real_dataset_or_skip() -> Path:
    p = Path("data/xauusd_m1_2026.csv")
    if not p.exists():
        pytest.skip("data/xauusd_m1_2026.csv not present; run fetch_dukascopy first")
    return p


def _make_pivot_cfg() -> dict:
    """A minimal cap-clean config that's fast and produces real trades."""
    return {
        "account": {"currency": "JPY", "starting_balance": 100000.0, "max_leverage": 100.0},
        "fx": {"USDJPY": 150.0},
        "instrument": {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "contract_size": 100,
            "tick_size": 0.01,
            "tick_value": 1.0,
            "quote_currency": "USD",
        },
        "execution": {
            "spread_points": 8,
            "slippage_points": 2,
            "commission_per_lot": 0.0,
        },
        "risk": {
            "risk_per_trade_pct": 2.5,
            "daily_profit_target_pct": 30.0,
            "daily_max_loss_pct": 10.0,
            "withdraw_half_of_daily_profit": False,
            "max_concurrent_positions": 1,
            "lot_cap_per_unit_balance": 0.000001,
        },
        "strategy": {
            "name": "pivot_bounce",
            "params": {
                "pivot_period": "daily",
                "atr_period": 14,
                "touch_atr_buf": 0.05,
                "sl_atr_buf": 0.30,
                "max_sl_atr": 2.0,
                "tp1_rr": 1.0,
                "tp2_rr": 2.0,
                "leg1_weight": 0.5,
                "cooldown_bars": 60,
                "session": "london_or_ny",
                "use_s2r2": True,
                "max_trades_per_day": 4,
            },
        },
    }


def test_evaluate_config_smoke(tmp_path: Path) -> None:
    """End-to-end: build windows from real data, run pivot_bounce, get a verdict."""
    csv = _real_dataset_or_skip()
    from ai_trader.data.csv_loader import load_ohlcv_csv

    df = load_ohlcv_csv(csv)
    windows = build_rolling_windows(
        df,
        n_windows=4,
        research_days=30,
        validation_days=14,
        test_days=14,
        step_days=14,
    )
    assert len(windows) >= 2
    cfg = _make_pivot_cfg()
    audit_path = tmp_path / "audit.jsonl"
    ev = evaluate_config(
        cfg,
        full_df=df,
        windows=windows,
        audit_path=audit_path,
        label="harness-smoke",
        i_know_this_is_tournament_evaluation=True,
    )
    # We get one WindowResult per Window with finite-or-DQ scores.
    assert len(ev.windows) == len(windows)
    for w in ev.windows:
        assert w.label.startswith("W")
        assert isinstance(w.score, float)
    # Audit file has one line per window, all stamped with the opt-in token.
    lines = audit_path.read_text().splitlines()
    assert len(lines) == len(windows)
    for line in lines:
        rec = json.loads(line)
        assert rec["audit_token"] == "i_know_this_is_tournament_evaluation=True"
        assert rec["label"] == "harness-smoke"
        assert rec["config_hash"] == ev.config_hash
    # Score function row contains every column we expect.
    row = score_config(ev)
    for w in ev.windows:
        assert f"{w.label}_val_pf" in row
        assert f"{w.label}_test_pf" in row
        assert f"{w.label}_score" in row
    # Promotion status returns one of the known labels.
    verdict = promotion_status(ev)
    assert verdict.status in {"promotable", "candidate", "falsified", "disqualified"}


def test_audit_violation_when_token_missing(tmp_path: Path) -> None:
    csv = _real_dataset_or_skip()
    from ai_trader.data.csv_loader import load_ohlcv_csv

    df = load_ohlcv_csv(csv)
    windows = build_rolling_windows(df, n_windows=2)
    cfg = _make_pivot_cfg()
    audit_path = tmp_path / "audit.jsonl"
    evaluate_config(
        cfg,
        full_df=df,
        windows=windows,
        audit_path=audit_path,
        i_know_this_is_tournament_evaluation=False,
    )
    rec = json.loads(audit_path.read_text().splitlines()[0])
    assert rec["audit_token"] == "audit_violation"
