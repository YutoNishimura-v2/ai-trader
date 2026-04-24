# Lessons learned

Append-only. One bullet per insight. Keep short; link to a PR or a
progress entry for the full story.

## Planning

- **Separate user constraints from discoverable policy.** The first
  two plan drafts pre-committed numbers (risk %, DD thresholds,
  demo duration) that should have been outputs of the iteration
  loop. v3 fixes this: only user-imposed rules are locked; numeric
  policy is discovered and reviewed.
- **Single-position assumption was too restrictive.** Real
  discretionary management uses TP1/TP2 + break-even. The Signal
  abstraction needs to carry multiple legs from day one, not be
  retrofitted later.
- **"Steady, consistent" and "high-risk, high-reward" are
  incompatible framings** unless "steady" is narrowed to "don't
  blow up". v3 uses the narrow meaning.
- **Mandatory review cadence matters more than trigger reviews.**
  Quiet days are data. If we only review on triggers, we bias the
  corpus toward losing days.

## Phase 2

- **"Zero trades in validation" is not a good score.** First recent-
  regime sweep had trials declaring validation PF of +infinity on
  one trade. One trade is statistical noise. Added a "minimum
  validation trade count" rule of thumb: 20+ is the floor before
  any validation metric is trustable. Next sweep harness revision
  should flag trials below the threshold in the summary.
- **Regime change is visible in ADX + up-day share before it shows
  in P&L.** XAUUSD 2026-02 → 2026-03: up-day share 66 % → 37 %, ADX
  22 → 15, monthly return +9.5 % → −12.6 %. `regime_profile.py` is
  now the first thing we run before any sweep to avoid mis-reading
  results.
- **Silence on validation means the strategy, not the parameters,
  is wrong.** The seed strategy doesn't take trades in the current
  regime because its SL sizing collides with the ¥100k lot cap at
  current volatility. No amount of `tp_rr` / `cooldown` tuning
  fixes that; it needs a different entry rule entirely. This is
  why the v3 plan says "discoveries are outputs, seed strategies
  are disposable".
- **Recent-regime sweeps need a wider research window.** With 19
  months of research and 2 months of validation, `score_on=validation`
  would happily reward a trial that took one lucky trade. The
  `--score-on validation` flag needs a companion filter
  "min validation trades" before it's trustworthy as the sort key.

## Phase 2

- **The walk-forward ratchet actually catches overfitting.** First
  real sweep on 2024 XAUUSD: research PF 1.50 → validation PF 0.33
  on the same parameters. Without the splitter + validation step
  we would have happily promoted a fake edge. Keep the ratchet
  tight; don't be tempted to loosen it when early candidates fail.
- **Dukascopy works as an HFM stand-in for research.** Spread is
  wider (~40 pts vs HFM's ~8 pts) and the pessimistic spread model
  in the engine already over-estimates costs. Net: a candidate that
  clears Dukascopy-based research will *probably* clear HFM, but
  the promotion-window test must still run on HFM data once we
  have it.
- **Lookahead bugs are easy to introduce via caches.** A swing at
  iloc `i` is only confirmable at bar `i + k`. When precomputing
  full-series masks in `prepare`, `on_bar` at bar `n` must query
  with `end_iloc_exclusive = n - k`. Missing this silently
  improves backtest metrics (I saw PF 1.42 → 0.90 change shape —
  not "better", just *different*, and the shape told me it was
  wrong). Added `test_perf_and_lookahead.py` as a regression guard.
- **Single-year data isn't enough.** A 2-month validation window
  with 18 trades is dominated by one losing streak. Next pull: 3
  years. Rule of thumb: aim for 50+ validation trades at a minimum.

## Phase 1

- **JPY lot cap collides with min_lot for multi-leg.** With plan v3's
  0.1 lot cap on ¥100k and an HFM min_lot of 0.01, a 50/50 leg split
  rounds to 0.05/0.05, which is fine; but a risk-% decision of 0.09
  lots splits into 0.045/0.045 which rounds *down* to 0.04/0.04 —
  losing 0.01 lots to rounding. The engine's current behaviour is to
  bias the leftover to the largest leg, which is acceptable but worth
  revisiting: for a ¥100k account, asymmetric leg weights (e.g.
  40/60) may waste less to rounding.
- **Consecutive-SL counter needs win-based AND day-based resets.**
  Either alone would be wrong: win-based-only wouldn't reset on a
  flat day with no trades; day-based-only would let a win-loss-loss
  sequence trigger the 2-SL review even though the trader was mid-
  correct. Both resets are required and tested.
- **Tournament-window protection works best with grep-able opt-in.**
  `i_know_this_is_tournament_evaluation=True` is ugly on purpose; it
  will surface immediately if anyone (including future-me) tries to
  use the tournament window in a tuning path.
- **Broker stays currency-agnostic; RiskManager translates.** The
  PaperBroker's P&L is in the instrument's quote currency (USD),
  and the engine converts at the call site via the FXConverter
  attached to the RiskManager. This keeps the broker interface
  uniform between backtest and live; MT5 reports JPY directly on a
  JPY account so no FX translation is needed there.

## Phase 0

- **MT5 Python API is Windows-only.** Abstracting behind a `Broker`
  interface is not optional — it is the only way to run backtests and
  CI on Linux.
- **Trend-pullback logic is surprisingly state-heavy.** A clean split
  helps: (a) a *trend detector* that only looks at swing pivots, (b) a
  *zone builder* that computes fib levels from the last impulse, (c) a
  *trigger* that fires when price enters the zone with a rejection
  candle. Mixing these inside one function gets unreadable fast.
- **Risk-% sizing must cap leverage separately.** On XAUUSD a 1% risk
  with a 50-pip SL can blow through 1:100 leverage on a small account.
  The sizer clamps to the leverage budget even if that means taking
  less than the configured risk.
- **Deterministic synthetic data is worth the effort.** It lets tests
  assert on exact numbers and lets CI gate regressions without a
  broker connection.
