# Session log

Append-only chronological log of what was done in each working
session. Lightweight; mirrors git log but with intent, not diff.

## 2026-04-24

- Kicked off project. Agreed with user on the self-improvement loop:
  spec → backtest → review → iterate → demo.
- Shipped Phase 0: full runnable demo/backtest environment with one
  concrete strategy (`TrendPullbackFib`), paper broker, MT5 adapter
  stub, synthetic data, metrics, and tests.
- No real money, no live connection. All numbers so far are on
  synthetic data and exist only to prove plumbing.
- Three rounds of spec review with user. Landed on plan v3:
  constraints vs. discoveries are now separate; multi-leg position
  management (TP1/TP2 + break-even) is a framework feature;
  mandatory daily reviews; HFM Katana / ¥100k / XAUUSD + BTCUSD.
- Phase 0 code on PR #1 will be reworked to match v3 before any
  strategy tuning begins. Framework before strategies.
- Phase 1 (framework) shipped in one session: multi-leg + BE,
  JPY-native accounting, walk-forward splitter, bounded sweep
  harness, crash-safe state, review triggers + packets, news
  blackout, BTCUSD config. 62 tests green. No strategy tuning
  yet — that's Phase 2 and it needs real HFM data first.
- User asked about Windows / VPS. We agreed to unblock Phase 2
  with Dukascopy data (cross-platform) while waiting for HFM's
  free-VPS response. Windows becomes necessary only for Phase 3
  live demo.
- User also asked for perf work ("the verification was taking
  quite a long time"). Profiled, vectorised find_swings, added
  BaseStrategy.prepare hook. ~9× speedup on both pytest and
  backtest runtime; caught a look-ahead bug in the first cache
  draft.
- First real backtest on Dukascopy XAUUSD 2024: seed strategy
  overfits (research PF 1.50 → validation PF 0.33). Framework
  caught it as designed. Next: more data + regime router.
- User: prioritise recent-regime performance; Mar 2026 onward is
  a new regime where the seed edge may have vanished.
- Pulled 2024-06 → 2026-04 (134k bars). regime_profile.py confirms
  the regime shift: Feb 2026 up-day 66% → Mar 2026 37%, ADX 22 →
  15, monthly ret +9.5% → -12.6%.
- Sweep with tournament=last-30-days, validation=60-days-before:
  seed strategy takes 0-1 trades in validation. Not a losing
  strategy in the new regime — a silent one. Lot-cap + fib-zone
  calibration collide with current volatility. Needs a different
  strategy, not different parameters.
- Tightened sweep ranking: trials with fewer than
  --min-validation-trades (default 20) are demoted regardless of
  headline metric. Plan v3 says I can tighten the ratchet
  autonomously.
