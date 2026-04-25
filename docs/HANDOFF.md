# Handoff — read this first

You're picking up a long-running iterative project to build an
automated XAUUSD (gold) scalping bot for MetaTrader 5. This file
is the single source of truth for "where we are." Read this top
to bottom and you should be able to continue without reading any
other doc. The rest of `docs/` is supporting material:

- `docs/plan.md` — locked specification (constraints, gates).
- `docs/progress.md` — append-only iteration log with raw numbers.
- `docs/lessons_learned.md` — append-only insights.
- `docs/log.md` — chronological session diary.
- `docs/todo.md` — living task list.

## TL;DR

We've built and falsified 10+ different scalping strategy families
on real 2026 XAUUSD M1 data. Under the original conservative
constraints, **`news_fade`** was the first positive walk-forward
winner. After the user's 2026-04-25 GOLD-only high-risk revision,
the strongest current held-out result is now
**`session_sweep_reclaim`** — a London/NY false-breakout reclaim of
the Asian range.

The current best recent held-out signal is materially larger but
still below the 200 %/month aspiration: `session_sweep_reclaim`
with an aggressive 5 % risk / TP1=1R profile returned **+29.1 % over
the latest 14-day tournament** and **+36.2 % over the latest 7-day
tournament**, with no cap violations and min equity > 95 %. Its full
Jan-April run is now positive (+6.1 %, April +14.9 %, March -0.7 %)
but Jan/Feb are still weak, so latest-regime focus remains essential.

The next concrete moves (in `docs/todo.md`): refine
`session_sweep_reclaim` with M5/M15 confirmation and regime gating;
keep `news_fade` as a low-frequency defensive component; and
continue staged GOLD-only research batches without tuning against the
held-out windows.

## Project facts

- **Symbol:** XAUUSD only for active research. BTCUSD was tried and
  deprioritised (HFM real spread ~ $10 makes M1 scalping
  uneconomic). EURUSD/GBPUSD expansion was considered, then
  explicitly rejected by the user on 2026-04-25 because each symbol
  has distinct behavior and the mandate is now GOLD-only.
- **Timeframe:** M1, with M5/M15/H1 used as higher-TF context.
- **Broker / spec:** HFM Katana demo account, ¥100,000 JPY
  starting balance. Hard rules in `docs/plan.md §A`:
  - Conservative baseline: leverage ≤ 1:100
  - Conservative baseline lot cap ≤ `0.1 × balance_JPY / 100_000`
    (so 0.1 lot at ¥100k)
  - Conservative baseline daily envelope:
    **+30 % profit / −10 % loss → flatten + stop**
  - 2026-04-25 revision: these sizing/cap values may be loosened in
    clearly labelled research simulations. The non-negotiable guardrail
    is avoid margin-call / zero-cut ruin. No martingale, no blind
    averaging down, and no lookahead remain prohibited.
  - pullback-only entries; no martingale
  - XAUUSD: flatten before Friday close. BTCUSD: 24/7.
  - news blackout ±30 min around high-impact events
    (re-used as the *trigger* in `news_fade`)
  - up to 2 sub-positions per entry decision (TP1/TP2 with
    break-even on TP1 fill)
- **Execution model in backtests:** spread × 1.5 pessimistic
  (12 points = $0.12 for HFM Katana), 2-tick slippage,
  spread-only commission.
- **Aspiration:** +200 % per month. The user has been explicit
  this is aspirational, not a promotion gate. We track monthly
  return as the primary scoreboard.

## How to run anything

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Fetch real XAUUSD data (cross-platform, no Windows needed)
python -m ai_trader.scripts.fetch_dukascopy \
    --symbol XAUUSD --timeframe M1 \
    --start 2026-01-01 --end 2026-04-24 \
    --out data/xauusd_m1_2026.csv
# Cache lives in data/cache/dukascopy; re-runs are incremental.

# Run a single backtest (any config)
python -m ai_trader.scripts.run_backtest \
    --config config/news_fade.yaml \
    --csv data/xauusd_m1_2026.csv --no-report

# Walk-forward parameter sweep (the core research loop)
python -m ai_trader.scripts.run_sweep \
    --config config/news_fade.yaml --csv data/xauusd_m1_2026.csv \
    --strategy news_fade \
    --sweep-id my-sweep \
    --split-mode recent_only \
    --research-days 60 --validation-days 14 --tournament-days 14 \
    --score-on validation --objective monthly_pct_mean \
    --min-validation-trades 1 \
    --grid 'trigger_atr=1.0,1.5,2.0' \
    --grid 'sl_atr_mult=0.5,1.0'

