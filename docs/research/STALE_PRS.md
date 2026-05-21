# Stale pull requests — safe to close

All substantive content from open agent PRs **#49–#78** (and non-draft **#38–#43**)
is consolidated on `main` via **#79–#84** (2026-05-20/21).

Close each open PR with a short comment, for example:

> Superseded by merged PRs #79–#84. No unique files remain vs `main`.

## Merge map

| Merged PR | Absorbs (open PR numbers) |
|-----------|---------------------------|
| [#79](https://github.com/YutoNishimura-v2/ai-trader/pull/79) | #38–#43, #78 (iter60 + Wave X + Mar/Apr docs) |
| [#80](https://github.com/YutoNishimura-v2/ai-trader/pull/80) | #71–#77 (`zigzag_fib_mtf`, iter131–204) |
| [#81](https://github.com/YutoNishimura-v2/ai-trader/pull/81) | #55–#58, partial #63 (iter103–130) |
| [#82](https://github.com/YutoNishimura-v2/ai-trader/pull/82) | README only |
| [#83](https://github.com/YutoNishimura-v2/ai-trader/pull/83) | #49–#54 (iter66–102 artifacts) |
| [#84](https://github.com/YutoNishimura-v2/ai-trader/pull/84) | Stale-PR cleanup doc (this file) |

## Open PRs to close (30)

`#49` `#50` `#51` `#52` `#53` `#54` `#55` `#56` `#57` `#58` `#59` `#60` `#61` `#62` `#63` `#64` `#65` `#66` `#67` `#68` `#69` `#70` `#71` `#72` `#73` `#74` `#75` `#76` `#77` `#78`

Bulk close (requires repo write):

```bash
for n in $(seq 49 78); do
  gh pr close "$n" -c "Superseded by #79-#84 on main."
done
```
