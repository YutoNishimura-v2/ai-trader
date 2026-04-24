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
