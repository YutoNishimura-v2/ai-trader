# Wave Z — May+ / post-Apr OOS research

**Date:** 2026-05-22  
**Primary metric:** `data/xauusd_m1_2026_oos.csv` (Apr26–May20), especially **May-only** slice.  
**Baseline:** Wave Y frozen picks failed May OOS (~−3% each) — see `WAVE_Y_OOS_MAY.md`.

## Thesis

Post-Apr chop needs **defensive** structural changes, not iter244 micro-peels:

| Iter | Idea |
|------|------|
| **266** | Lower wave risk (0.20×), less chop moonshot (TP2 6, max 3/day) |
| **267** | Router **probe** + real eligibility hysteresis |
| **268** | Cap router risk multiplier at **0.70** |
| **269** | Minimal wave sleeve (0.15×, cd 22) |
| **270** | Wider lunch block **13–16** UTC on chop |

## Evaluation

```bash
python3 scripts/wave_y_oos_compare.py --csv data/xauusd_m1_2026_oos.csv \
  config/research_aspiration_200/iter266_wavez_defensive_wave20.yaml \
  config/research_aspiration_200/iter267_wavez_probe_eligibility.yaml \
  config/research_aspiration_200/iter268_wavez_cap070.yaml \
  config/research_aspiration_200/iter269_wavez_wave015_cd22.yaml \
  config/research_aspiration_200/iter270_wavez_block13141516.yaml
```

Also re-check training CSV Mar/Apr so we do not sacrifice iter244 4/4:

```bash
python3 scripts/iter32_compare_configs.py --csv data/xauusd_m1_2026.csv \
  config/research_aspiration_200/iter244_handoff_overlap_lunchblock.yaml \
  config/research_aspiration_200/iter266_wavez_defensive_wave20.yaml \
  ...
```

## Results (2026-05-22)

### May-only OOS (`xauusd_m1_2026_oos.csv`, 2026-05-01+)

| Config | ret % | PF | vs iter244 |
|--------|------:|---:|------------|
| iter244 | −3.0 | 0.79 | baseline |
| iter227 | −2.8 | — | ≈ tie |
| **iter268** (cap **0.70**) | **−1.5** | **0.86** | **best** |
| iter271 (cap 0.75) | −2.0 | 0.82 | better |
| iter272 (cap 0.80) | −3.6 | 0.71 | worse |
| iter266–270 (other) | −3.0 | 0.79 | no-op or worse |

### Training CSV (Jan–Apr) — do not drop iter244 for iter268

| Config | full % | Mar % | Apr % | wpass | worst |
|--------|-------:|------:|------:|:-----:|------:|
| **iter244** | **+192.3** | **+18.5** | **+7.7** | **4/4** | **~0.34** |
| iter268 | +114.2 | +11.4 | +4.7 | 3/4 | ~0.65 |
| iter266 | +171.9 | +18.4 | +24.8* | 4/4 | ~0.34 |

\*iter266 Apr spike on train slice; **no** May OOS lift.

## Picks (three-config)

| Role | YAML |
|------|------|
| Primary (train / 4/4) | `iter244_handoff_overlap_lunchblock.yaml` |
| Tail harness | `iter235_rollwin_handoff_wave_lowrisk.yaml` |
| April train sleeve | `iter227_wave_x_wave_structure_223_tp35.yaml` |
| **May+ OOS sleeve** | `iter268_wavez_cap070.yaml` / `config/simulation/wave_y_iter268_may_sleeve.yaml` |

Still **no live promotion** — May OOS remains **negative** even for iter268; cap sleeve only reduces loss.
