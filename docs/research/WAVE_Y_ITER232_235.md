# Wave Y — regime handoff research (iter232–235)

**Date:** 2026-05-22  
**CSV:** `data/xauusd_m1_2026.csv`  
**Tool:** `scripts/iter32_compare_configs.py`  
**Baseline:** `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml`

## Thesis family

Combine **proven rollwin pivots** with **wave_structure_mtf** in a *conservative* way:
no standalone 6% wave, no moonshot grids — only structural routing changes inside
`adaptive_router`.

| Iter | Idea | Verdict |
|------|------|---------|
| **232** | Wave as **third** member (transition), 0.42×, after pivots | **FALSIFIED** — cap=1, Apr −17.6%, 0/4 |
| **233** | **Handoff:** chop→range only, wave→transition only | Mar/Apr ↑, **worst ~2.19** — partial |
| **234** | 232 + UTC 13–14 block on chop | **FALSIFIED** — cap=1 |
| **235** | 233 + wave **0.30×**, TP2 2.8, cd 14 | **PROMISING** — see below |

## Headline numbers

| Config | full % | Mar % | Apr % | wpass | worst_score | cap |
|--------|-------:|------:|------:|:-----:|------------:|:---:|
| rollwin | +232.7 | +0.83 | +1.28 | 2/4 | **~0.10** | 0 |
| iter227 (wave alone) | +26.5 | +3.57 | +20.48 | 2/4 | ~1.64 | 0 |
| iter232 | +60.3 | +9.41 | −17.59 | 0/4 | DQ | 1 |
| iter233 | +186.6 | +12.01 | +6.75 | 2/4 | ~2.19 | 0 |
| iter234 | +243.8 | +2.07 | +2.88 | 2/4 | ~1.41 | 1 |
| **iter235** | **+182.6** | **+11.93** | **+6.93** | **3/4** | **~0.04** | **0** |

## iter235 — recommended simulation follow-up

**Config:** `config/research_aspiration_200/iter235_rollwin_handoff_wave_lowrisk.yaml`

Why it matters under `docs/plan.md` Mar/Apr priority:

- **March and April** both improve sharply vs rollwin without cap violations.
- Rolling harness: **3/4** window passes (vs rollwin 2/4) and **lower worst_score**.
- Full-period return is lower than rollwin because Jan/Feb moonshot chop is less
  dominant — acceptable if deployment target is recent difficult months.

**Not a live promotion** until re-checked on held-out windows and demo plumbing.

## Next probes (single-thesis)

1. iter236: iter235 with `block_hours_utc: [13,14]` on **range** chop only.
2. iter237: wave `sr_touch_atr: 1.72` at 0.30× (iter229 peel inside handoff).
3. Compare vs `iter227` for **April-only** sleeve (iter227 still wins Apr %).
