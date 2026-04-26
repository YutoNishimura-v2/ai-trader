# Iter29 adaptive candidates

Hypothesis: the breakthrough is not another static all-period strategy.
Iter29 tests adaptive behaviour and causal context selection:

- `h4_specialist_s1r1.yaml` — 4h pivot, only first support/resistance,
  lower risk. Tests whether the tournament-positive 4h edge is cleaner
  when the noisy S2/R2 levels are removed.
- `h4_specialist_no_fri_meta.yaml` — 4h specialist with Mon-Thu only and
  dynamic-risk metadata. Tests whether the iter28 Friday bleed generalises
  to faster pivots.
- `v4_growth_meta.yaml` — iter28 growth stack with explicit per-member
  risk metadata. Behaviour should remain close to iter28 while enabling
  adaptive/dynamic-risk controllers to reason about each member.
- `v4_plus_h4_protector.yaml` — low-risk 4h protector added before the
  growth stack with concurrency=2. Tests whether 4h can defend hostile
  April-like regimes without swamping Jan-Mar growth.

These are deliberate hypotheses, not a broad grid. Evaluation should use
research/validation and stress first; the already-known April tournament is
only a reporting stress window.

First local evaluation on the reproduced Jan-Apr dataset:

| config | full | validation | known April tournament |
|---|---:|---:|---:|
| h4_specialist_s1r1 | +10.06% PF 1.04 | +4.19% PF 1.11 | **+20.89% PF 1.70** |
| h4_specialist_no_fri_meta | +7.66% PF 1.04 | -15.72% PF 0.63 | +7.83% PF 1.29 |
| v4_growth_meta | +497.94% PF 1.63 | -0.21% PF 0.99 | -4.32% PF 0.85 |
| v4_plus_h4_protector | +455.54% PF 1.46 | -0.26% PF 0.99 | +13.85% PF 1.37 |
| v4_plus_h4_protector_conc1 | **+832.42% PF 1.56** | -0.26% PF 0.99 | +13.85% PF 1.37 |
| v4_plus_h4_protector_r25 | +666.98% PF 1.50 | +0.33% PF 1.01 | -1.09% PF 0.97 |
| v4_plus_h4_protector_r20 | +580.33% PF 1.51 | -0.75% PF 0.98 | +0.91% PF 1.02 |

Early read: 4h specialist/protector genuinely improves the known latest
stress window, but the protector trips one validation cap violation and the
specialist is much lower growth. This supports the adaptive-controller thesis:
the right system may need to switch between growth and 4h/defensive modes
instead of forcing either static config to carry every regime.

Follow-up: setting protector concurrency to 1 is a major full-period lift
(+832%) while preserving the +13.85% local tournament result and full-period
cap cleanliness. The 14d validation slice still has one cap violation, so this
is a research headline rather than a promotion candidate. Lowering protector
risk softens neither the validation cap nor the latest-window tradeoff enough.
