# Iter30 Handover — XAUUSD Adaptive Trading System

**Author**: cloud automation agent (this session)
**Audience**: next engineer / reviewer
**Last updated**: 2026-04-26
**Status**: PR [#33] open as draft, mergeable, awaiting review and
follow-up iteration (iter31, see "Open work" below).

This document is a self-contained professional handover. Read it
top to bottom and you should be able to (a) understand the project's
state, (b) reproduce the current numbers, (c) judge what's been
falsified, and (d) decide whether to merge, revise, or extend.

---

## 1. Executive summary

The project is a fully-automated XAUUSD (gold) scalping bot for
MetaTrader 5 / HFM Katana, operated against a JPY-denominated demo
account starting at ¥100,000. Iter30 (this session) shipped:

- A **live-faithful in-engine adaptive trading agent**
  (`ai_trader/strategy/adaptive_router.py`), wrapping a roster of
  pivot-bounce members and gating each by a causal HTF ADX regime
  classifier and decayed realised R-multiple expectancy.
- A new `Strategy.on_trade_closed(ctx)` engine hook fired
  identically by `BacktestEngine` and `LiveRunner` — the
  simulation/live equivalence guarantee, locked down by tests.
- A **rolling-window stability harness**
  (`ai_trader/research/stability.py`) that replaces the project's
  prior single-window scoring habit with N non-overlapping
  (research, validation, test) triples. Each test-window opening
  is audit-logged via the literal
  `i_know_this_is_tournament_evaluation=True` token.
- 55+ candidate configs explored under `config/iter30/`, each
  documented with the lever it tested and its result.

The **headline candidate** at the time of this handover is
`config/iter30/adaptive_v55_v43b_dml5.yaml`. On the M1 XAUUSD
2026-01-01..2026-04-24 dataset it produces:

| month | return | PF | cap_violations | worst_day | ruin_flag |
|---|---:|---:|---:|---:|---:|
| Jan 2026 | **+256.55%** | 3.82 | 0 | -8.33% | False |
| Feb 2026 | +118.04% | 1.69 | 0 | -8.77% | False |
| Mar 2026 | +68.91% | 1.37 | 0 | -9.14% | False |
| Apr 2026 | -3.55% | 0.98 | 0 | -8.10% | False |
| Full Jan-Apr | +1166.49% | 1.50 | 0 | -9.98% | False |

296 trades, max drawdown -27.23%, no cap violations on any of the
4 stability-harness windows OR on the full period.

### Honest gap

After review, **the user has rejected the headline**. The objection,
verbatim: "January and February were easy markets... what I'm
looking for is an algorithm that can profit in the difficult
markets of March and April." The Jan/Feb numbers are not evidence
of adaptation; they are evidence of trend-favoured regime. April's
-3.55% on the same config makes the agent unfit for promotion.

**The rest of this handover assumes April is the proving ground**
and orients next steps accordingly.

---

## 2. Repository layout

```
ai_trader/
├── data/             OHLCV loaders (CSV, Dukascopy, synthetic) + MTFContext
├── indicators/       ATR, fractal swings, ZigZag, Fibonacci, etc.
├── strategy/         Strategies (registered in registry.py)
│   ├── adaptive_router.py    NEW — iter30 in-engine adaptive router
│   ├── pivot_bounce.py       extended with `levels`, `risk_multiplier`,
│                             `confidence`, `emit_context_meta`
│   ├── regime_router.py      pre-iter30 static regime gating wrapper
│   └── ensemble.py           generic ordered-priority ensemble
├── risk/             RiskManager: dynamic sizing + caps + envelope + FX
├── broker/           PaperBroker (backtests) + MT5LiveBroker stub
├── backtest/         BacktestEngine + metrics + walk-forward splitters
├── news/             News calendar (legacy; news strategies are PROHIBITED)
├── live/             Live runner (paper + MT5)
├── research/         NEW — iter30 stability harness module
└── scripts/          CLIs

scripts/
├── iter28_*          iter28 phase-A sweep tooling
├── iter29_*          iter29 adaptive simulator + trade attribution
└── iter30_*          NEW — stability harness CLI + bounded sweep CLI

config/
├── default.yaml
├── iter9..iter30/    one directory per iteration; iter30/ has 55+
│                     candidate configs and a README index
└── ...

tests/                201 tests, all green at HEAD
docs/                 HANDOFF.md, progress.md, lessons_learned.md,
                      log.md, plan.md, todo.md, this file
```

Two design invariants are worth knowing about and respecting:

1. **No-lookahead**. Strategies see history up to and including the
   just-closed bar; signals fill at the next bar's open. HTF
   indicators query via `MTFContext.last_closed_idx` so a
   still-forming HTF candle is never visible.
2. **Strategies are stateless w.r.t. the broker**. Strategies emit
   `Signal` objects; the risk manager sizes; the broker executes.
   The same strategy code runs in backtest and live.

---

## 3. The iter30 adaptive router

File: `ai_trader/strategy/adaptive_router.py`. ~330 lines.

### Decision pipeline (every `on_bar`)

1. Compute HTF ADX regime: range / transition / trend, via
   `MTFContext.last_closed_idx` to avoid lookahead.
2. Filter the member roster to those whose configured `regimes`
   include the current regime.
3. Sort eligible members in **adaptive priority order**: active
   members first (by decayed expectancy descending), then probe
   members (by config order). Tie-break by config priority.
4. Walk in priority order; the first member that returns a
   non-None `Signal` wins the bar.
5. Attach to that Signal:
   - `risk_multiplier = clamp(scaled_decayed_expectancy, floor, cap)`
     for active members, or `probe_risk_multiplier` for probe
     members. Multiplied by the per-member intrinsic
     `risk_multiplier` (default 1.0) and the optional intra-day
     pyramid scalar.
   - `confidence = blend(regime_confidence_prior, |adx|/50)`.
6. The risk manager (with `dynamic_risk_enabled: true`) consumes
   `risk_multiplier` and `confidence` to size lots.

### Causal state update (every `on_trade_closed`)

1. The engine's `_book_close()` helper fires this hook AFTER it has
   booked the close in the risk manager.
2. The router locates the originating `_MemberSlot` via
   `ctx.member_name` (set by the engine to the slot's `member_id`
   at signal time).
3. Appends `ctx.r_multiple` (or `sign(ctx.pnl)` fallback) to the
   slot's deque of recent samples.
4. Recomputes decayed expectancy and updates the slot's
   `state` flag with hysteresis: probe → active when expectancy
   reaches `eligibility_on_threshold` (default +0.05R); active →
   probe when it drops below `eligibility_off_threshold` (default
   -0.10R).

### Optional intra-day pyramid

When `intra_day_pyramid_enabled=true`, the router multiplies every
post-close trade's risk_multiplier by a scalar that grows with
intraday wins (`intra_day_win_scalar`, capped at
`intra_day_max_scalar`) and shrinks with intraday losses
(`intra_day_loss_scalar`, floored at `intra_day_min_scalar`).
After `intra_day_loss_streak_pause` consecutive losses, all
members drop to probe-level for the rest of the UTC day. State
resets at UTC day rollover.

### Sim/live equivalence

Both `ai_trader/backtest/engine.py` (in `_book_close()`) and
`ai_trader/live/runner.py` (in `_fire_close_callback()`) construct
a `ClosedTradeContext` and call `strategy.on_trade_closed(ctx)`.
The same dataclass shape is delivered in both. This is the
guarantee that the router's adaptive state evolves identically in
simulation and live demo, locked down by:

- `tests/test_strategy_close_callback.py` (4 tests)
- `tests/test_live_runner_close_callback.py` (1 test)
- `tests/test_adaptive_router.py::test_router_causality_close_does_not_affect_same_bar_decision`

---

## 4. The iter30 stability harness

File: `ai_trader/research/stability.py`. ~600 lines.

### Public API

| symbol | role |
|---|---|
| `Window` | one (research, validation, test) triple sharing a calendar |
| `build_rolling_windows(df, n_windows, ...)` | partition a dataset into N non-overlapping triples ending at the dataset's last bar |
| `generalization_score(val_metrics, test_metrics)` | per-window single number; -inf when DQ |
| `evaluate_config(cfg, full_df, windows, ...)` | run a config across the full set and every window |
| `score_config(eval)` | flatten an evaluation into a leaderboard row |
| `promotion_status(eval, ...)` | dual-gate verdict: `promotable / candidate / falsified / disqualified` |
| `compute_best_month(metrics_full)` | extract best calendar-month return from full-period metrics |

### Per-window disqualification rules

`generalization_score(val, test)` returns `-inf` whenever any of:

- `val.cap_violations > 0` or `test.cap_violations > 0`
- `val.ruin_flag` or `test.ruin_flag`
- `val.profit_factor < 1.0` or `test.profit_factor < 1.0`
- `(val.return_pct < 0) != (test.return_pct < 0)` — sign mismatch

Otherwise it returns `min(val.return_pct, test.return_pct)`. The
`min` (not max, not mean) is deliberate: it forces a config to
prove generalization on its WORST passing window, not its average.

### Audit log

Every test-window opening writes one JSONL line to
`artifacts/iter30/stability/audit.jsonl`:

```json
{
  "ts": "2026-04-26T...",
  "label": "<run label>",
  "window": "W3",
  "config_hash": "abc123...",
  "config_path": "config/iter30/...",
  "audit_token": "i_know_this_is_tournament_evaluation=True",
  "research_span": ["2026-...","2026-..."],
  "validation_span": ["2026-...","2026-..."],
  "test_span": ["2026-...","2026-..."],
  "val_return_pct": 12.34,
  "val_profit_factor": 1.45,
  "val_cap_violations": 0,
  "test_return_pct": 5.67,
  "test_profit_factor": 1.21,
  "test_cap_violations": 0,
  "score": 5.67
}
```

Audit-grep on this file confirms post-hoc that each test window
was opened once per candidate.

### Promotion gate (iter30 version)

```
promotable iff:
   windows_passing >= 3 of 4
   AND val PF >= 1.5 on every passing window
   AND test PF >= 1.2 on every passing window
   AND no cap_violations / ruin on any reported window
   AND best_month_pct >= 200.0 with cap_violations=0 on that month

candidate iff:
   ONE of (generalization, 3x-month) gates passes

falsified iff: neither gate passes (no cap/ruin trigger)

disqualified iff: any cap_violations or ruin_flag on any reported window
```

**Iter31 retires the `best_month_pct >= 200.0` gate.** See §7.

---

## 5. Sweep history (55+ configs)

The full lineage and per-config falsification reasons live in
`config/iter30/README.md`. Headline numbers:

| config | rolling-battery wins | full% | Jan% | Feb% | Mar% | Apr% | PF | cap_viol |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **iter28 v4_ext_a_dow_no_fri** (baseline) | 1/4 | +498 | +105 | +94 | +45 | +4 | 1.63 | 0 |
| **iter29 protector_conc1** (baseline) | 3/4 | +832 | +116 | +86 | +77 | **+31** | 1.56 | 0 |
| **iter9 v4_router** (baseline) | 0/4 | -16 | -19 | 0 | -2 | +6 | 0.73 | 0 |
| **adaptive_v29 (clone)** | 3/4 | +832 | +116 | +86 | +77 | +31 | 1.56 | 0 |
| **adaptive_v31 (Mon-Thu unlock)** | 4/4 | +444 | +110 | +88 | +47 | +9 | 1.46 | 0 |
| **adaptive_v32** | 4/4 | +308 | +117 | +34 | +33 | +6 | 1.37 | 0 |
| **adaptive_v36** | 4/4 | +375 | +154 | +33 | +33 | +6 | 1.37 | 0 |
| **adaptive_v43b** | 4/4 | +441 | +159 | +42 | +40 | +5 | 1.40 | 0 |
| **adaptive_v55b** (iter30 headline) | 2/4 | +1167 | +257 | +118 | +69 | -4 | 1.50 | 0 |

Key observations from the table:

- **iter29 protector_conc1 has the best April number we've ever
  seen (+31%)** — and it's a STATIC config, no router. The iter30
  router did not improve April; in many cases it made April worse.
- The iter30 trade-off frontier is between Jan upside and rolling-
  battery generalization. Pushing Jan past +200% costs windows.
- **No iter30 config beats iter29 protector_conc1's April
  number.** This is the most damning finding for the iter30
  adaptive layer in light of the user's Mar/Apr criticism.

### Negative records (notable falsifications)

- `risk_per_trade ∈ {11, 12}` always trips cap violations because
  the first-of-day SL closes the day at -11% > the -10.5% cap.
  Cap-respecting ceiling on this engine is risk_per_trade=10%.
- Aggressive intra-day pyramid (`v13`, `v18`, `v40`, `v47`)
  amplifies losing days as much as winning days.
- Adding more uncorrelated members (`v11`, `v17`, `v21`, `v28`) to
  the router's roster generally drags PF — the new members didn't
  earn their probe-state survival.
- Concurrency=2 (`v7`, `v20`) double-up on parallel SL hits trips
  the cap.

---

## 6. How to run things

### Environment

```bash
python3 -m pip install --user -r requirements-dev.txt
```

### Fetch real M1 XAUUSD data

```bash
python3 -m ai_trader.scripts.fetch_dukascopy \
  --symbol XAUUSD --timeframe M1 \
  --start 2026-01-01 --end 2026-04-24 \
  --out data/xauusd_m1_2026.csv
```

Cache lives under `data/cache/dukascopy`; re-fetch is incremental.
The dataset is gitignored.

### Reproduce the iter30 headline

```bash
python3 scripts/iter30_stability.py \
  --csv data/xauusd_m1_2026.csv \
  --label iter30-headline \
  --config config/iter30/adaptive_v55_v43b_dml5.yaml
```

Output: per-window leaderboard table to stdout, `leaderboard.md`
to `artifacts/iter30/stability/iter30-headline/`, audit lines
appended to `artifacts/iter30/stability/audit.jsonl`.

### Per-month standalone evaluation

```python
from ai_trader.config import load_config
from ai_trader.data.csv_loader import load_ohlcv_csv
from ai_trader.research.stability import _run_one
import pandas as pd

df = load_ohlcv_csv("data/xauusd_m1_2026.csv")
cfg = load_config("config/iter30/adaptive_v55_v43b_dml5.yaml")
for month_name, (start, end) in [
    ("Jan", ("2026-01-01", "2026-02-01")),
    ("Feb", ("2026-02-01", "2026-03-01")),
    ("Mar", ("2026-03-01", "2026-04-01")),
    ("Apr", ("2026-04-01", "2026-04-25")),
]:
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sub = df[(df.index >= s) & (df.index < e)]
    m = _run_one(sub, cfg)
    print(month_name, m["return_pct"], m["cap_violations"])
```

### Bounded sweep

```bash
python3 scripts/iter30_sweep.py \
  --csv data/xauusd_m1_2026.csv \
  --base config/iter30/adaptive_v43_v36_loose_dd.yaml \
  --label mi-test \
  --grid "risk.daily_max_loss_pct=4,5,6" \
  --max-trials 4
```

### Tests

```bash
python3 -m pytest -q                  # 201 tests, ~2 min
python3 -m pytest -q tests/test_adaptive_router.py     # ~0.5s
python3 -m pytest -q tests/test_stability_harness.py   # ~95s (real data)
```

---

## 7. Open work — Iter31 (Mar/Apr-focused adaptation)

A complete iter31 plan is in `/opt/cursor/artifacts/PLAN.md`.
Summary of the redirection:

### What changes from iter30

- **Retire the `best_month_pct ≥ 200%` gate.** It rewarded
  cherry-picking. Iter31 evaluates every month on its own and
  insists no month is strongly negative.
- **March and April become the proving ground.** The new
  promotion gate is: Mar ≥ +20%, Apr ≥ +20%, every month ≥ -3%,
  cap_violations=0 on every month, ruin_flag=False everywhere.
  January and February numbers are reported but are NEVER part
  of the objective.
- **Stability harness gets a `focus_months` weighting.** Only
  windows whose test slice overlaps March or April count for
  `focus_worst_score` and `focus_windows_passing`.

### What changes in the agent

The diagnosis driving iter31 is that the iter30 adaptive router
adapts *too slowly and on too few features*. By the time
per-member realised expectancy decays to demotion threshold, April
is already half over.

Iter31 will add to `adaptive_router`:

1. **Causal market-state features**: rolling M15 ATR-percentile
   buckets (low/mid/high vol), trend-persistence buckets
   (directional/chop), session-of-day, day-of-week.
2. **Per-(member × bucket) expectancy**: the router maintains
   separate decayed-expectancy estimates per bucket. A regime flip
   doesn't have to wait for global expectancy to decay; it
   instantly consults the relevant bucket's history.
3. **Bucket-warm seeding from the research period**: pre-populate
   each (member × bucket) deque with realised expectancy from
   matching warmup buckets. Avoids cold-start penalty on month
   boundaries.
4. **Mar/Apr-weighted stability harness** as above.

Bounded sweep budget: 4 micro-iterations × ≤ 12 trials each.

### What we should NOT do

- Do NOT loosen the ruin guard (`risk_per_trade > 10%`,
  `daily_max_loss_pct > 10%`) to manufacture an April win. Hard
  ceiling enforced by the engine and the score function.
- Do NOT add news-driven strategies. Permanently prohibited per
  iter11 user directive.
- Do NOT optimize on the rolling battery's W2 window (Feb test
  slice). It is reported only.

