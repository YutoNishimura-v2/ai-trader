# Iter30 configs

This directory holds every iter30 candidate evaluated through the
new rolling-window stability harness
(`ai_trader/research/stability.py`).

Each config is an `adaptive_router` strategy variant. They are
chained via YAML `extends:` so a one-line override produces a new
candidate that reuses everything else from its parent.

## Headline configs

| config | wins/4 | full% | Jan% | PF | cap_viol | label |
|---|---:|---:|---:|---:|---:|---|
| `adaptive_v55_v43b_dml5.yaml` | 2/4 | +1167 | **+257** | 1.50 | 0 | **3x-month winner** |
| `adaptive_v43_v36_loose_dd.yaml` | 4/4 | +441 | +159 | 1.40 | 0 | 4/4 generalization leader |
| `adaptive_v36_v31_4h_s2r2.yaml` | 4/4 | +375 | +154 | 1.37 | 0 | 4/4 + S2/R2 4h |
| `adaptive_v31_v29_4h_mwt.yaml` | 4/4 | +444 | +110 | 1.46 | 0 | 4/4 generalization first hit (Mon-Thu unlock) |
| `adaptive_v32_v31_levels.yaml` | 4/4 | +308 | +117 | 1.37 | 0 | 4/4 + frequency boost |
| `adaptive_v29_static_clone.yaml` | 3/4 | +832 | +116 | 1.56 | 0 | iter29 protector reproduced through router |

## How the router was tuned (lineage)

1. **`adaptive_v1.yaml`** — initial roster (5 members including
   `bos_retest_scalper` and `session_sweep_reclaim`). Probe-by-default
   was too cautious; full -19%, 1/4 wins.
2. **`adaptive_v2..v6`** — narrowed roster to 4 pivot members
   matching iter29 protector. Started in `active` state. v6 hit
   2/4 wins, full +476%, Jan +99.
3. **`adaptive_v22_all_levels.yaml`** — enabled `use_s2r2=true` on
   every pivot member. Jan +160 but only 0/4 wins (Mar -13).
4. **`adaptive_v29_static_clone.yaml`** — router as a perfect
   reproduction of iter29 protector_conc1. Confirmed the router
   infrastructure is correct (same trades, same numbers).
5. **`adaptive_v31_v29_4h_mwt.yaml`** — added `weekdays: [0,1,2,3]`
   to the 4h member to align with the rest of the stack. **First
   4/4 generalization** on the rolling battery.
6. **`adaptive_v36_v31_4h_s2r2.yaml`** — re-enabled `use_s2r2=true`
   on the 4h member. Still 4/4 wins; Jan lifted to +154.
7. **`adaptive_v43_v36_loose_dd.yaml`** — loosened the DD throttle
   (soft 12 → 15, hard 22 → 25). 4/4 wins, full +441, Jan +159.
8. **`adaptive_v55_v43b_dml5.yaml`** — lifted `daily_max_loss_pct`
   from 4 to 5 and tightened the DD throttle to soft=10/hard=15.
   2/4 wins (lost W3/W4 sign agreement), but **Jan +257% with
   cap_violations=0 across all reported windows AND the full
   period** — meets the user's hard 100k → 300k single-month gate.

## Falsified variants

The remaining ~45 files in this directory are exploratory variants
that did not improve on the parent on either gate. Each is labelled
in its top-of-file comment with the lever it tested and the result.
Notable falsifications:

- **risk_per_trade=11 or 12** — structurally breaches the -10.5%
  cap_violations threshold on the first-of-day SL. See `v9, v25,
  v42, v45, v49`.
- **Aggressive intra-day pyramid** — amplifies losing days as much
  as winning days. See `v13, v18, v40, v47`.
- **More members (BOS, sweep_reclaim)** — drags PF down. See `v11,
  v17, v21, v28`.
- **Concurrency=2** — double-up on parallel SL hits trips cap. See
  `v7, v20`.

## Run instructions

```bash
# Single-config evaluation through the rolling battery
python3 scripts/iter30_stability.py \
  --csv data/xauusd_m1_2026.csv \
  --label iter30-headline \
  --config config/iter30/adaptive_v55_v43b_dml5.yaml

# Bounded grid sweep
python3 scripts/iter30_sweep.py \
  --csv data/xauusd_m1_2026.csv \
  --base config/iter30/adaptive_v43_v36_loose_dd.yaml \
  --label mi-test \
  --grid "risk.daily_max_loss_pct=4,5,6" \
  --max-trials 4
```

Both scripts append to `artifacts/iter30/stability/audit.jsonl` for
post-hoc auditing of which test windows were opened with which
config hash.
