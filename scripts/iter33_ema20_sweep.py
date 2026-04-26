"""iter33 ema20 family validation-only sweep.

Same discipline as iter33_val_only_sweep but on the ema20×M15
family (the user's article recipe). Bounded grid ≤16 trials.
"""
from __future__ import annotations
import argparse, sys, yaml
from pathlib import Path
from itertools import product

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.iter33_val_only_sweep import (  # type: ignore
    run_trial, val_score,
)


BASE = {
    "extends": "../../default.yaml",
    "instrument": {
        "symbol": "XAUUSD", "timeframe": "M1", "contract_size": 100,
        "tick_size": 0.01, "tick_value": 1.0, "quote_currency": "USD",
    },
    "risk": {
        "risk_per_trade_pct": 2.0,
        "daily_max_loss_pct": 10.0,
        "daily_profit_target_pct": 30.0,
        "withdraw_half_of_daily_profit": False,
        "max_concurrent_positions": 1,
        "lot_cap_per_unit_balance": 0.000020,
    },
}


def main():
    csv = Path("data/xauusd_m1_2026.csv")
    out = Path("config/iter33/ema20_sweep")
    out.mkdir(parents=True, exist_ok=True)

    htfs = ["H1", "H4"]
    sessions = ["london", "london_or_ny"]
    touches = [0.5, 0.8]
    sb = [8, 12]
    grid = list(product(htfs, sessions, touches, sb))
    print(f"# Trials: {len(grid)} (validation-only)")

    results = []
    for i, (htf, sess, tch, swing) in enumerate(grid):
        cfg = yaml.safe_load(yaml.safe_dump(BASE))
        cfg["strategy"] = {
            "__replace__": True,
            "name": "ema20_pullback_m15",
            "params": {
                "ema_period": 20, "confirm_bars": 2, "touch_dollar": tch,
                "sl_buffer_dollar": 1.0, "tp_buffer_dollar": 1.0,
                "swing_lookback_bars": swing, "max_sl_dollar": 6.0,
                "tp1_rr": 1.0, "leg1_weight": 0.5,
                "cooldown_m15_bars": 4, "session": sess,
                "max_trades_per_day": 6, "htf": htf, "htf_ema_period": 20,
            },
        }
        name = f"ema20_{htf}_{sess}_t{int(tch*100)}_sb{swing}"
        cfg_path = out / f"{name}.yaml"
        with cfg_path.open("w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        t = run_trial(cfg_path, csv)
        results.append(t)
        flag = "OK" if t.val_cap_viol == 0 else "CAP"
        print(f"[{i+1:2d}/{len(grid)}] {name:<45} val ret={t.val_return:+7.2f} PF={t.val_pf:5.2f} DD={t.val_dd:+7.2f} n={t.val_trades:3d} {flag}")

    ranked = sorted(results, key=val_score, reverse=True)
    print("\n## Top 5 by val score")
    for i, t in enumerate(ranked[:5], 1):
        s = val_score(t)
        print(f"#{i} {Path(t.config_path).stem:<45} val={t.val_return:+7.2f}/{t.val_pf:.2f}/DD{t.val_dd:+6.2f} score={s:>+10.2f}")

    winner = ranked[0]
    print("\n## SINGLE FINAL READ")
    print(f"Headline: {winner.config_path}")
    print(f"  Val:    ret={winner.val_return:+7.2f}% PF={winner.val_pf:.2f}")
    print(f"  Full:   ret={winner.full_return:+7.2f}% PF={winner.full_pf:.2f} cap={winner.full_cap_viol}")
    print(f"  Tourn:  ret={winner.tourn_return:+7.2f}% PF={winner.tourn_pf:.2f}  ← single read")


if __name__ == "__main__":
    main()
