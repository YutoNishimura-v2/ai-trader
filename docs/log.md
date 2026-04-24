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
