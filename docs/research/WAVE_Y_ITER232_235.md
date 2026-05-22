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

## Wave Y continued (iter236–241, 2026-05-22)

| Iter | Change | full % | Mar % | Apr % | wpass | worst | cap | Verdict |
|------|--------|-------:|------:|------:|:-----:|------|:---:|---------|
| 235 | handoff baseline | +182.6 | +11.93 | +6.93 | 3/4 | **~0.04** | 0 | **Balanced PROMISING** |
| 236 | lunch block 13–14 chop | +192.4 | **+19.35** | +7.70 | **4/4** | ~1.40 | 0 | Mar hero, tail fail |
| 237 | wave SR 1.72 | +182.6 | +11.93 | +6.93 | 3/4 | ~0.04 | 0 | no-op vs 235 |
| 238 | wave TP2 3.5 @ 0.28× | +186.0 | +11.14 | +7.56 | 2/4 | ~1.40 | 0 | FALSIFIED vs 235 |
| 239 | router risk 7% | +142.1 | +12.76 | +4.41 | 1/4 | ~2.57 | 0 | FALSIFIED |
| 240 | 236 + wave 0.22× TP2 2.6 | +194.3 | +18.34 | +7.82 | **4/4** | ~0.84 | 0 | 4/4 peel; tail still >>235 |
| 241 | block 13–14–15 chop | +124.6 | +17.99 | **+8.15** | **4/4** | ~1.40 | 0 | Apr↑, full↓, tail fail |

**Takeaway:** Two Pareto styles —

- **`iter235`** — best **worst_score** + solid Mar/Apr (conservative default).
- **`iter236` / `iter240`** — best **Mar** and **4/4** windows; use only if you accept **~0.8–1.4** rolling tail.

## iter242–245 (2026-05-22)

| Iter | Change | full % | Mar % | Apr % | wpass | worst | cap |
|------|--------|-------:|------:|------:|:-----:|------|:---:|
| 242 | wave overlap only | +186.5 | +10.83 | +7.32 | 3/4 | ~0.039 | 0 |
| 243 | wave RCI off | +183.4 | +12.53 | −2.05 | 1/4 | ~8.35 | 0 |
| **244** | lunch block + wave overlap | **+192.3** | **+18.50** | **+7.70** | **4/4** | **~0.34** | 0 |
| 245 | 244 + wave 0.25× | +187.2 | +17.67 | +6.72 | 4/4 | ~0.34 | 0 |

**Pick:** **`iter235`** — best tail (~0.04). **`iter244`** — best **4/4** + Mar/Apr with tail ~0.34.

## iter246–260 (PRs #93–#95)

Member-param peels (wave risk/TP/block, cd/SR/session, pivot, zigzag): mostly **no-op** or
**tail regression**. **iter253** proved **wave `session: overlap`** is required for ~0.34 tail.

## iter261–265 — router ADX boundaries (2026-05-22)

| Iter | Change | full % | Mar % | Apr % | wpass | worst | cap | Verdict |
|------|--------|-------:|------:|------:|:-----:|------:|:---:|---------|
| 244 | baseline | +192.3 | +18.50 | +7.70 | **4/4** | **~0.34** | 0 | **Pareto 4/4** |
| 261 | range_adx_max 18 | +149.6 | +15.33 | **+24.91** | 4/4 | ~4.57 | 0 | FALSIFIED (tail) |
| 262 | range_adx_max 22 | +257.3 | +2.94 | +6.69 | 2/4 | ~3.36 | **2** | FALSIFIED |
| 263 | trend_adx_min 23 | +156.8 | **+34.77** | −4.76 | 2/4 | ~5.79 | 0 | FALSIFIED |
| 264 | trend_adx_min 27 | +196.2 | +15.45 | +3.12 | 4/4 | ~0.35 | 0 | ≈244, Mar/Apr↓ |
| 265 | range18 + trend27 | +155.2 | +13.34 | +17.65 | 4/4 | ~3.05 | 0 | FALSIFIED (tail) |

**Takeaway:** Default **20 / 25** ADX cutoffs are near-optimal for **iter244**. Widening transition
(more wave) hurts tail; **iter264** is the only near-tie on worst but trades away Mar/Apr.

**Picks unchanged:** **iter235**, **iter244**.

## Held-out validation (2026-05-22)

Script: `scripts/wave_y_heldout_validate.py` — split at **2026-03-01** (jan-feb vs mar-apr).

**Mar–Apr slice (held-out calendar window):**

| Config | ret % | Mar % | Apr % | wpass |
|--------|------:|------:|------:|:-----:|
| iter244 | +11.2 | +5.6 | +5.3 | 0/2 |
| iter235 | +10.8 | +4.7 | +5.8 | 0/2 |
| iter227 | **+23.8** | +3.1 | **+20.0** | **2/2** |
| rollwin | −6.2 | −6.5 | +0.4 | 1/2 |

Full-period Mar/Apr keys (**iter244** +18.5% / +7.7%) include Jan–Feb routing context; held-out
slice numbers are the honest forward view for the priority months. **iter227** wins the held-out
window — see `docs/research/WAVE_Y_TWO_CONFIG.md`.

## Wave Y status

Micro-peels on **iter244** (iter246–265) are **closed**. Frozen simulation picks:

- **iter235** — best rolling tail (~0.04), 3/4
- **iter244** — best full-sample **4/4** + Mar/Apr combo, tail ~0.34
- **iter227** — optional **April sleeve** (separate YAML; do not merge into router)

## Next probes

1. Fetch **post-Apr 2026** M1 CSV and re-run held-out script (true out-of-sample extension).
2. MT5 demo plumbing for frozen picks (`docs/live/VPS_HFM_DEMO.md`).
3. No further single-knob YAML grids on **iter244**.
