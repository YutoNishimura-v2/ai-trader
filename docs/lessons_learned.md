# Lessons learned

Append-only. One bullet per insight. Keep short; link to a PR or a
progress entry for the full story.

## 2026-04-26 iter29 — adaptive simulation

- **H4 protector validates the adaptive thesis.** A full-risk static
  growth stack still wins raw Jan-Apr return, but adding a low-risk
  H4 S1/R1 protector with concurrency=1 produced +832% full and
  +13.85% on the local 14d stress window, versus iter28's +497% /
  -4.32%. The market-state answer is not "one strategy"; it's
  specialist participation with careful exposure control.
- **Validation cap violations are the new blocker.** The best H4
  protector variant is excellent on full and recent stress but trips
  one validation cap violation. Loosening daily max loss raises full
  return but worsens cap/stress. Next work should address the
  specific validation loss day, not blindly change global risk.
- **Adaptivity is useful, but naive trailing-winner selection is not
  enough.** Rolling 5-day winner switching collapses to +47% full
  vs static growth +498%, with 33 switches. It reacts to noise and
  pays whipsaw cost. Adaptation needs a stronger state model than
  "pick recent P&L leader."
- **Expertise is regime-specific, not globally ranked.** Static H4
  pivot is weak full-period (+16.5%) but dominates the April stress
  window in local reproduction (+42.8% tournament 14d). A bad
  all-period expert can still be the correct defensive mode.
- **Causal adaptive policies can improve risk shape.** Expectancy
  rotation (+196.7%, DD -18.3%, min_eq 97.5%) sacrifices static
  growth's upside but materially improves drawdown profile. This is
  closer to live-demo behaviour: choose what is working, preserve
  capital when confidence is low.
- **The oracle gap is enormous.** Hindsight best-expert rotation is
  +7198% with tiny DD, proving the expert library contains enough
  diverse edge to matter. The unsolved problem is causal selection,
  not absence of any exploitable behaviour.
- **Friday is not universally bad.** Iter28 Friday-cut helped the
  growth stack, but H4 specialist attribution shows Friday was a
  major positive in April stress. Calendar filters must be
  strategy/context-specific, not global doctrine.

## 2026-04-25 iter28 — bold exploration

- **NY session adds independent alpha to pivot bounces.** We
  spent iter13-iter27 ONLY on session=london for pivot_bounce
  because the iter13 sweep showed NY-only as flat-or-worse.
  But `london_or_ny` (UNION, not NY-only) lifted both daily and
  weekly pivot members by ~+18 percentage points on the FULL
  window. **Always test the union, not just each member alone.**
- **Day-of-week is a strong, research-honest mechanical filter.**
  DoW profile of the new stack on RESEARCH ALONE showed Friday
  losing ¥-14k (8 trades, 25% winrate). Cutting Friday lifted
  full +286% → +497.94% with validation unchanged. The
  research-window justified the cut without peeking at
  validation/tournament.
- **Hour-of-day filters HURT, even when in-sample stats favor
  them.** Hours 7-8 looked slightly negative on full but cutting
  them dropped full from +497% → +170%. Each filter is a fitting
  parameter; deeper masks usually OVERFIT noise.
- **4-hour pivot is the only standalone with positive tournament
  in iter28** (+6.69% PF 1.20). Worth investigating in iter29.
- **The two-leg TP1+BE structure beats single-TP for GROWTH
  configs** (full +497% vs +361%/+465% for single-TP variants),
  even though single-TP wins for BALANCED configs (iter26).
  TP structure should match risk-tier intent.
- **Tournament hostility persists even at the new growth ceiling.**
  iter28's headline is at +497% full but -13.78% tournament.
  Across all iter24-iter28 growth-tier configs, tournament is
  consistently negative. The pivot family is structurally
  vulnerable to the specific April-2 weeks of 2026 in our data.

## 2026-04-25 iter9 — honest reset (price-action only)

- **Tournament-window peeking is selection bias.** iter5-iter7
  reads tournament 30+ times during sweeps, picking variants
  that did well on it. The reported tournament numbers (+148-206%)
  are NOT honest held-out. Restoring plan v3 §B.3 (tournament
  opened ONCE per strategy family) costs reported headlines but
  protects against false promotion. iter9's single-shot
  tournament is +5.41% — small but real.
