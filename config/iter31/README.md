# Iter31 / Iter32 research notes

Dataset: `data/xauusd_m1_2026.csv` (Dukascopy M1, 2026-01-01 .. 2026-04-24),
rolling stability harness default windows (4 triples).

## Finding: v55 flat sizing makes buckets a no-op

`adaptive_v55_v43b_dml5.yaml` sets `active_risk_multiplier_floor == cap` and
`eligibility_off_threshold` very negative, so **bucket keys do not change
behavior** vs `adaptive_v55_v43b_dml5_buckets.yaml` (bit-identical monthly
returns in a full harness run).

## Best new candidate vs static v55 headline

`adaptive_v55_expectancy_sizing_b.yaml` — same risk as v55, but **probe start**
and **0.55–1.0** active sizing with `eligibility_off_threshold: -0.08`.

| config | full % | PF | Apr % | Mar % | rolling wins | worst_score |
|--------|-------:|---:|------:|------:|:-------------:|------------:|
| iter30 `adaptive_v55_v43b_dml5` | ~1166 | 1.50 | **-3.55** | 68.9 | 2/4 | ~1.87 |
| iter31 `adaptive_v55_expectancy_sizing_b` | ~905 | 1.58 | **+1.82** | 48.2 | **4/4** | ~0.43 |

Still below iter29 `v4_plus_h4_protector_conc1` April (~+31%) on this slice.

## Rejected in this pass

- `adaptive_v55_expectancy_buckets.yaml` (expectancy sizing + buckets): April
  ~-2.7%, rolling 1/4 — **do not use** without redesign.

Reproduce:

```bash
python3 scripts/iter32_compare_configs.py --csv data/xauusd_m1_2026.csv \
  config/iter30/adaptive_v55_v43b_dml5.yaml \
  config/iter31/adaptive_v55_expectancy_sizing_b.yaml
```

**Iter33 (Apr uplift on static ensemble):** see [`config/iter33/README.md`](../iter33/README.md)
and `protector_conc1_dw_m15_adx30.yaml` (M15 ADX gate on daily+weekly only).
