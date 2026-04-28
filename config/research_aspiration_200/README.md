# Research — +200%/month aspiration (simulation-only)

Per `docs/plan.md`, **+200% per calendar month** is an *aspiration*, not the
iter30 rolling-window promotion gate. This directory holds **explicitly
aggressive, simulation-only** YAML stacks that loosen sizing within plan
§A guardrails (no martingale, no averaging down, no lookahead, no news
strategies) to answer: *is the ceiling risk/sizing or signal quality?*

**Do not deploy these as the HFM baseline** without a separate review. They
exist to push the search frontier and log honest metrics.

See `scripts/iter34_moonshot_sweep.py` for a bounded grid over members of
this family.

## Result snapshot (2026-01..04 M1, same CSV as other iter runs)

| Config | best month % | full % | cap viol | ruin |
|--------|-------------:|-------:|:--------:|:----:|
| `moonshot_pivot_daily_r18_tp9` | **~275** (Feb, +274.98%) | ~691 | 4 | no |
| `moonshot_pivot_daily_r10` | ~83.5 (Mar) | ~210 | 2 | no |
| `moonshot_squeeze_lon_r15` / dual ensemble | negative | deep loss | many | yes |

**Interpretation:** the +200%/month bar can be exceeded in a *single* month
in-sample by a lone high-risk `pivot_bounce` with long TP2; the same recipe
still has bad months, cap hits, and is not walk-forward safe. Treat as a
ceiling probe, not a live candidate.

## Cap-clean frontier (same family, Jan–Apr 2026 M1)

Scripted grid (`scripts/iter35_moonshot_cap_frontier.py`): with **no HTF
gate**, **no** `(risk%, tp2_rr)` pair in the default scan both hit
**cap_violations=0** and **best_month ≥ 200%** on this CSV. The best
cap-clean single-month observed in that scan was **~+83.5%** at **5% / TP2
9.5** — saved as `moonshot_pivot_daily_r5_tp95_capclean.yaml`.

**Sweep with M15 `adx_max` on the hot r15–18 / tp2 6–9 grid:** still **zero**
`cap_violations==0` rows — gating chop does not rescue the daily moonshot
from cap days at those risk levels on this sample.

Saved cap-clean picks from `iter35_moonshot_cap_frontier.py`:

- `moonshot_pivot_daily_r45_tp95_capclean.yaml` — **~+90.3%** best month.
- `moonshot_pivot_daily_r5_tp95_capclean.yaml` — **~+83.5%** best month, **~+204%** full.

## Iter36 — regime splits (creative, mostly falsified)

- **`regime_split_pivot_mr_squeeze_tr.yaml`**: pivot in chop, squeeze in trend
  → **bad** (negative full, caps) on 2026 sample. Marked falsified in file.
- **`regime_split_squeeze_mr_pivot_tr.yaml`**: inverse → **bad** (caps).
- **`adaptive_dual_pivot_chop_moon_r9_tp9.yaml`**: `adaptive_router` with two
  `pivot_bounce` members — wide TP2 + M15 `adx_max` on chop only, tight pivot
  in trend. At **9%/trade** → **cap=0**, Feb **~+104%**, full **~+60%** on the
  same CSV (Apr still weak; not a full fix).
- **`adaptive_dual_pivot_chop_moon_r9_tp9_aprilblock.yaml`** — Iter37: chop
  member adds `block_hours_utc: [13, 14]` and `adx_max: 28`. Same CSV:
  **Apr ~+5.9%** (vs ~-12.8% base), **cap=0**, full **~+250%**, Feb **~+126%**;
  **Mar ~-8.1%** trade-off. Reproduce sweep: `scripts/iter37_dual_pivot_april_sweep.py`.
- **`adaptive_dual_pivot_chop_moon_r9_tp9_balanced.yaml`** — Iter38: same chop
  as aprilblock; **pivot_trend** at TP2 **1.6R**, risk **0.6×**, max **2** trades/day.
  **Mar ~-0.24%**, **Apr ~+2.17%**, **cap=0**, full **~+252%** (Mar/Apr Pareto
  winner in `scripts/iter38_mar_apr_pareto_sweep.py --quick`).

## Iter39 — rolling harness + triple-member falsification

`python3 scripts/iter32_compare_configs.py --csv data/xauusd_m1_2026.csv <configs>`:

| YAML | full % | Mar | Apr | rolling wins | worst_score | full cap |
|------|-------:|----:|----:|:-------------:|------------:|:--------:|
| dual `r9_tp9` (base) | ~60 | -2.6 | -10.8 | **0/4** | DQ | 0 |
| dual aprilblock | ~250 | -8.1 | +5.9 | 1/4 | ~3.60 | 0 |
| **dual balanced** | ~252 | -0.2 | +2.2 | **2/4** | **~0.97** | **0** |
| triple weekly-first | ~355 | -11.5 | +7.3 | 1/4 | ~4.41 | **1** |

