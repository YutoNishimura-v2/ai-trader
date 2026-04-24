# Plan — AI GOLD Trading Bot (MT5)

This file is the single source of truth for the *specification* of the bot.
Edit it whenever the scope or rules change. All code decisions must trace back
to something in this document.

## 1. Objective

Build a fully automated XAUUSD (GOLD) trading bot that connects to
MetaTrader 5, earns **steady, consistent profits**, and minimizes losses.
Explosive gains are explicitly **not** a goal.

## 2. Success criteria (quantitative)

A candidate strategy is considered "promising" and eligible for demo only
if, over at least **6 months** of M5/M15 XAUUSD history:

- Profit factor ≥ 1.4
- Max drawdown ≤ 10% of starting equity
- Sharpe (daily) ≥ 1.0
- Expectancy per trade > 0 after spread/commission
- Win rate is *not* optimized; instead we optimize **R-multiple expectancy**
- At least 100 trades in the window (to limit overfitting)

These thresholds can be retuned as we learn, but changes must be recorded in
`docs/lessons_learned.md`.

## 3. Trading rules (non-negotiable)

These are user-imposed rules. The risk manager enforces them in code; a
violation must abort trading, not be "softened".

1. **Leverage cap:** effective leverage on open exposure ≤ **1:100**.
2. **Daily profit withdrawal:** half of realized daily profit is swept to a
   "withdrawal" sub-ledger at end of session and not available for sizing.
3. **Pullback only:** entries must be on a retracement of the prevailing move,
   never naïve breakout chasing or "spam" trades.
4. **Daily profit target + max daily loss:** once either is hit, the bot flat-
   tens open positions and stops for the day.
5. **Slippage & spread realism:** every backtest must apply spread and a
   configurable slippage model.
6. **No martingale, no grid.** Position size is derived from risk %, not from
   prior P&L.

## 4. Strategy taxonomy

We split strategies into three families, matching the user's discretionary
style:

- **A. Trend-pullback (Fibonacci 38.2 / 50.0).** Primary strategy.
- **B. Zone reversal.** Counter-trend entries at swept liquidity / long-wick
  zones. Secondary.
- **C. Pattern break.** Triangle / neckline breaks with retest. Tertiary.

Phase 1 ships **A** only. B and C are stubbed in the strategy registry so
they can be added without plumbing changes.

## 5. Architecture

```
ai_trader/
  data/         market data loaders (MT5, CSV, synthetic)
  indicators/   pure functions on DataFrames (swings, trend, fib, ATR, zones)
  strategy/     BaseStrategy + concrete strategies -> Signal objects
  risk/         RiskManager (daily limits, sizing, leverage cap, withdrawal)
  broker/       Broker interface; PaperBroker (sim) + MT5LiveBroker (real)
  backtest/     event-driven engine, metrics, reporting
  live/         real-time runner that wires strategy + risk + broker
  utils/        logging, JSON state persistence
```

Key invariants:

- Strategies are **stateless w.r.t. the broker**. They emit `Signal` objects;
  the risk manager sizes them; the broker executes.
- The same `Strategy` class is used for backtest, demo, and live. The only
  thing that changes is the `Broker` implementation.
- All configuration lives in `config/*.yaml` and is loaded via
  `ai_trader.config.load_config`.

## 6. Self-improvement loop

Implementing the user's loop:

0. **[DONE: scaffold]** Demo environment ready (this PR).
1. Backtest the current strategy registry on recent history.
2. Append the run summary to `docs/progress.md` and the raw metrics JSON to
   `artifacts/runs/<timestamp>.json`.
3. Inspect failure modes; write findings to `docs/lessons_learned.md`; add
   concrete follow-ups to `docs/todo.md`.
4. If acceptance criteria (§2) are met, promote to demo (MT5 demo account).
5. Watch live demo; loop.

Every iteration **must** produce at least one entry in `lessons_learned.md`
even if negative, so context survives across sessions.

## 7. Open questions (to resolve before Phase 2)

- Which broker/server provides demo XAUUSD data? (affects spread model)
- Commission model: per-lot fixed or spread-only?
- Are news-time blackouts required? (NFP, CPI, FOMC)
- Session filter: London + NY overlap only, or 24h?
- Currency of account: USD assumed; confirm.
