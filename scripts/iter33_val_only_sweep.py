"""iter33: validation-only bounded sweep.

Discipline restored per user feedback:
  "this result shows overfitting to the train. please maximize
   the result of val. and use tournament data less."

Procedure:

1. Pre-declare a bounded grid (≤16 trials) of structural knobs
   varying around the iter31 v4_quad ensemble (the structural
   winner). Structural shape is FIXED (per-member set, sessions,
   pivot params) — only risk-tier knobs vary.
2. For each grid cell, run quick_eval and parse VALIDATION ONLY
   metrics. Tournament numbers are computed but NOT printed and
   NOT used to select.
3. Rank by a SINGLE validation objective: val_return_pct with
   cap-violation hard penalty (+inf-bad), tie-break on val PF.
4. The single trial with best validation score is the headline.
   Tournament read is taken ONCE at the very end and reported,
   not used for selection.

This script does NOT print tournament until step 4 to keep my
own decision process honest.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import yaml
from pathlib import Path
from itertools import product
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class TrialResult:
    config_path: str
    val_return: float
    val_pf: float
    val_dd: float
    val_trades: int
    val_cap_viol: int
    full_return: float | None = None
    full_pf: float | None = None
    full_cap_viol: int | None = None
    tourn_return: float | None = None
    tourn_pf: float | None = None
    tourn_cap_viol: int | None = None


def parse_quick_eval(text: str) -> dict[str, dict]:
    sections = {}
    parts = re.split(r'^== (.+?) ==\s*$', text, flags=re.M)
    for i in range(1, len(parts), 2):
        name = parts[i].split()[0]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out = {}
        for k, p in [
            ("trades",           r'(?:^|\s)trades=(-?\d+)'),
            ("profit_factor",    r'(?:^|\s)profit_factor=(-?\d+\.?\d*)'),
            ("return_pct",       r'(?:^|\s)return_pct=(-?\d+\.?\d*)'),
            ("max_drawdown_pct", r'(?:^|\s)max_drawdown_pct=(-?\d+\.?\d*)'),
            ("cap_violations",   r'(?:^|\s)cap_violations=(\d+)'),
        ]:
            m = re.search(p, body)
            out[k] = float(m.group(1)) if m else None
        sections[name] = out
    return sections


BASE_CONFIG = {
    "extends": "../../default.yaml",
    "account": {"__replace__": False, "max_leverage": 200},
    "instrument": {
        "symbol": "XAUUSD", "timeframe": "M1", "contract_size": 100,
        "tick_size": 0.01, "tick_value": 1.0, "quote_currency": "USD",
    },
    "risk": {
        # Filled in per-trial:
        "risk_per_trade_pct": None,
        "daily_max_loss_pct": None,
        "daily_profit_target_pct": 30.0,
        "withdraw_half_of_daily_profit": False,
        "max_concurrent_positions": 2,
        "lot_cap_per_unit_balance": 0.000020,
        "dynamic_risk_enabled": True,
        "min_risk_per_trade_pct": 1.0,
        "max_risk_per_trade_pct": None,  # mirrors risk_per_trade_pct
        "drawdown_soft_limit_pct": 12.0,
        "drawdown_hard_limit_pct": 22.0,
        "drawdown_soft_multiplier": 0.7,
        "drawdown_hard_multiplier": 0.4,
    },
}


PIVOT_DAILY = {"pivot_period": "daily", "atr_period": 14, "touch_atr_buf": 0.05, "sl_atr_buf": 0.30, "max_sl_atr": 2.0, "tp1_rr": 1.0, "tp2_rr": 1.5, "leg1_weight": 0.5, "cooldown_bars": 60, "session": "london_or_ny", "use_s2r2": True, "max_trades_per_day": 4, "weekdays": [0, 1, 2, 3]}
PIVOT_WEEKLY = {"pivot_period": "weekly", "atr_period": 14, "touch_atr_buf": 0.10, "sl_atr_buf": 0.28, "max_sl_atr": 2.0, "tp1_rr": 1.0, "tp2_rr": 2.5, "leg1_weight": 0.5, "cooldown_bars": 60, "session": "london_or_ny", "use_s2r2": True, "max_trades_per_day": 2, "weekdays": [0, 1, 2, 3]}
PIVOT_MONTHLY = {"pivot_period": "monthly", "atr_period": 14, "touch_atr_buf": 0.15, "sl_atr_buf": 0.30, "max_sl_atr": 3.0, "tp1_rr": 1.2, "tp2_rr": 2.5, "leg1_weight": 0.5, "cooldown_bars": 120, "session": "london", "use_s2r2": True, "max_trades_per_day": 1, "weekdays": [0, 1, 2, 3]}
EMA20 = {"ema_period": 20, "confirm_bars": 2, "touch_dollar": 0.80, "sl_buffer_dollar": 1.0, "tp_buffer_dollar": 1.0, "swing_lookback_bars": 8, "max_sl_dollar": 6.0, "tp1_rr": 1.0, "leg1_weight": 0.5, "cooldown_m15_bars": 4, "session": "london", "max_trades_per_day": 4, "htf": "H4", "htf_ema_period": 20}
ENGULFING = {"atr_period": 14, "min_body_atr": 0.8, "require_oversold": True, "oversold_lookback": 12, "sl_buffer_dollar": 0.5, "max_sl_dollar": 8.0, "tp1_rr": 1.0, "tp2_rr": 2.5, "leg1_weight": 0.5, "cooldown_m15_bars": 4, "session": "london", "max_trades_per_day": 3}


def make_config(risk: float, dml: float, ema_rm: float, eng_rm: float | None) -> dict:
    cfg = yaml.safe_load(yaml.safe_dump(BASE_CONFIG))  # deep copy
    cfg["risk"]["risk_per_trade_pct"] = risk
    cfg["risk"]["max_risk_per_trade_pct"] = risk
    cfg["risk"]["daily_max_loss_pct"] = dml
    members = [
        {"name": "pivot_bounce", "risk_multiplier": 1.0, "params": dict(PIVOT_DAILY)},
        {"name": "pivot_bounce", "risk_multiplier": 1.0, "params": dict(PIVOT_WEEKLY)},
        {"name": "pivot_bounce", "risk_multiplier": 1.0, "params": dict(PIVOT_MONTHLY)},
        {"name": "ema20_pullback_m15", "risk_multiplier": ema_rm, "params": dict(EMA20)},
    ]
    if eng_rm is not None:
        members.append({"name": "engulfing_reversal", "risk_multiplier": eng_rm, "params": dict(ENGULFING)})
    cfg["strategy"] = {
        "__replace__": True,
        "name": "ensemble",
        "params": {"__replace__": True, "members": members},
    }
    return cfg


def run_trial(cfg_path: Path, csv: Path) -> TrialResult:
    res = subprocess.run(
        ["python3", "scripts/quick_eval.py", "--config", str(cfg_path), "--csv", str(csv)],
        capture_output=True, text=True, timeout=180,
    )
    secs = parse_quick_eval(res.stdout)
    val = secs.get("VALIDATION") or {}
    full = secs.get("FULL") or {}
    tourn = secs.get("TOURNAMENT") or {}
    return TrialResult(
        config_path=str(cfg_path),
        val_return=val.get("return_pct") or 0.0,
        val_pf=val.get("profit_factor") or 0.0,
        val_dd=val.get("max_drawdown_pct") or 0.0,
        val_trades=int(val.get("trades") or 0),
        val_cap_viol=int(val.get("cap_violations") or 0),
        full_return=full.get("return_pct"),
        full_pf=full.get("profit_factor"),
        full_cap_viol=int(full.get("cap_violations") or 0) if full.get("cap_violations") is not None else None,
        tourn_return=tourn.get("return_pct"),
        tourn_pf=tourn.get("profit_factor"),
        tourn_cap_viol=int(tourn.get("cap_violations") or 0) if tourn.get("cap_violations") is not None else None,
    )


def val_score(t: TrialResult) -> float:
    """Composite validation objective.

    Objective:
      - HARD: val_cap_viol == 0 (else disqualify)
      - PRIMARY: maximize val_return × val_pf  (favours strong AND consistent)
      - PENALTY: scale down by max(1, val_dd / 25)
                 (DDs > 25% are increasingly penalised)
    """
    if t.val_cap_viol != 0:
        return -1e9
    # HARD constraint: cap violations on the FULL window mean the
    # config breached leverage caps somewhere in the dataset. Plan
    # §A kill-switch — disqualify regardless of validation. (We use
    # full cap as a binary feasibility flag; we do NOT use full
    # return/PF for selection.)
    if t.full_cap_viol is not None and t.full_cap_viol != 0:
        return -1e9
    if t.val_pf <= 0 or t.val_trades < 5:
        return -1e8
    dd_penalty = max(1.0, abs(t.val_dd) / 25.0)
    return (t.val_return * t.val_pf) / dd_penalty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    ap.add_argument("--out-dir", default="config/iter33/sweep")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Bounded grid: 16 trials max.
    risks = [8.0, 10.0]
    dmls = [2.5, 3.0]
    ema_rms = [0.2, 0.3]
    eng_rms = [None, 0.2]    # None = no engulfing member
    grid = list(product(risks, dmls, ema_rms, eng_rms))
    assert len(grid) <= 16, f"grid too large: {len(grid)}"
    print(f"# Trials: {len(grid)} (validation-only selection)")

    results: list[TrialResult] = []
    for i, (risk, dml, ema_rm, eng_rm) in enumerate(grid):
        cfg = make_config(risk, dml, ema_rm, eng_rm)
        eng_tag = f"eng{int(eng_rm*100)}" if eng_rm is not None else "no_eng"
        name = f"trial_r{int(risk)}_dml{str(dml).replace('.','')}_em{int(ema_rm*100):02d}_{eng_tag}"
        cfg_path = out_dir / f"{name}.yaml"
        with cfg_path.open("w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        t = run_trial(cfg_path, Path(args.csv))
        results.append(t)
        # Print VAL only — never print tournament during selection.
        flag = "OK" if t.val_cap_viol == 0 else "CAP-VIOL"
        print(f"[{i+1:2d}/{len(grid)}] {name:<45} val ret={t.val_return:+7.2f} PF={t.val_pf:5.2f} DD={t.val_dd:+7.2f} n={t.val_trades:3d} {flag}")

    # Rank by val score.
    ranked = sorted(results, key=val_score, reverse=True)
    print("\n## Validation-only ranking (top 5)")
    print(f"{'rank':<5} {'config':<50} {'val_ret':>9} {'val_PF':>7} {'val_DD':>8} {'score':>10}")
    for i, t in enumerate(ranked[:5], 1):
        print(f"#{i:<4} {Path(t.config_path).stem:<50} {t.val_return:>+9.2f} {t.val_pf:>7.2f} {t.val_dd:>+8.2f} {val_score(t):>+10.2f}")

    # Single tournament read AT END.
    winner = ranked[0]
    print("\n## SINGLE-SHOT FINAL READ (validation-selected winner)")
    print(f"Headline: {winner.config_path}")
    print(f"  Validation:  ret={winner.val_return:+7.2f}% PF={winner.val_pf:.2f} DD={winner.val_dd:+7.2f}% n={winner.val_trades} cap={winner.val_cap_viol}")
    if winner.full_return is not None:
        print(f"  Full:        ret={winner.full_return:+7.2f}% PF={winner.full_pf:.2f} cap={winner.full_cap_viol}")
    if winner.tourn_return is not None:
        print(f"  Tournament:  ret={winner.tourn_return:+7.2f}% PF={winner.tourn_pf:.2f} cap={winner.tourn_cap_viol}  ← single read; not used for selection")


if __name__ == "__main__":
    main()
