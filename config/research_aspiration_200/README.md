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
