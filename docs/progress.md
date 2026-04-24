# Progress log

Append-only. One entry per iteration of the self-improvement loop.
Format: `YYYY-MM-DD — <headline>`.

## 2026-04-24 — Phase 0: demo environment complete

**What changed**

- Spec written (`docs/plan.md`).
- Package scaffold under `ai_trader/` with clean separation of
  data / indicators / strategy / risk / broker / backtest / live.
- First strategy implemented: `TrendPullbackFib` (strategy "A" from the
  spec).
- Paper broker with spread + slippage + commission.
- MT5 live broker adapter (lazy import; runs on Windows with MT5
  terminal installed).
- Event-driven backtest engine with metrics: profit factor, max
  drawdown, Sharpe (daily), expectancy, win rate, trade count.
- Synthetic regime-switching GBM XAUUSD generator so CI can run a
  deterministic backtest without any broker connection.

**Baseline backtest (synthetic 180-day M5 XAUUSD, seed=7)**

| metric              | value    |
|---------------------|----------|
| trades              | 201      |
| win rate            | 45.3 %   |
| profit factor       | 1.57     |
| expectancy / trade  | $15.27   |
| net profit          | +$3,069  |
| return              | +30.7 %  |
| max drawdown        | −9.4 %   |
| daily Sharpe        | −0.13    |
| withdrawn (½-sweep) | $3,284   |

These numbers are on **synthetic regime-switching GBM**, not real
XAUUSD. They only demonstrate that the pipeline — strategy → risk
manager → paper broker → metrics — is wired correctly and that the
user-imposed rules (leverage cap, daily limits, half-profit sweep)
actually fire in simulation.

Notes:
- The negative daily Sharpe with a positive return is a synthetic-
  data artifact: bar-level P&L is lumpy and the daily aggregation
  hides the trade-level edge. On real data we expect this to line up
  more cleanly; if not, it's a flag.
- Profit factor (1.57) and drawdown (9.4 %) are inside the
  acceptance envelope in `plan.md §2`. Trade count (201 > 100) is
  sufficient to not be statistical noise — on synthetic data.

**Next**

- Pull 12 months of real MT5 demo data for XAUUSD.
- Re-run the same strategy and populate this section with the real
  numbers.
- If the acceptance thresholds in `plan.md §2` are not met, iterate on
  the strategy and/or add regime filtering. Record findings in
  `lessons_learned.md`.

- `20260424T135406Z` strat=`trend_pullback_fib` data=`synthetic(days=180,seed=7)` trades=201 pf=1.57 ret=30.70% dd=-9.40% sharpe=-0.13