### Honest probability assessment

The static iter29 protector_conc1 already gets April +31% with no
adaptation at all — that's our prior best. The iter31 agent must
demonstrably IMPROVE on +31% while keeping cap_violations=0 in
April and Mar. There's a real chance the dataset's April simply
doesn't yield a +50% month for the pivot-bounce family at any
ruin-respecting sizing — in which case the honest answer is
"Apr +31% is the ceiling on this dataset; promotion needs fresh
May data."

---

## 8. Plan-§A guardrails (immutable)

These are the user constraints from `docs/plan.md §A`. Any
violation is an automatic-reject. They have NOT changed in iter30.

1. Leverage cap ≤ 1:100.
2. Lots ≤ 0.1 × balance_JPY / 100,000.
3. Daily envelope: +30% / -10%; flatten + stop on either trigger.
4. Pullback-only entries; no martingale; no averaging-down.
5. Up to 2 sub-positions per entry decision (TP1/TP2 with
   break-even on TP1 fill).
6. Weekend handling: XAUUSD flatten before Friday close.
7. News blackout ±30 min around high-impact USD events; news
   strategies themselves are PERMANENTLY PROHIBITED.
8. Crash-safe state on disk.
9. Withdrawal half-of-daily-profit (off in research configs).
10. Review-session triggers (EOD, 2-SL streak, rule violation,
    weekly).

