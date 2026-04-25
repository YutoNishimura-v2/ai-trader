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

- **MTF + ZigZag produces the highest-quality signals to date.**
  mtf_zigzag_bos with M5 ZigZag bias + M1 BOS-retest produced
  the best validation PF (1.47) AND tightest DD (~3-5 %) of any
  strategy. But the high-confluence requirement makes signals
  rare (~17 in 14 days), so tournament's 18-trade sample is
  noise-dominated. Lesson: signal quality and statistical power
  are in tension — a stricter strategy has cleaner trades but
  needs proportionally more data to validate.
- **MTFContext design pattern: index by close_time.** A common
  MTF lookahead bug is to query the HTF bar at M1 time t and
  get the still-forming bar back. Indexing the HTF frame by
  close_time and using searchsorted-with-`right`-then-minus-1
  gives "most recent bar fully formed at t". Tested + locked.
- **ZigZag pivot vs confirmation must be separated.** A pivot's
  iloc is the extreme bar; its confirm_iloc is when the
  threshold-reversal made it visible to a live trader. Querying
  with confirm_iloc as the cutoff matches live behaviour;
  using iloc would be a lookahead.

- **Interleaved splits are the most honest test.** Contiguous
  Jan/Feb-research / Mar/Apr-validation puts each role in one
  regime; if the regimes are different, you're just measuring
  regime-mismatch. Block-based round-robin makes both roles see
  every regime. The ensemble's validation PF jumped from
  unhelpful to 1.16-1.48 under interleaved — that's where its
  real signal showed up.
- **Recent-only is the loudest test for current-regime edge.**
  Compresses everything into the last ~35 days. Found the highest
  validation PF (1.34) we've seen, but tournament was 7 days of
  statistical noise — strategies that look great on validation
  can lose -5 to -11% on the next 7 days. **Window length floors
  matter:** ~7 days is too short for a meaningful tournament at
  ~20-30 trades/day.
- **4 strategy families, 0 clean walk-forward winners.** Trend-
  pullback (EMA + fib), BOS retest, BB scalper, liquidity sweep
  — all show the same pattern: validation PF mildly positive in
  some trials, research PF below 1, tournament noise. This isn't
  one bad strategy; it's evidence that **simple price-action
  scalping on M1 XAUUSD doesn't have an exploitable edge under
  tight risk discipline**. The honest paths forward are: add
  information beyond OHLC, try entirely different edges (news,
  calendar, multi-instrument), or accept the gap.

- **Default-off feature flags rot.** `BBScalper.use_two_legs`
  defaulted to False and the yaml never set it. Every BB result
  reported across multiple iterations was running without the
  break-even feature I'd added two PRs earlier. Going forward:
  any strategy with single-leg and 2-leg modes should default to
  2-leg+BE, and configs should declare it explicitly so it can't
  be silently dropped.
- **Bug fixes can retroactively invalidate every prior backtest.**
  The kill-switch intra-bar fix was correct, but re-running prior
  winners shows they'd been benefiting from the leak: positions
  that should have been flushed at the cap were running into the
  next bar and netting out. The honest numbers are 10-20 %-points
  worse on tournament return than I reported. Rule: any change
  to engine semantics must trigger a re-evaluation of every
  prior "winning candidate" before any further claims.
- **Hard TP/SL and entry-at-next-open were always correct.**
  Locked as invariants now (`test_fills_intra_bar.py`).

- **Kill-switch must flatten on the same bar, not the next.**
  Found by the new `cap_violations` metric: BB @ risk=1 % had one
  day close at −10.54 % because when a losing trade tripped the
  cap, another open position sat exposed for a whole bar before
  being flushed. Fix: flatten all open positions at the current
  bar's close whenever the kill-switch fires mid-bar. Residual
  ~50 bp cap overshoot is bar-granularity physics and matches
  real broker slippage on fast moves.
- **Monthly return is the real scoreboard, not PF.** The user was
  right: PF tells us about trade-level edge but not whether we
  net a profitable month. After switching scoring to
  `monthly_pct_mean`, several "winning" configs from earlier
  sweeps turned out to have negative or flat monthly means.
- **Regime-matching effects dominate small tournament windows.**
  BB scalper looked like a winner on a 12-day tournament that
  happened to be choppy (its ideal regime). On the full 4-month
  2026 window, it loses money in the months that trend hard
  (Jan/Feb 2026). Lesson: a 12-day tournament is not a full
  picture; always report full-period monthly returns too.
- **Risk-% has a ceiling, not a slope.** BB @ risk={1,2,3,4}%:
  returns peak at 2 % and fall at 3 %+. Higher risk-% doesn't
  just increase variance; it decreases expected return because
  losing trades compound faster than winners and eat the edge.