- **Risk per trade has a sweet spot specific to the strategy's
  edge.** session_sweep_reclaim has small per-trade edge
  (val PF 1.49 at small risk). At the user's 2.5% sizing, that
  same strategy goes -16% full-period: bad-trade losses scale
  faster than good-trade gains. At 1.0% risk inside an ensemble
  with regime_router, the same strategy delivers val +20%
  PF 2.33. Lesson: lifting risk only works if the per-trade
  edge is robust enough to survive amplification.
- **Mechanical pattern detection cannot replicate discretionary
  contextual judgement.** The user's recipe (HH/HL trend → fib
  pullback → rejection candle → wide SL → BE on TP1) was
  built faithfully in `fib_pullback_scalper` and produced
  full -73% / val +1.72% (PF 1.04). The user's discretionary
  edge depends on a "skip-or-trade" decision the algorithm
  doesn't have. Conclusion: don't expect mechanical
  implementations of discretionary recipes to match the user's
  manual results.
- **Hand-curated news calendars introduce subtle selection bias
  even when the events themselves are real.** In iter5-7, my
  choice of which mid-impact events to include in
  xauusd_2026_full.csv was probably biased toward ones that
  "felt" tradeable. The user correctly retired this approach.

## 2026-04-25 push-to-200% iter3 (ALL MONTHS POSITIVE)

- **The "chop edges" (sweep_reclaim, friday_flush) are
  underutilized in iter2 v6.** Boosting their multiplier in
  range/transition regimes (range 1.30→1.50, transition 1.00→1.20)
  AND letting sweep_reclaim fire 3 trades/day (was 2) lifted Jan
  from -1.4% to +29.2% and full Jan-Apr from +150% to +232%.
  The Jan/Mar drag wasn't a strategy failure — it was a
  member-multiplier failure for the strategies that work in chop.
- **`min_equity_pct=100` over a full 4-month run is NOT
  unreachable.** v7_chop_robust never dipped below starting
  balance on the full Jan-Apr backtest. The early-session
  winners (sweep_reclaim) get the day to a profit before the
  tail-of-day losers (sometimes news_continuation) can drag.
- **Symmetric-knob exploration: tighten kill-switch BEFORE
  bumping risk multipliers.** Each iter2-iter3 step that
  improved performance involved tightening daily_max_loss FIRST
  (10→7→5→4) then bumping the active-day multipliers. The
  tighter kill is what enables the higher per-trade risk to
  remain safe.

## 2026-04-25 push-to-200% iter2 (200%/mo CLEARED)

- **Stack opposite-sign edges on the same trigger.** news_fade
  fires the post-event snap-back; news_continuation fires the
  post-event sustained move. Per event one of them wins, the
  other never enters because triggers are mutually exclusive.
  Adding news_continuation transformed the ensemble from
  +80.5%/full to +123% then to +148-159% as we layered more
  variants.
- **Multiple instances of the same strategy at different
  parameters can BOTH add value.** Three news_continuation
  members with different (trigger_atr, confirm_bars) catch
  different post-event price patterns. Each is essentially a
  different "filter" on the same calendar. Triple-NC delivered
  full +150% / Apr standalone +175%, beating dual-NC +159% /
  +119% and quad-NC +64% (over-stuffed).
- **Tighter daily kill-switch + tighter DD throttle PUSHED
  performance UP, not down.** Counter-intuitive: cutting daily
  loss from 10% to 5% and DD throttle from 18%/32% to 8%/14%
  raised tournament 14d from +40% to +94%+. Why: aggressive
  losing streaks in research now get throttled before they
  compound, so subsequent winning streaks start from a higher
  balance.
- **Concurrency=2 BEATS concurrency=1 once you have FOUR+
  members.** With 6+ members in v6_triple, multiple edges can
  fire on the same news event. concurrency=1 forces only one
  to play; concurrency=2 captures the aligned-bias amplification.
  (This is the opposite of the iter1 finding — context: with
  3 members concurrency=1 won; with 6 concurrency=2 wins.)
- **The 200%/month aspiration is achievable on a
  trend+news-rich calendar month.** April 2026 standalone:
  +175.07% on real held-out M1 XAUUSD with 0 cap violations and
  min equity 88.2%. The number is REAL on this dataset. The
  caveat is that it's REGIME-CONTINGENT — Jan/Feb/Mar standalone
  numbers are much lower (+0% to +23%).

## 2026-04-25 push-to-200% iteration

