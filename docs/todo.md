# TODO

Living task list. Anything crossed out moves to `progress.md`.

## Phase 0 — Demo environment (current PR)

- [x] Write spec (`docs/plan.md`)
- [x] Repo scaffold (`ai_trader/`, `tests/`, `config/`, `scripts/`)
- [x] Indicators: swings, trend-state, Fibonacci retracement, ATR, zones
- [x] BaseStrategy + `TrendPullbackFib` (strategy "A")
- [x] RiskManager: risk-% sizing, daily target/loss, leverage cap,
      half-profit withdrawal, kill-switch
- [x] Broker interface + `PaperBroker` (spread + slippage + commission)
- [x] `MT5LiveBroker` adapter (stubbed: imports MetaTrader5 lazily,
      Windows-only at runtime)
- [x] Event-driven backtest engine + metrics (PF, DD, Sharpe, expectancy)
- [x] Synthetic XAUUSD OHLCV generator (regime-switching GBM) for
      deterministic CI runs
- [x] CLI: `scripts/run_backtest.py`, `scripts/run_demo.py`,
      `scripts/fetch_mt5_history.py`
- [x] pytest suite (indicators, risk, engine, strategy)
- [x] CI-ready Makefile

## Phase 1 — First real backtest pass

- [ ] Pull 12 months of real M5 XAUUSD from MT5 demo
      (`scripts/fetch_mt5_history.py`) on a Windows host and commit the
      CSV to `data/` (or a release asset).
- [ ] Run `TrendPullbackFib` on that data; record metrics in
      `docs/progress.md`.
- [ ] Parameter sweep (swing lookback, fib level, SL ATR mult, TP mult).
- [ ] Add session filter (London/NY overlap).
- [ ] Add news blackout filter (CSV of high-impact events).

## Phase 2 — More strategies

- [ ] Strategy **B**: zone reversal on M15 with ATR-sized wick filter.
- [ ] Strategy **C**: triangle break + retest.
- [ ] Ensemble / regime router (trend vs range classifier).

## Phase 3 — Live demo

- [ ] Windows VM provisioned with MT5 demo account.
- [ ] `run_demo.py` runs the best strategy on the demo account,
      writes trade log to `artifacts/live/`.
- [ ] Daily report poster (writes a markdown summary to `docs/log.md`).

## Open questions (see `plan.md §7`)

- [ ] Confirm broker / demo server for XAUUSD.
- [ ] Confirm commission model.
- [ ] Confirm news blackout requirement.
