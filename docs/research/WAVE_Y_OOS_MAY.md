# Wave Y — post-Apr 2026 OOS extension

**Date:** 2026-05-22  
**Training CSV:** `data/xauusd_m1_2026.csv` (ends ~2026-04-24)  
**OOS CSV:** `data/xauusd_m1_2026_oos.csv` (gitignored; fetch locally)

## Fetch OOS slice

```bash
python3 -m ai_trader.scripts.fetch_dukascopy \
  --symbol XAUUSD --timeframe M1 \
  --start 2026-04-25 --end 2026-05-20 \
  --out data/xauusd_m1_2026_oos.csv
```

## Run validation

```bash
# In-sample split + OOS extension
python3 scripts/wave_y_heldout_validate.py \
  --csv data/xauusd_m1_2026.csv \
  --oos-csv data/xauusd_m1_2026_oos.csv

# Quick harness on OOS file only
python3 scripts/iter32_compare_configs.py --csv data/xauusd_m1_2026_oos.csv \
  config/simulation/wave_y_iter244_primary.yaml \
  config/simulation/wave_y_iter235_tail.yaml \
  config/simulation/wave_y_iter227_april_sleeve.yaml
```

## Simulation entrypoints

| Role | YAML |
|------|------|
| Primary 4/4 | `config/simulation/wave_y_iter244_primary.yaml` |
| Tail mode | `config/simulation/wave_y_iter235_tail.yaml` |
| April sleeve | `config/simulation/wave_y_iter227_april_sleeve.yaml` |

## Results (2026-05-22, Dukascopy Apr26–May20)

OOS file has **no rolling windows** (slice too short); metrics are full-slice backtests only.

### OOS full (late Apr + May)

| Config | ret % | PF | cap | Apr % (partial) |
|--------|------:|---:|:---:|----------------:|
| rollwin | −19.8 | 0.39 | 0 | −17.0 |
| iter235 | −18.9 | 0.35 | 0 | −14.3 |
| iter244 | −17.7 | 0.36 | 0 | −14.3 |
| iter227 | −2.8 | — | 0 | n/a |

### May-only (2026-05-01+, true OOS month)

| Config | ret % | PF | cap |
|--------|------:|---:|:---:|
| rollwin | −3.0 | 0.79 | 0 |
| iter235 | −3.5 | 0.78 | 0 |
| iter244 | −3.0 | 0.79 | 0 |
| iter227 | −2.8 | — | 0 |

**Verdict:** Frozen Jan–Apr picks **do not generalize** to this post-Apr slice without
re-tuning or regime gating. **iter227** is least negative on May-only but still losing.
Do **not** promote Wave Y configs to live on OOS evidence alone — extend research on May+
conditions or wait for more OOS bars before re-harnessing.

Reproduce::

    python3 scripts/wave_y_oos_compare.py --csv data/xauusd_m1_2026_oos.csv