**Takeaway:** **balanced** is the best **generalization** trade-off in this
family on the default 4-window battery. **`adaptive_triple_pivot_protect_r9_balanced.yaml`**
(weekly listed first) is **falsified**: preempts daily members and trips caps.

## Iter40 — risk% for rolling `worst_score`

Grid on `adaptive_dual_pivot_chop_moon_r9_tp9_balanced` (trend params fixed):
**8%/trade** gives **worst_score ~0.10** and **2/4** window passes (vs 9% with
~0.97). Full sample: **Mar ~+0.83%**, **Apr ~+1.28%**, cap=0, full **~+233%**.

Saved: `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml` (extends balanced; only
risk block changes). Reproduce sweep: `scripts/iter40_rollwin_risk_sweep.py`.

## Iter41 — `ensemble` sweep + dual router

`ensemble_sweep_then_dual_pivot_r8.yaml` (sweep first, dual stack second) →
**falsified**: cap hit, **0/4** harness wins (sweep dominates bars).

## Iter42 — state buckets + Mar/Apr sweep on rollwin

On `data/xauusd_m1_2026.csv` vs ``adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml``:

| YAML | Notes |
|------|--------|
| `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin_buckets.yaml` | Iter31 buckets on; **bit-identical** to rollwin — **flat sizing** (`floor == cap`) makes bucket keys irrelevant for risk; `priority_mode: config` prevents expectancy reordering. |
| `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin_expect.yaml` | Probe + **0.55–1.0** active sizing (mirrors iter31 sizing_b): **Apr / Mar can improve** but **rolling harness regresses** (worst_score ~1.9–2.0 vs ~0.1, same 2/4 wins). |

**Mar/Apr tilt:** `scripts/iter42_rollwin_mar_apr_sweep.py` perturbs chop `block_hours_utc`
and trend `tp2_rr`. Best Mar/Apr balance in the `--quick` grid: widen block to
**[13,14,15]** — saved as `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin_m131415_tp16.yaml`
(**Mar ~+3.4%, Apr ~+4.4%**, full ~153%, cap=0) but **worst_score ~4.2** and **1/4**
window passes vs rollwin — **prefer rollwin for robustness**, use `_m131415_` only if
recent-month lift outweighs cross-window score.

## Iter45 — multi-method stacks (web-inspired; March/April focus)

Desk-style ideas: combine **regime identification**, **session liquidity**, and
**mean reversion in chop** (Keltner / ATR bands) rather than pivot-only grids.

### Strong Mar/Apr: `adaptive_triple_keltner_split_regimes_r8.yaml`

**adaptive_router** with **split regimes**: **transition → Keltner MR** (listed first),
**range → chop pivot**, **trend → tight pivot**. Same **8%/trade** stack as rollwin.

On `data/xauusd_m1_2026.csv`: **Mar ~+20%, Apr ~+10%**, **cap=0**, harness **3/4**
passes — large lift vs rollwin **~+0.8% / ~+1.3%** for those months. Trade-off:
**worst_score ~2.5** vs **~0.1**; use when **difficult months** matter more than
minimax rolling score.

### Falsified: `regime_router_keltner_range_squeeze_trend_r8.yaml`

**regime_router** (Keltner in range, squeeze in transition, pivot in trend) →
**cap_violations**, bad March. See **FALSIFIED** in file header.

### Iter46 — widen **transition** band (`range_adx_max`, `trend_adx_min`)

Quick grid over Keltner `risk_multiplier` × ADX thresholds (**script:**
`scripts/iter46_triple_keltner_tune.py` — phase 1 fast screen, optional harness on top-K).

Saved: **`adaptive_triple_keltner_split_regimes_r8_tune_adx19.yaml`**
(**range_adx_max=19**, **trend_adx_min=24**, **kelt_rm=0.40**).

| YAML | Mar % | Apr % | wins | worst_score |
|------|------:|------:|:------:|------------:|
| Triple Keltner (Iter45 base) | ~20 | ~10 | 3/4 | ~2.48 |
| **Tune adx19 (Iter46)** | **~38.5** | **~26.4** | 3/4 | **~8.6** |
| Rollwin dual pivot | ~0.8 | ~1.3 | 2/4 | ~0.10 |

**Takeaway:** Tightening “range” vs “transition” routing sends more bars to Keltner —
**March/April explode in-sample** but **rolling worst_score gets much worse** than
Iter45. Use **Iter45 triple** for a Mar/Apr lift with milder harness pain; use
**tune_adx19** only as an extreme Mar/Apr probe.