The 2026-04-25 GOLD-only revision authorizes loosening sizing/cap
values in clearly labelled simulation-only research runs, but the
non-negotiable guardrail remains "avoid margin-call / zero-cut
ruin."

---

## 9. Branches and PR

- Active branch: `cursor/simulation-200-return-target-a4a0`,
  pushed to origin. Mirrored to
  `cursor/trading-algorithm-breakthrough-12d3` for continuity with
  prior session naming.
- Pull request: [#33](https://github.com/YutoNishimura-v2/ai-trader/pull/33).
  Open as draft. Currently `MERGEABLE` / `CLEAN` after the
  origin/main merge committed in this session.
- The merge commit's body documents the conflict resolution: 4
  files (engine.py + 3 docs), all classified as simple, all
  resolved with HEAD because main was a squash-merge of iter29
  whose content already lives further down on the branch.

---

## 10. Decision matrix for the reviewer

| Decision | Rationale |
|---|---|
| **Merge PR #33 to main** | The iter30 plumbing (engine hook, stability harness, audit log, adaptive_router skeleton) is reusable infrastructure regardless of the headline candidate. The 55+ configs document the falsification surface. 201/201 tests green. |
| **Hold PR #33 until iter31 completes** | If you want a single PR that contains both the infrastructure AND a config that passes the Mar/Apr-focused gate. Recommended if the next reviewer prefers atomic merges. |
| **Cherry-pick the infrastructure, drop the configs** | Possible if you want the engine hook + stability harness without cluttering main with 55+ falsified candidates. |

The cloud agent recommends **option 2** (hold PR #33 until iter31
completes). The headline candidate (v55b) does not survive the
user's stated bar; merging it would ship a "winner" that the user
has explicitly rejected.

---

## 11. Files added in iter30 (reference)

```
ai_trader/research/__init__.py
ai_trader/research/stability.py            (~600 lines)
ai_trader/strategy/adaptive_router.py      (~330 lines)

scripts/iter30_stability.py                (~220 lines)
scripts/iter30_sweep.py                    (~210 lines)

config/iter30/README.md
config/iter30/adaptive_v1.yaml             ... v55_v43b_dml5.yaml
                                           (≥ 55 candidates)

tests/test_stability_harness.py            (11 tests)
tests/test_strategy_close_callback.py      (4 tests)
tests/test_live_runner_close_callback.py   (1 test)
tests/test_adaptive_router.py              (10 tests)
```

Modifications:

```
ai_trader/strategy/base.py                 + ClosedTradeContext, on_trade_closed
ai_trader/backtest/engine.py               + _book_close() helper, fires both hooks
ai_trader/live/runner.py                   + _fire_close_callback() mirror
ai_trader/broker/base.py                   + Order/Position .meta field
ai_trader/broker/paper.py                  + propagate Order.meta to Position
ai_trader/strategy/registry.py             + register adaptive_router
ai_trader/strategy/pivot_bounce.py         (already had: levels, risk_multiplier,
                                            confidence, emit_context_meta — iter29)
docs/HANDOFF.md                            + iter30 TL;DR section
docs/progress.md                           + iter30 entry
docs/lessons_learned.md                    + iter30 entries
docs/plan.md                               + generalization-scoring update
docs/log.md                                + 2026-04-26 iter30 session entry
config/iter30/README.md                    NEW (lineage, falsifications)
```

---

## 12. Quick-start for the next engineer

```bash
# 1. Get the branch.
git fetch origin
git checkout cursor/simulation-200-return-target-a4a0

# 2. Verify clean state.
python3 -m pytest -q             # 201 should pass
git status                       # should be clean

# 3. Reproduce the iter30 headline (~2 min).
python3 scripts/iter30_stability.py \
  --csv data/xauusd_m1_2026.csv \
  --label reproduce-iter30 \
  --config config/iter30/adaptive_v55_v43b_dml5.yaml

# 4. Read the iter31 plan.
cat /opt/cursor/artifacts/PLAN.md   # if accessible
# (or read this file's §7 above)

# 5. Begin Phase 0 of iter31: write scripts/iter31_attribution.py
#    to dump per-trade meta and confirm the iter30 router is NOT
#    actually adapting across the Mar/Apr boundary.
```

---

End of handover.