- **SIZE the trade, do not GATE it.** Naive HTF EMA / ADX gating
  bars `session_sweep_reclaim` from trend regimes — but the
  April tournament window is ~41 % trend by M15 ADX, so the gate
  throws away 40 % of the day. Instead, let the strategy fire in
  every regime and pass a per-regime risk_multiplier through the
  signal meta. v2's regime_risk_multipliers={range:1.30,
  transition:1.00, trend:0.70} recovers April while killing the
  Jan/Mar drag.
- **Concurrency=1 can BEAT concurrency=2 with a richer signal
  source.** With xauusd_2026_full.csv (64 events vs 25), news_fade
  and session_sweep_reclaim try to overlap on event days.
  Concurrency=2 lets both fire and doubles risk on already-volatile
  days; concurrency=1 forces a priority queue (news_fade wins,
  sweep waits) and ends up SAFER and HIGHER EV. Counter-intuitive
  but reproducible.
- **Hidden plumbing bugs invalidate whole sweeps.**
  `scripts/quick_eval.py` was silently dropping all
  dynamic-risk-meta kwargs when constructing `RiskManager`. Every
  ultimate_regime_meta variant produced identical numbers
  regardless of what the YAML said. Caught only by checking that
  v1/v2/v3 — which had visibly different YAMLs — produced
  identical metrics. Lesson: when sweeping a config knob and
  results don't move, BISECT the wiring before assuming the knob
  is dead.
- **M15 EMA bias is a lagging indicator at the entry bar.**
  `asian_breakout` was the trend-day complement to
  session_sweep_reclaim. Both gated variants lost across every
  window. The root cause: by the time fast-EMA > slow-EMA on M15
  ADX > 22, the trend's momentum has been spent or the price has
  retraced into the Asian range. M15 trend confirmation works as
  a FILTER on counter-trend strategies (skip the bad ones), not
  as a TRIGGER for trend-following ones.
- **A standalone-falsified strategy can still earn its place
  inside an ensemble.** `news_fade_full` standalone has tournament
  14d +3.0 % (down from rich-only +9.3 %); per binary rule it's
  falsified. But inside `ensemble_ultimate_v2`, the same
  full-calendar input lifts the FULL Jan-Apr from +44 % (v8 with
  rich-only) to +80.5 % (v11 with full). The mid-impact events
  fire in regimes where the high-impact-only sweep was idle, and
  the regime-meta layer protects the bad ones. Lesson:
  ensemble-level metrics are NOT the sum of standalone-component
  metrics. Always re-test the full ensemble after a component
  change.
- **Per-month / interleaved stress catches survivor bias the
  recent_only split misses.** Baseline ensemble_ultimate looks
  great on the recent_only April tournament (+66.9 %) but earns
  only +1.34 %/block on interleaved tournament (1/3 positive). v2
  is +8.72 %/block (2/3 positive). The recent_only split rewards
  April-friendly strategies; interleaved punishes them.

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

- **HTF EMA-bias / ADX gating did not preserve the
  `session_sweep_reclaim` April edge.** Three gating modes (`with`,
  `neutral_or_with`, `skip_counter_trend`) and a chop-only ADX
  ceiling all *kill* the standalone tournament return. The strategy
  is fundamentally a counter-trend mean-reversion: any pro-trend or
  range-only filter removes the trades that produce its profit.
  The honest next move is **risk sizing by HTF ADX**, not boolean
  signal gating: keep firing in trend regimes but cut lots 2-4×.
- **Calendar-driven uncorrelated edges stack cleanly.** Adding
  `friday_flush_fade` (Fri 18:30-20:00 UTC anchor fade) on top of
  rich-news `news_fade` + `session_sweep_reclaim` lifted the held-out
  14d tournament from +42.4 % to +66.9 %. The two new edges fire on
  disjoint hours/weekdays from each other and from the session
  sweep, so concurrency=2 actually opens both books on the few
  bars where they overlap.
- **Aspiration framing matters; honest extrapolation matters more.**
  The user's +200 %/month target is ~3.5 %/day compounded. The best
  held-out 14d is 4.7 %/day pace, but the same configuration
  delivers -17 % across each of two trending months. Short-window
  extrapolations of "X %/day" overstate sustainable monthly results
  by a factor of 5-10× during regime mismatch. Always report
  full-period monthly mean alongside the held-out window pace.
- **Pre-event drift fade (`news_anticipation`) is a noise edge.**
  Validation positive on the looser trigger config, validation
  negative on the stricter config; tournament negative on both. Same
  shape as bb_scalper / volume_reversion / vwap_reversion: a
  price-action fitting artefact, not edge. Kept in registry for
  future MTF-gated work but explicitly excluded from
  `ensemble_ultimate.yaml`.