### Iter47 — session + chop variants on the triple-Keltner stack

| YAML | Mar % | Apr % | wins | worst_score | Note |
|------|------:|------:|:------:|------------:|------|
| `adaptive_triple_keltner_split_overlap_keltner.yaml` | ~13.3 | ~8.0 | **4/4** | **~0.65** | Keltner only in **12–16 UTC overlap** — **only variant to pass all 4 windows** with Mar/Apr well above rollwin. |
| `adaptive_triple_keltner_split_regimes_r8_m131415.yaml` | **~28.0** | **~11.1** | 3/4 | ~2.47 | Wider chop block **13–15 UTC**; best **raw** Mar/Apr in this family, same rolling pain as Iter45. |
| rollwin (reference) | ~0.8 | ~1.3 | 2/4 | ~0.1 | Cautious dual-pivot. |

**Practical pick:** use **`_overlap_keltner`** when you want **strong Mar/Apr *and* rolling
robustness**; use **`_m131415`** or **Iter45 base** when you want **maximum** Mar/Apr
on the sample and can accept **3/4** harness or **worse** `worst_score`.

### Iter48 — overlap stack refinements (negative / marginal)

| YAML | Mar % | Apr % | wins | worst_score | Verdict |
|------|------:|------:|:------:|------------:|---------|
| `adaptive_triple_keltner_split_overlap_keltner.yaml` (Iter47) | ~13.3 | ~8.0 | **4/4** | ~0.65 | **Baseline** |
| `..._overlap_keltner_m131415.yaml` | ~12.9 | ~7.7 | 4/4 | ~1.10 | **Worse** — wider chop block does not help this combo. |
| `..._overlap_keltner_kr040.yaml` | ~14.1 | ~8.0 | 4/4 | ~0.65 | **Marginal** — tiny March lift, same worst_score as Iter47. |

### Iter49 — overlap micro-grid (`scripts/iter49_overlap_micro_grid.py`)

Swept **Keltner `risk_multiplier`** × **`kelt_mult`** on the overlap stack (15 cap-clean trials).
**`worst_score` was identical (~0.645)** across the grid; **best min(Mar,Apr)**:

- **`adaptive_triple_keltner_split_overlap_keltner_kr042_km19.yaml`** — **kelt_rm 0.42**, **kelt_mult 1.9**:
  Mar ~**13.4%**, Apr ~**8.0%**, **min(Mar,Apr) ~8.0%** vs baseline ~**7.99%**, **4/4** wins.

Use as a **drop-in refinement** over Iter47 overlap when optimizing the **weaker of Mar/Apr**.

### Iter50 — London Keltner vs ADX-tuned overlap

| YAML | Mar % | Apr % | cap | wins | worst_score | Note |
|------|------:|------:|:---:|:------:|------------:|------|
| `..._overlap_keltner_kr042_km19` (Iter49) | ~13.4 | ~8.0 | **0** | **4/4** | ~**0.65** | **Default** overlap refinement |
| `..._london_keltner_kr042_km19` | ~28.2 | ~14.8 | **1** | 4/4 | ~2.08 | **FALSIFIED** — daily cap hit |
| `..._overlap_adx1924_kr038_km19` | **~27.0** | **~18.9** | **0** | **4/4** | ~**1.89** | **Mar/Apr rocket**, rolling score worse than Iter49 |

When you want **maximum Mar/Apr** while keeping **caps = 0** and **all windows**, try **`_overlap_adx1924_kr038_km19`**; when you want **best rolling minimax**, stay on **`_kr042_km19`**.

### Iter51 — `kelt_rm` sweep on ADX19/24 overlap stack

Script: **`scripts/iter51_overlap_adx1924_rm_sweep.py`** (0.30–0.50 step 0.02, cap-clean only).

