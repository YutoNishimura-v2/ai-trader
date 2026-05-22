# Wave Y — two-config simulation sleeve (not one YAML)

**Date:** 2026-05-22  
**Status:** Documentation only — do **not** merge iter227 into iter244/235.

## Why two configs

| Config | Role | Strength | Weakness |
|--------|------|----------|----------|
| **iter244** | Primary handoff (range→pivot, transition→overlap wave, lunch block) | **4/4** rolling harness, strong **Mar+Apr** combo, tail ~0.34 | April alone below iter227 |
| **iter235** | Conservative handoff (same routing, no lunch block) | Best **worst_score** (~0.04) | **3/4** windows |
| **iter227** | Standalone Wave X runner | Best **April** (~+20.5%) on slice | Weak full period, harness ~1.64 |

Merging iter227 into the adaptive_router shell has been **falsified** (iter232 cap=1, iter231 no lift).
Use **separate YAMLs** and switch by calendar regime or month policy in the demo runner — not a single merged strategy block.

## Recommended simulation policy

1. **Default:** `iter244_handoff_overlap_lunchblock.yaml` for Mar/Apr-first work and 4/4 stability target.
2. **Tail-risk mode:** `iter235_rollwin_handoff_wave_lowrisk.yaml` when minimizing rolling `worst_score` matters more than 4/4.
3. **April sleeve (optional):** run `iter227_wave_x_wave_structure_223_tp35.yaml` in parallel for April-only comparison; promote only if April uplift justifies worse full-period / harness trade-off.

## Harness reminder (Jan–Apr 2026 M1, full CSV)

| Config | Mar % | Apr % | wpass | worst |
|--------|------:|------:|:-----:|------:|
| iter244 | +18.5 | +7.7 | 4/4 | ~0.34 |
| iter235 | +11.9 | +6.9 | 3/4 | ~0.04 |
| iter227 | +3.6 | **+20.5** | 2/4 | ~1.64 |

## Forward held-out (Mar–Apr slice only, split at 2026-03-01)

Run: `python3 scripts/wave_y_heldout_validate.py`

| Config | slice ret % | Mar % | Apr % | wpass (2 win) |
|--------|------------:|------:|------:|:-------------:|
| rollwin | −6.2 | −6.5 | +0.4 | 1/2 |
| iter235 | +10.8 | +4.7 | +5.8 | 0/2 |
| **iter244** | +11.2 | +5.6 | +5.3 | 0/2 |
| **iter227** | **+23.8** | +3.1 | **+20.0** | **2/2** |

On the **held-out calendar window**, iter227 leads April and slice return; iter244/235 still
positive but with **weaker** Mar/Apr than full-period monthly keys (tuning context included Jan–Feb).
Use **two-config** policy: **iter244** for full-sample 4/4 stability; **iter227** optional April sleeve.

## Post-Apr OOS (2026-05-22)

Fetched `data/xauusd_m1_2026_oos.csv` (Apr26–May20). **May-only returns are negative**
for iter244/235/227 (~−3% each). See `docs/research/WAVE_Y_OOS_MAY.md`.

Jan–Apr picks remain valid **on the training slice**; live promotion requires May+
re-validation or explicit regime switch — not automatic from iter244 4/4 alone.

## Live / demo

Still blocked on **news_fade** for walk-forward gates. Use `config/simulation/wave_y_*.yaml`
for paper runs. MT5 demo plumbing: `docs/live/VPS_HFM_DEMO.md`.
