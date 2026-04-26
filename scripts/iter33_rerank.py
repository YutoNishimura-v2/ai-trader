"""Re-rank iter33 trials adding full-cap-viol as a hard constraint.

Re-runs quick_eval on each existing trial config (cheap because
results are deterministic) and re-applies the validation-only
score with the additional kill-switch.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.iter33_val_only_sweep import (  # type: ignore
    run_trial, val_score, parse_quick_eval,
)


def main():
    sweep_dir = Path("config/iter33/sweep")
    csv = Path("data/xauusd_m1_2026.csv")
    results = []
    for cfg in sorted(sweep_dir.glob("trial_*.yaml")):
        t = run_trial(cfg, csv)
        results.append(t)
        s = val_score(t)
        flag_v = "OK" if t.val_cap_viol == 0 else "VAL-CAP"
        flag_f = "OK" if (t.full_cap_viol or 0) == 0 else "FULL-CAP"
        print(f"{cfg.stem:<45} val={t.val_return:+7.2f}/{t.val_pf:.2f}/DD{t.val_dd:+6.2f} fullcap={t.full_cap_viol} score={s:>+10.2f} [{flag_v}, {flag_f}]")
    ranked = sorted(results, key=val_score, reverse=True)
    print("\n## Validation-only ranking (with full-cap kill-switch)")
    print(f"{'rank':<5} {'config':<50} {'val_ret':>9} {'val_PF':>7} {'val_DD':>8} {'fullcap':>8} {'score':>10}")
    for i, t in enumerate(ranked[:5], 1):
        print(f"#{i:<4} {Path(t.config_path).stem:<50} {t.val_return:>+9.2f} {t.val_pf:>7.2f} {t.val_dd:>+8.2f} {t.full_cap_viol or 0:>8d} {val_score(t):>+10.2f}")
    winner = ranked[0]
    print("\n## SINGLE-SHOT FINAL READ (validation-selected winner)")
    print(f"Headline: {winner.config_path}")
    print(f"  Validation:  ret={winner.val_return:+7.2f}% PF={winner.val_pf:.2f} DD={winner.val_dd:+7.2f}% n={winner.val_trades} cap={winner.val_cap_viol}")
    print(f"  Full:        ret={winner.full_return:+7.2f}% PF={winner.full_pf:.2f} cap={winner.full_cap_viol}")
    print(f"  Tournament:  ret={winner.tourn_return:+7.2f}% PF={winner.tourn_pf:.2f} cap={winner.tourn_cap_viol}  ← single read")


if __name__ == "__main__":
    main()