# Tournament evaluation: open the held-out window EXACTLY ONCE
# per strategy family / data file. Don't tune against this.
python -m ai_trader.scripts.evaluate_tournament \
    --config config/news_fade.yaml --csv data/xauusd_m1_2026.csv \
    --strategy news_fade --label my-eval \
    --split-mode recent_only \
    --research-days 60 --validation-days 14 --tournament-days 14 \
    --param trigger_atr=2.0 --param sl_atr_mult=0.5

# Tests (always run before you push)
pytest -q
```

Live demo on a Windows host with HFM MT5:

```bash
python -m ai_trader.scripts.run_demo --config config/demo.yaml
```

Live execution requires the `MetaTrader5` Python package
(Windows-only); we have a thin adapter (`ai_trader/broker/mt5_live.py`)
but it has not been validated end-to-end on a live account yet.

## Architecture in 30 seconds

```
ai_trader/
├── data/             OHLCV loaders (CSV, Dukascopy, synthetic) + MTFContext
├── indicators/       ATR, fractal swings, ZigZag, Fibonacci, etc.
├── strategy/         Strategies (see "Strategies" below) + EnsembleStrategy
├── risk/             RiskManager (sizing + caps + envelope) + FX
├── broker/           PaperBroker (backtests) + MT5LiveBroker stub
├── backtest/         BacktestEngine + metrics + walk-forward splitters + sweep harness
├── news/             News calendar (CSV-driven blackout / event-trigger)
├── live/             Live runner (paper + MT5)
├── state/            Crash-safe persisted state (day ledger, kill-switch)
├── review/           Review-trigger engine + packet generator
└── scripts/          CLIs (run_backtest, run_sweep, evaluate_tournament, ...)
```

Two design invariants worth knowing about:

- **No-lookahead:** strategies see history up to and including the
  just-closed bar; signals fill at the **next** bar's open. Hard
  TP/SL fill **intra-bar** at the SL/TP price (locked in
  `tests/test_fills_intra_bar.py`). MTF lookups via `MTFContext`
  return only fully-closed HTF bars.
- **Strategies are stateless w.r.t. the broker.** They emit
  `Signal` objects; the risk manager sizes; the broker executes.
  Same code runs in backtest and live.

## Walk-forward discipline (read carefully — this is what protects us)

Every strategy goes through three non-overlapping windows
*before* we believe its numbers:

1. **Research:** parameter tuning happens here. Grid sweep capped
   at 20 trials per iteration (`--max-trials 20`) — anti
   p-hacking ratchet.
2. **Validation:** the sweep ranks trials on this window's
   metrics. Trials with fewer than `--min-validation-trades`
   trades on the scored window are demoted, no matter how good
   their headline metric looks (one lucky trade ≠ a win).
3. **Tournament:** held out. Only opened once per strategy
   family / data file. Strict opt-in via the literal phrase
   `i_know_this_is_tournament_evaluation=True` in the loader
   (grep-able on purpose).

Three split modes, all in `ai_trader/backtest/splitter.py`:

- `recent` — date-based: last N days = tournament, M days
  before = validation. Default for current-regime focus.
- `recent_only` — all three windows pulled from the tail
  (default 21 + 14 + 7 = ~6 weeks). For "current regime is the
  only one that matters" tuning. **This is what produced the
  news_fade result.**
- `interleaved` — round-robin block deal. Each role samples
  every regime; protects against contiguous-regime bias.

Promotion gates are **human review**, not hard numbers. Bot
produces evidence; user decides. Auto-reject only on a hard rule
violation (cap, leverage, etc.).

## Strategies (registry summary)

All registered in `ai_trader/strategy/registry.py`. Each has a
config file under `config/`. Ones in **bold** are current
candidates worth keeping; the rest are kept for the falsification
record but should not be promoted.

| name | family | best result | verdict |
|---|---|---|---|
| `trend_pullback_fib` | swing | seed only; M5; never promotable | shelved |
| `donchian_retest` | swing | research PFs all sub-1.0 | falsified |
| `bb_scalper` | M1 mean-rev | full 4-mo: −13.1 %/month | falsified honest re-eval |
| `bos_retest_scalper` | M1 structure | tournament PF 1.05–1.06, +0.6–1.4 %/12d | candidate (regime-agnostic, low-freq) |
| `trend_pullback_scalper` | M1 trend | 12d tournament PF 0.79 | falsified (regime-dep) |
| `liquidity_sweep` | M1 reversal | best validation PF 1.07 | falsified |
| `mtf_zigzag_bos` | MTF + ZZ | val PF 1.47 / DD 3 % / tournament PF 0.58 | sample-too-small |
| `volume_reversion` | M1 mean-rev + vol | val 1.18 / tournament 0.82 | falsified |
| `vwap_reversion` | M1 VWAP fade | val PF 1.48 / tournament PF 0.93 (14d) | thin |
| `london_orb` | session breakout | flat (~0.2 trades/day) | shelved |
| **`news_fade`** | **calendar event fade** | **research PF 3.24 / val PF 10.6 / tournament PF 3.87** | **first conservative walk-forward winner** |
| **`session_sweep_reclaim`** | **Asian-range sweep/reclaim** | **latest 14d tournament +7.9 %, PF 2.65, DD -5.9 %** | **current high-risk GOLD candidate** |
| `ensemble` | wrapper | best as `news_fade + vwap_reversion` | tournament floor better than VWAP alone |

### `news_fade` — the current best

Trades only inside a window after a high-impact USD event
(NFP, CPI, FOMC, Core PCE — see `data/news/xauusd_2026.csv`).
At T + 5 minutes, watch for price to displace > `trigger_atr ×
ATR` from the pre-news anchor; fade in the opposite direction
with TP back at the anchor.

- **Config:** `config/news_fade.yaml`
- **Best params (iter25 winner, recent_only 60/14/14):**
  `trigger_atr=2.0, window_min=60, sl_atr_mult=0.5, tp_to_anchor=true`
- **Numbers:**
  - Research: PF 3.24, +2.60 %, DD −1.11 %, 11 trades
  - Validation: PF 10.6, +0.24 %, DD 0 %, 2 trades
  - Tournament (held out): PF 3.87, +0.10 %, DD −0.13 %, 2 trades
  - Full 4-month run: monthly mean **+0.60 %**, 2/4 profitable
    months, daily Sharpe **+1.65**, DD **−2.0 %**
- Why it works: scheduled events, structurally consistent
  overshoot, real anchor price for SL/TP. **Different population
  of trades from price-action scalping → uncorrelated edge.**

### `session_sweep_reclaim` — current high-risk GOLD candidate

Fades false breakouts of the Asian range during the London/NY
session. It waits for price to sweep an Asian-session extreme,
then close back inside the range before entering in the reclaim
direction. This matches the user's discretionary premise better
than the prior ORB breakout: do not chase the liquidity grab; trade
the reclaim after the stop-hunt.

- **Config:** `config/session_sweep_reclaim_aggressive.yaml`
- **Best params (session-sweep-v1):**
  `trade_start_hour=7, trade_end_hour=12, min_sweep_atr=0.1,
  risk_per_trade_pct=2.0`
- **Numbers:**
  - Research: PF 0.50, −8.26 %, DD −11.1 %, 51 trades
  - Validation: PF 2.59, +4.95 %, DD −3.95 %, 13 trades
  - 14d tournament: PF 2.65, +7.90 %, DD −5.91 %, 17 trades,
    min equity 98.2 %, no cap violations
  - 7d tournament: PF 5.52, +9.25 %, DD −5.83 %, 8 trades,
    min equity 99.7 %, no cap violations
  - Full Jan-April: monthly mean −4.48 %, April +2.02 %,
    March −5.0 %, Jan −13.7 %
- Interpretation: strong latest-regime candidate, not a universal
  4-month winner. Needs regime gating or "recent-only" promotion
  framing.

### `ensemble`

Generic wrapper. Members declared in YAML; first member to fire
on a bar wins that bar (priority order). Risk manager's
`max_concurrent_positions` lets multiple members hold at once.
Best ensemble found so far is `news_fade + vwap_reversion`
(`config/ensemble_news_vwap.yaml`) but VWAP drags it negative
on the full 4-month run; **`news_fade` alone is the cleaner pick**
right now.

## Key gotchas (real bugs we hit; don't re-hit them)

1. **Default-off feature flags rot.** `bb_scalper.use_two_legs`
   defaulted to False and the YAML never set it; every BB result
   reported across multiple iterations was single-leg without
   break-even until the user asked. Lesson: explicit > default
   for any feature that affects results.
2. **Kill-switch must flatten on the same bar.** When a losing
   trade trips the −10 % cap, any other open positions used to
   sit exposed for a bar. Fixed; regression locked in
   `tests/test_killswitch_tight.py`. Residual ~50 bp slippage
   is bar-granularity physics.
3. **Withdrawal sweep used to inflate DD.** Equity was
   `balance + unrealized`; balance drops on the half-profit
   sweep. Fixed: equity is now
   `balance + unrealized + withdrawn_total`.
4. **Day-rollover state must be at the top of `on_bar`.** Bug
   in `london_orb` where window-end was set inside a conditional
   branch made the strategy produce 0 trades.
5. **Structural SLs need an SL cap.** `london_orb`'s
   Asian-range SL was $50+; at 0.5 % risk × $10k that rounds
   below min-lot and signals get silently rejected.
   `max_sl_atr=2.0` cap fixed it.
6. **MTF lookahead trap.** Indexing HTF frames by `close_time`
   (not bar-start time) and querying with searchsorted-`right`-
   minus-1 ensures `last_closed("M5", t)` never returns a
   still-forming HTF bar. Locked in `tests/test_mtf.py`.
7. **ZigZag: `iloc` ≠ `confirm_iloc`.** A pivot's iloc is the
   extreme bar; confirm_iloc is when threshold-reversal made it
   visible. Querying with `iloc` would be lookahead.
8. **Bug fixes can retroactively invalidate "wins."** When
   the kill-switch fix landed, several prior tournament
   "winners" (BB scalper, ensemble) collapsed: the leak had
   been masking losses. Rule: any change to engine semantics
   triggers a re-evaluation of every prior winning candidate
   before further claims.
9. **Validation can be variance-driven.** Always tournament-eval
   at a couple of window lengths (e.g. 7d AND 14d). VWAP
   showed PF 1.48 validation → PF 0.08 on 7d / PF 0.93 on 14d.

## What is NOT yet proven

- The `news_fade` tournament window had **only 2 trades**. The
  positive PF is real but the sample is tiny. Forward live data
  is the only way to settle confidence; ~3 events per month
  means ~6 trades over 2 weeks of demo.
- BB / BOS / VWAP / MTF-ZZ-BOS all show validation-positive
  edges that the tournament didn't confirm. They might be real
  and just need more data; they might be fitting noise.
- The +200 %/month aspiration is **far out of reach** with
  current strategies. Honest gap: ~333× from current
  `news_fade`-only floor. No iteration has closed it.
- Live demo has not yet run — we lack a Windows host with HFM
  MT5 access. The whole "Phase 3" demo run from the original
  plan is blocked on that.

## Where to look next (recommended order)

1. **Multi-instrument `news_fade`.** Same pattern likely works
   on EURUSD/GBPUSD around the same USD events, but the user has
   explicitly rejected multi-symbol expansion for now. Do not pursue
   unless the user reverses that direction.
2. **Refine `session_sweep_reclaim`.** Add M5/M15 confirmation,
   session-specific filters, and BE/runner variants. It is the best
   latest-tournament GOLD-only result so far.
3. **Regime router.** Add `vwap_reversion` and friends only in
   chop regimes (ADX < 25 or realized vol below median). The
   hypothesis is they validate but die in trend; gating by
   regime should recover their validated edge.
4. **Richer event calendar.** Current `data/news/xauusd_2026.csv`
   is hand-curated 14 events for 4 months. A complete calendar
   (ISM, retail sales, PPI, ADP, jobless claims) doubles trade
   count.
5. **Live demo on HFM.** Either you provision a Windows host /
   VPS, or get HFM's free VPS approved (last we checked, it
   typically requires a funded live account; demo accounts
   often don't qualify).
5. **Then go after the gap.** With `news_fade` proven on demo,
   stack uncorrelated edges (instrument breadth, calendar
   breadth, regime-routed price-action) until the floor is
   meaningfully above the current ~0.6 %/month.

## The user's stated direction (most-recent-wins)

The plan has shifted under user direction multiple times. Most
recent guidance, in order:

- Push trade frequency higher (the user trades ≥ 15/day manually).
- Take more risk per trade (2–4 %), backed by the daily kill-switch.
- Don't fixate on the 200 %/month; just maximise return however
  you can.
- M1 alone is too noisy → use MTF + ZigZag for trend bias.
- Keep iterating relentlessly. Use the web. Try new strategies.

The bot's current settings honour these where they don't conflict
with §A constraints. Risk-% is a discoverable parameter; per-
strategy configs use 1.0 % as a baseline. The kill-switch is
verified to enforce the −10 % daily floor (with ~50 bp
bar-granularity slack).

## Workflow / git etiquette

- All work happens on `cursor/gold-trading-bot-scaffold-bb88`.
- Open PR against `main`; PRs typically get squash-merged.
- **Always pull main before pushing** (`git fetch origin main &&
  git merge origin/main`). Past sessions hit add/add merge
  conflicts every time PR was squashed during ongoing work;
  this avoids it.
- Commit per logical change, don't batch.
- All sweep / tournament artefacts go to `artifacts/sweeps/` and
  `artifacts/tournament/` (gitignored). The per-month regime
  profile in `artifacts/regime/` IS tracked because it's a
  first-class finding.

## What state is on disk

- `data/xauusd_m1_2026.csv` — 108,871 bars of real M1 XAUUSD
  Jan-Apr 2026. Gitignored. Re-fetch with `fetch_dukascopy.py`.
- `data/cache/dukascopy/` — per-hour `.bi5` cache, gitignored.
  Makes re-fetches fast.
- `data/news/xauusd_2026.csv` — hand-curated high-impact USD
  events.
- `artifacts/sweeps/`, `artifacts/tournament/` — per-iteration
  outputs (gitignored).
- `artifacts/regime/xauusd_m5_recent.md` — per-month diagnostic
  table (tracked).