- **News-fade was the first walk-forward winner.** After 7
  strategy families across price-action variants (BB, BOS, trend
  pullback, liquidity sweep, MTF-ZZ-BOS, London ORB, VWAP, BB
  with volume confirmation), the only strategy that cleared
  research + validation + tournament with positive returns was
  the calendar-driven news_fade. Lesson: when price-action edges
  vanish under tight risk, look at non-price-action information
  (events, calendar, volume).
- **VWAP validation can deceive.** VWAP best trial showed
  validation PF 1.48 on 67 trades. Tournament collapsed to
  PF 0.08 on 19 trades (7d) but recovered to PF 0.93 on 47
  trades (14d). Always tournament-eval at a couple of window
  lengths to gauge real variance before believing a result.
- **Strategies with structural SLs need an SL cap.** london_orb's
  Asian-range opposite extreme can be $50+ on M1 XAUUSD; at
  0.5% risk × $10k = $50 budget, position rounds below min-lot
  and signals get silently rejected. `max_sl_atr=2.0` cap fixed
  it.
- **Day-rollover state needs to be at the top of on_bar.** Bug
  in london_orb where window-end was set inside the "range
  done" branch produced 0 trades on every day. Lesson: state-
  machine resets should always be unconditional at the top.

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

- **GOLD-only sweep-and-reclaim is the best new recent-period edge.**
  After the user narrowed scope back to XAUUSD and allowed higher
  risk, a London-session sweep/reclaim strategy (Asian range stop
  hunt → close back inside → continuation to opposite edge) cleared
  the held-out recent tournament: +7.9% over 14 days / PF 2.65 /
  DD -5.9%, and +9.25% over the last 7 days / PF 5.52 / DD -5.8%.
  This is not 200%/month, but it is materially stronger recent
  tournament performance than the previous news-only floor.
- **Validation winners still routinely fail the freshest window.**
  In the first high-risk GOLD batch, VWAP (+29% validation) and BOS
  (+21% validation) both collapsed on the 14-day tournament (-19%
  and -20%). Higher risk amplifies the familiar validation→tournament
  noise problem; ruin metrics must accompany every leaderboard.
- **Post-news continuation did not fire enough with strict retest
  rules.** The new `news_breakout` strategy is coded and tested, but
  the first strict batch produced zero validation trades. After adding
  a richer calendar, news_breakout did take trades but failed the
  latest tournament (-2.2% / PF 0.52). The continuation thesis remains
  weaker than news-fade on this sample.
- **Richer predeclared events materially improve news-fade.** Adding
  PPI, retail sales, ADP, JOLTS, consumer confidence and extra ISM
  releases roughly tripled event opportunities. The best rich-calendar
  news_fade config returned +24.7% full Jan-Apr and +9.3% on the
  14-day tournament with only -2.5% DD. Calendar breadth is a real
  edge multiplier when added pre-test, not selected event-by-event.
- **Session sweep + rich news is the strongest GOLD-only stack so far.**
  The ensemble of the 5% London sweep/reclaim and rich-calendar
  news_fade returned +46.5% over Jan-Apr and +42.4% on the 14-day
  tournament (min equity 95.2%). This is the first result in the
  project that enters the lower end of the user's 50-100%/month
  ambition on current-regime data while staying far from zero-cut.
- **Naive ADX regime routing can remove the edge.** A first
  M15-ADX `regime_router` improved full Jan-April return (+19.1%,
  3/4 profitable months) by filtering older bad periods, but the
  same router failed the latest 14-day tournament (-6% to -10%).
  Lesson: for current-regime optimization, do not accept a router
  just because it improves full-history smoothness; it must preserve
  the latest held-out edge.
- **Squeeze breakouts are not enough on M1 GOLD.** A Bollinger/
  Keltner compression-release strategy was added and swept. Most
  research and validation cells were negative; the best validation
  candidate (+3.3%) failed the 14-day tournament at -16% / PF 0.54.
  Compression alone creates too many false continuation entries
  under M1 costs; it needs stronger MTF/session context or should
  stay shelved.
- **Naive impulse-pullback continuation is worse than expected.**
  A direct discretionary-style impulse → fib pullback → rejection
  strategy produced hundreds of M1 signals, but every validation cell
  was negative (best still -12.3%). The user's intuition about
  momentum pullbacks likely needs higher-timeframe trend/structure
  context; a single-candle impulse trigger on M1 is just noise.

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
