# Iter33 — Mar/Apr pivot filters (2026-04-26)

Harness: default `build_rolling_windows` on `data/xauusd_m1_2026.csv`.

## `protector_conc1_dw_m15_adx30.yaml`

M15 ADX gate (`adx_max: 30`) on **daily + weekly** `pivot_bounce` only.
Monthly and 4h members unchanged vs iter29 `v4_plus_h4_protector_conc1`.

| metric | baseline conc1 | dw M15 adx30 |
|--------|----------------:|-------------:|
| full % | ~832 | ~232 |
| Mar % | ~77 | ~58 |
| Apr % | ~31 | **~54** |
| rolling wins | 3/4 | 2/4 |
| worst_score | ~1.35 | ~8.08 |
| cap violations (full) | 0 | 0 |

**Sensitivity (same gate, sweep `adx_max`):** 26 → cap + poor April; 28 → cap hits;
**30** clean; **32** cap violations on full run; 34 lower April.

Reproduce:

```bash
python3 scripts/iter32_compare_configs.py --csv data/xauusd_m1_2026.csv \
  config/iter29/v4_plus_h4_protector_conc1.yaml \
  config/iter33/protector_conc1_dw_m15_adx30.yaml
```

Broader scripted sweep: `python3 scripts/iter33_mar_apr_sweep.py` (writes
`artifacts/iter33_sweep_stdout.jsonl`).

## Combination tried (falsified)

**Same dw M15 adx30 + iter30 v55 DD throttle** (`daily_max_loss_pct: 5`,
soft/hard DD 10/15 with 0.60/0.30 multipliers): full ~108%, Apr ~24%,
rolling **1/4** — worse than dw-only on this slice; not pursued.
