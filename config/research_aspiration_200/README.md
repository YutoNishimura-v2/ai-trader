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

## Iter61 — creative multi-method (session edges + MR ensemble)

External playbooks often emphasize **London open after an Asian range** and
**session liquidity** on gold; we stacked those ideas on top of the Iter40
`adaptive_dual_pivot_chop_moon_r8_tp9_rollwin` core (same risk block).

`python3 scripts/iter32_compare_configs.py --csv data/xauusd_m1_2026.csv <configs>`:

| YAML | full % | Mar | Apr | rolling wins | worst_score | full cap |
|------|-------:|----:|----:|:-------------:|------------:|:--------:|
| rollwin baseline | ~233 | +0.83 | +1.28 | 2/4 | ~0.101 | **0** |
| `adaptive_quad_orb_bb_keltner_rollwin_r8.yaml` | ~294 | +15.8 | +15.5 | **3/4** | ~0.117 | **0** |
| `adaptive_quartet_orb_liq_tom_rollwin_r8.yaml` | ~192 | -0.15 | -16.9 | 1/4 | ~1.91 | **1** |
| `adaptive_quad_atr_squeeze_trend_rollwin_r8.yaml` | ~155 | -15.0 | -2.6 | **0/4** | DQ | **1** |

**Takeaway:** a **transition** stack of **BB squeeze reversal → Keltner** (overlap),
then **London ORB**, then the existing dual pivots, materially lifts **Mar/Apr**
on this sample while staying cap-clean, at the cost of still missing one rolling
window. **Turn-of-month + ORB + liquidity sweep** and **ATR squeeze in trend**
were **falsified** here (caps and/or harness collapse).
