# Stale pull requests — resolved (2026-05-22)

All agent research PRs **#34–#78** are **no longer open**. Content was already on
`main` via squash merges **#79–#86**; remaining open PRs were cleared by aligning
each head branch to `main` (force-push), after which GitHub auto-**closed** empty
diffs or **merged** where still mergeable (e.g. **#78**).

**Open PR count:** **0** (verified 2026-05-22).

## Consolidation merges (substance on `main`)

| Merged PR | Content |
|-----------|---------|
| [#79](https://github.com/YutoNishimura-v2/ai-trader/pull/79) | iter60 Keltner/moonshot, Wave X, Mar/Apr docs, MT5 helpers |
| [#80](https://github.com/YutoNishimura-v2/ai-trader/pull/80) | `zigzag_fib_mtf`, iter131–204, serial journal |
| [#81](https://github.com/YutoNishimura-v2/ai-trader/pull/81) | iter103–130 |
| [#82](https://github.com/YutoNishimura-v2/ai-trader/pull/82) | README range |
| [#83](https://github.com/YutoNishimura-v2/ai-trader/pull/83) | iter66–102 artifacts |
| [#84](https://github.com/YutoNishimura-v2/ai-trader/pull/84) | This doc (initial version) |
| [#85](https://github.com/YutoNishimura-v2/ai-trader/pull/85) | May HANDOFF update |
| [#86](https://github.com/YutoNishimura-v2/ai-trader/pull/86) | iter231 + `compare_mar_apr_headliners.py` |

## Cleared PRs #34–#78

Closed or merged without new code — superseded by the table above. Do not reopen;
branch tips were reset to `main` at `2e6f528…`.

## If new stale PRs appear

```bash
# 1) Confirm nothing unique vs main
git fetch origin
for b in $(gh pr list --state open --json headRefName -q '.[].headRefName'); do
  git diff main...origin/$b --name-only | while read f; do
    git cat-file -e "main:$f" 2>/dev/null || echo "NEW $f"
  done
done

# 2) If no NEW files, align branch to main (triggers auto-close when diff empty)
git push origin main:BRANCH_NAME --force
```

Optional: GitHub Action **Close superseded research PRs**
(`.github/workflows/close-superseded-prs.yml`) if `pull-requests: write` is available.
