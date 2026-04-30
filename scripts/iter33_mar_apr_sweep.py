#!/usr/bin/env python3
"""Sweep Mar/Apr-focused variants on the stability harness (research only)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import build_rolling_windows, evaluate_config, score_config


def _patch_members(cfg: dict, indices: tuple[int, ...], **pivot_kw) -> dict:
    c = copy.deepcopy(cfg)
    mems = c["strategy"]["params"]["members"]
    for i in indices:
        mems[i]["params"] = {**mems[i].get("params", {}), **pivot_kw}
    return c


def _patch_risk(cfg: dict, **risk_kw) -> dict:
    c = copy.deepcopy(cfg)
    c["risk"] = {**c.get("risk", {}), **risk_kw}
    return c


def _patch_router(cfg: dict, **router_kw) -> dict:
    c = copy.deepcopy(cfg)
    c["strategy"]["params"].update(router_kw)
    return c


def main() -> None:
    csv_path = Path("data/xauusd_m1_2026.csv")
    if not csv_path.exists():
        print("missing", csv_path, file=sys.stderr)
        sys.exit(1)
    df = load_ohlcv_csv(csv_path)
    windows = build_rolling_windows(df)

    base = load_config("config/iter29/v4_plus_h4_protector_conc1.yaml")
    ad_b = load_config("config/iter31/adaptive_v55_expectancy_sizing_b.yaml")

    trials: list[tuple[str, dict]] = [
        ("iter29_conc1_baseline", base),
        ("iter29_daily_adxmax28", _patch_members(base, (0,), htf="M15", adx_period=14, adx_max=28.0)),
        ("iter29_daily_adxmax32", _patch_members(base, (0,), htf="M15", adx_period=14, adx_max=32.0)),
        ("iter29_daily_adxmax35", _patch_members(base, (0,), htf="M15", adx_period=14, adx_max=35.0)),
        (
            "iter29_daily_weekly_adxmax30",
            _patch_members(base, (0, 1), htf="M15", adx_period=14, adx_max=30.0),
        ),
        (
            "iter29_all4_adxmax32",
            _patch_members(base, (0, 1, 2, 3), htf="M15", adx_period=14, adx_max=32.0),
        ),
        ("iter29_dml6_softdd", _patch_risk(base, daily_max_loss_pct=6.0)),
        (
            "iter29_v55_dd_only",
            _patch_risk(
                base,
                daily_max_loss_pct=5.0,
                drawdown_soft_limit_pct=10.0,
                drawdown_hard_limit_pct=15.0,
                drawdown_soft_multiplier=0.60,
                drawdown_hard_multiplier=0.30,
            ),
        ),
        ("ad_expect_b_offth06", _patch_router(ad_b, eligibility_off_threshold=-0.06)),
        ("ad_expect_b_offth12", _patch_router(ad_b, eligibility_off_threshold=-0.12)),
        ("ad_expect_b_probe045", _patch_router(ad_b, probe_risk_multiplier=0.45, active_risk_multiplier_floor=0.45)),
    ]

    rows: list[dict] = []
    for name, cfg in trials:
        ev = evaluate_config(
            cfg,
            full_df=df,
            windows=windows,
            label="iter33-sweep",
            i_know_this_is_tournament_evaluation=True,
        )
        m = ev.full_metrics.get("monthly_returns") or {}
        sc = score_config(ev)
        rows.append(
            {
                "name": name,
                "full_ret": round(float(ev.full_metrics.get("return_pct", 0)), 2),
                "pf": round(float(ev.full_metrics.get("profit_factor", 0)), 4),
                "mar": round(float(ev.mar_return_pct or 0), 2) if ev.mar_return_pct is not None else None,
                "apr": round(float(ev.apr_return_pct or 0), 2) if ev.apr_return_pct is not None else None,
                "windows_passing": ev.windows_passing,
                "worst_score": sc["worst_score"],
                "cap": ev.full_cap_violations,
            }
        )
        print(json.dumps(rows[-1]))

    # Sort: April desc, then windows_passing desc
    def key(r: dict):
        apr = r["apr"] if r["apr"] is not None else -1e9
        return (apr, r["windows_passing"], r["full_ret"])

    rows.sort(key=key, reverse=True)
    print("\n# sorted by Apr then wins:", file=sys.stderr)
    for r in rows:
        print(json.dumps(r), file=sys.stderr)


if __name__ == "__main__":
    main()
