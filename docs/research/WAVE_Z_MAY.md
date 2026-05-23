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

## Wave Z vol gate (iter273–277, 2026-05-22)

New causal **`vol_risk_cap_gate_enabled`** on `adaptive_router`: M1 ATR band sets
`active_risk_multiplier_cap` per bar (high vol → lower cap).

### May-only OOS

| Config | May ret % | PF |
|--------|----------:|---:|
| iter268 (static cap 0.70) | **−1.5** | 0.86 |
| **iter274** (tiered 0.65/0.85/1.0) | **−2.1** | 0.81 |
| iter273 (high 0.70 only) | −2.7 | 0.76 |
| iter244 | −3.0 | 0.79 |

### Train CSV

| Config | Mar % | wpass | worst |
|--------|------:|:-----:|------:|
| iter244 | +18.5 | 4/4 | ~0.34 |
| iter273 | **+22.9** | 4/4 | ~2.14 |
| iter274 | +18.3 | 4/4 | ~2.11 |
| iter268 | +11.4 | 3/4 | ~0.65 |

**Pick:** **iter268** for best May OOS; **iter274** if you want **dynamic** cap + **4/4** on train (worse tail than 244).
**iter244** remains primary for tail/Mar Pareto on train slice.

## iter278–282 — chop overlap + stand-down (2026-05-22)

| Iter | Change | May OOS | Train wpass | worst | Verdict |
|------|--------|--------:|:-----------:|------:|---------|
| iter268 | cap 0.70 | −1.5% | 3/4 | ~0.65 | May sleeve |
| iter274 | vol gate tiered | −2.1% | 4/4 | ~2.11 | Dynamic cap |
| 278–279, 281–282 | stand-down / max2 | ≈274/268 | — | — | no-op on May |
| **280** | chop **overlap** + vol gate | **+0.9%** | **4/4** | **~3.43** | **First positive May OOS** |

Engine: `chop_vol_stand_down_enabled` (causal mid-vol + low M15 persistence → skip bar).

**iter280** is the **May+ positive** candidate; tail on train slice is high (~3.43) — use as
**seasonal sleeve** with **iter244** primary, not a full replacement.

Simulation: `config/simulation/wave_y_iter280_may_positive.yaml`

## Extended May (combined CSV through 2026-05-22)

`python3 scripts/build_combined_m1_csv.py` + `wave_y_monthly_compare.py`:

| Config | May 2026 (full month) |
|--------|---------------------:|
| iter244 | −2.2% |
| iter268 | −0.6% |
| **iter280** | **+1.7%** |

Calendar policy: `docs/research/WAVE_Y_CALENDAR.md`, `scripts/wave_y_resolve_config.py`.