**Surprise:** **very low** `kelt_rm` (**0.30–0.32**) **hurts** `worst_score** (~**3.31** vs ~**1.88**) — not monotonic.

**Best rolling score** in band: **`kelt_rm` ~0.46–0.50** → `worst_score` ~**1.880** (tiny improvement over **0.38** at ~**1.887**). Saved:
**`adaptive_triple_keltner_overlap_adx1924_kr046_km19.yaml`**.

**Trade-off:** March sample **~25.7%** vs **~27%** at `kelt_rm` 0.38 — slightly less March, slightly better `worst_score`.

### Iter52 — `kelt_mult` + `trend_adx_min` on **kr046** (`scripts/iter52_overlap_kr046_km_tam_sweep.py`)

**`kelt_mult`:** **1.75** spikes `worst_score` (~**2.88**) — wide bands hurt here. Among **4/4**
windows, **`kelt_mult` 1.95–2.0** ties **lowest `worst_score`** (~**1.878**) vs **1.880** at **1.9**.
Saved **`adaptive_triple_keltner_overlap_adx1924_kr046_km20.yaml`** (same stack, **`kelt_mult: 2.0`**).

**`trend_adx_min`:** **`23`** vs **24** shrinks trend bucket → **Apr ~4.7%**, Mar ~23%, **`worst_score` ~0.88**
but only **3/4** harness wins — **April collapses** (do not use for Mar/Apr-first without accepting that).
**`25`** → **3/4** wins and bad `worst_score` (~3.43).

### Iter53 — **`range_adx_max` sweep at `trend_adx_min=24`** (`scripts/iter53_ram_fixed_tam24_sweep.py`)

Fixed stack: **`adaptive_triple_keltner_overlap_adx1924_kr046_km20`** (kr046 + km2.0); sweep **`range_adx_max`** ∈ **17–22**.

**Winner:** **`range_adx_max: 17`** → **`adaptive_triple_keltner_overlap_adx1724_kr046_km20.yaml`**

| YAML | Mar % | Apr % | wins | worst_score |
|------|------:|------:|:------:|------------:|
| Iter49 `..._kr042_km19` | ~13.4 | ~8.0 | **4/4** | ~**0.645** |
| **`..._adx1724_kr046_km20`** | **~26.3** | **~27.3** | **4/4** | **~0.533** |
| `..._adx1924_kr046_km20` | ~26.2 | ~17.4 | 4/4 | ~1.878 |

**Interpretation:** **Tighter range threshold** (ram **17**) widens **transition → Keltner** vs ram **19**,
lifting **April** massively while **improving** rolling score vs Iter49 overlap — **cap=0**, **4/4** windows.

**Note:** At default ADX **20/25**, changing **kelt_mult** 1.9→2.0 matches km19 metrics (no extra YAML).

### Iter54 — **`trend_adx_min` sweep at `range_adx_max=17`** (`scripts/iter54_ram17_tam_sweep.py`)

On **`adaptive_triple_keltner_overlap_adx1724_kr046_km20.yaml`**, swept **tam** ∈ **18–25**.

**4/4 harness + best Mar/Apr:** **`trend_adx_min` 24** (and **24.5** — **identical** metrics on this CSV). **`23.5`**
matches **`worst_score` ~0.533** but **April ~11.8%** vs **~27.3%** at tam **24** — worse April-first trade.

**`trend_adx_min` 21:** **`worst_score` ~0.16** but only **3/4** windows — rolling robustness fails.

**`trend_adx_min` 20:** **cap violation** (dead).

**Low tam (18–19):** huge March but **April negative / ~0** — useless for the stated goal.

**Conclusion:** keep **`…_adx1724_kr046_km20`** as the **tam 24** flagship; no better **4/4** cell found in this sweep.

### Iter55 — **`range_adx_max` extend below 17** (`scripts/iter55_ram_extend_tam24_sweep.py`)

Fixed **tam 24**, **kr046 + km2.0**; swept **ram** **14–23** (Iter53 covered **17–22** partially; this extends **14–16**).

**Best rolling among 4/4:** still **`range_adx_max: 17`** → **`worst_score` ~0.533** (unchanged flagship).

**Higher Mar/Apr (4/4) alternative:** **`range_adx_max: 14`** → **`adaptive_triple_keltner_overlap_adx1424_kr046_km20.yaml`**

| YAML | Mar % | Apr % | min(Mar,Apr) | worst_score |
|------|------:|------:|-------------:|------------:|
| `..._adx1724_...` (ram **17**) | ~26.3 | ~27.3 | ~26.3 | **~0.533** |
| **`..._adx1424_...` (ram **14**)** | **~44.0** | **~31.0** | **~31.0** | **~0.850** |

**Dead:** **ram 22** caps; **ram 23** **0/4** harness.

### Iter56 — **Keltner tuning on ram 14** (`scripts/iter56_ram14_keltner_tune.py`)

Grid **kelt_rm** × **kelt_mult** on **`adaptive_triple_keltner_overlap_adx1424_kr046_km20.yaml`**.

**Result:** among **4/4** trials, **`worst_score` stays ~0.850** whenever **`kelt_mult` = 2.0** (any **kelt_rm** in grid);
**`kelt_mult` < 2.0** jumps **`worst_score` to ~1.85+** while **hurting** Mar/Apr floor. **No better Pareto**
than the default **kr046 / km2.0** ram14 file — **keep `…_adx1424_…` as-is** for that branch.

**Fractional `range_adx_max` (quick check, tam 24, kr046 km20):** **14.5** → Mar ~32%, Apr ~42%,
**4/4** but **`worst_score` ~0.95** (worse than **14** and **17**). **15.0 == 15.5** on this sample.
