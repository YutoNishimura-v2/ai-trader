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

## Results

_(filled after harness run)_
