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