- **+30 % daily target is reachable at 3 %+ risk.** BB @ risk=3 %
  produced best-day of +26.6 %, risk=4 % produced +26.6 %. The
  user's target is a real possibility on lucky days at these
  risk levels — but the average monthly return gets worse, not
  better. The daily target is not a good optimisation target.

- **Search the literature first.** Before building
  `bos_retest_scalper` I searched for BOS / CHoCH / SMC / ICT
  scalping patterns. Hit rate for useful signal was good: CHoCH
  invalidation rule, multi-timeframe confluence, spread ≤ 10–12
  pts, session gating — none of this was wasted reading. It
  stopped me from rolling a fifth half-baked trend detector.
- **Stacked filters can silence a strategy without anybody
  noticing.** First BOS sweep stacked session + 2HH + 2HL +
  BOS-close + retest + rejection + CHoCH invalidation on M1.
  Every validation window had <30 trades. Loosen one gate at a
  time to diagnose; dropping the session filter + using
  `swing_lookback=4` was what unlocked real signal.
- **M1 fractals need a tiny lookback.** `swing_lookback=10` on
  M1 confirms a pivot 5 minutes late, by which time price has
  often reversed again. `swing_lookback=4` is noisy but *produces
  enough confirmed pivots for the structural rules to apply*.
  That trade-off didn't show up on higher timeframes.
- **First regime-agnostic candidate found.** BB scalper
  (chop-loving, 11 trades/day) + trend-pullback scalper (trend-
  loving, dies in chop) + BOS-retest (works in both, low
  frequency). The ensemble approach is now the obvious move;
  all three have in-regime edge.

- **Regime dependency is detectable on the tournament window.**
  Trend-pullback scalper cleared research (PF 1.43) and
  validation (PF 1.12) with low DD, then failed the tournament
  (PF 0.79). A same-day regime check of the tournament window
  showed it was range-bound (+1.44 % over 12 days, ranges
  $50-162). Trend strategies need trending regimes — obvious in
  hindsight, but the tournament ratchet caught it anyway. The
  lesson: every tournament failure deserves a regime-of-window
  diagnostic before we blame the strategy.
- **"Let winners run" validates on real data.** Sweeping
  `tp2_rr ∈ {2.0, 3.0, 4.5}` on the trend-pullback scalper: all
  three cleared research + validation gates with similar DD. The
  4.5R variant was best on validation PF. Hard-capping profit at
  1R (what the BB mean-reversion target does by design) is not
  free — it leaves edge on the table in trending regimes.
- **Complementary strategies need a router, not a choice.** BB
  scalper (research PF 1.14, tournament 1.14) wants chop;
  trend-pullback scalper (research PF 1.43, tournament 0.79)
  wants trend. Picking one over the other is wrong; the right
  move is a regime classifier that routes bars to the appropriate
  strategy. Both candidates have demonstrated in-regime edge.

- **DD metric bug: withdrawal sweep inflated drawdowns by tens of
  percent.** Equity was `balance + unrealized`; it needed
  `balance + unrealized + withdrawn_total` because §A.9 moves
  profit *out* of balance as a ledger transfer, not a loss. Every
  "−78 % DD" number before this fix was partly artefactual. Fixed
  and locked with a regression test.
- **Strategy family must match the spec.** Plan v3 says "direction
  on M5, entry on M1" — that is a scalping spec. I built two swing
  strategies (trend-pullback, Donchian-retest) before noticing. A
  swing strategy on a scalping spec will always look "too quiet"
  because it is too quiet, *by design*. Match the family to the
  spec up front.
- **Dead-regime history is noise, not data.** The 19 months of
  pre-Feb data came from a regime where "buy anything" won. Using
  it as research when the current regime is completely different
  just leaks noise into the search. Narrowing to 2026-only
  (trading 19 months of signal for 4 months of relevance) turned a
  consistently-losing BB scalper sweep into a genuine candidate.
- **config `extends:` needs a replace escape hatch.** Deep-merging
  `strategy.params` across files leaks foreign keys into the next
  strategy's constructor (TypeError on `swing_lookback` when loading
  `bb_scalper`). Added `__replace__: true` sentinel so subtrees
  can opt out of merge.
- **First genuine candidate found when all three gates held.** PF
  1.14 research / 1.37 validation / 1.10 tournament, DDs under
  25 % on all three, scalping frequency confirmed. The discipline
  (walk-forward + min-trade floor + tournament held until the end)
  was worth the pain.

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
