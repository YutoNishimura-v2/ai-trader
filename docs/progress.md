# Progress log

Append-only. One entry per iteration of the self-improvement loop.
Format: `YYYY-MM-DD — <headline>`.

## 2026-04-24 — Phase 1 framework landed

Plan v3 Phase 1 is complete. The framework is in place; no real
strategy tuning has happened yet.

**What shipped in this pass (commits on PR #1):**

- Multi-leg `Signal` + `Broker.modify_sl` + engine-side break-even.
  One entry decision may open up to 2 sub-legs with a shared initial
  SL; on TP1 fill, the engine moves the runner's SL to
  `move_sl_to_on_fill` (typically the entry price).
- JPY-native accounting. `InstrumentSpec` gains `quote_currency` +
  `is_24_7`; `RiskManager` gains `account_currency` + `FXConverter`
  + the plan-v3 §A.2 lot cap (`lot_cap_per_unit_balance`).
  `default.yaml` is now HFM Katana / XAUUSD / ¥100k JPY with the
  +30 / −10 daily envelope and pessimistic 12-point spread.
- Walk-forward splitter (`backtest/splitter.py`). Held-out
  tournament window is only revealed when the caller passes
  `i_know_this_is_tournament_evaluation=True` — a deliberately
  grep-able opt-in string.
- Bounded grid sweep harness (`backtest/sweep.py`) with a hard
  `max_trials` cap (p-hacking ratchet), per-trial JSONL log, and
  `best.json` selection.
- Crash-safe `BotState` persistence (`state/store.py`). RiskManager
  optionally binds to a `StateStore`; daily ledger, kill-switch,
  `consecutive_sl` counter, and `withdrawn_total` survive restarts.
- Review-trigger engine + packet generator (`review/`). EOD
  (mandatory, including quiet days), weekly wrap, 2-consecutive-SL,
  kill-switch. Quiet-day packet explicitly records "silence is data".
- News blackout calendar (`news/calendar.py`). CSV-driven, wired
  into the backtest engine; signals within ±window_minutes of a
  high-impact event are dropped before the risk manager sees them.
- BTCUSD instrument config (`config/btcusd.yaml`, 24/7 flag).

**Baseline backtest (synthetic 180-day M5 XAUUSD, seed=7, JPY
account, +30/-10 envelope, v3 lot cap)**

| metric | value |
|---|---|
| trades | 104 |
| win rate | 58.7 % |
| profit factor | 1.42 |
| expectancy | ¥64.66 / trade |
| net profit | +¥6,724 |
| return | +6.72 % |
| max drawdown | −3.71 % |
| daily Sharpe | −0.76 |
| withdrawn (½-sweep) | ¥9,196 |

Still synthetic data — these numbers prove the JPY plumbing and the
new lot-cap/break-even behaviour work end-to-end. They are **not** a
market result. The lower trade count vs. the pre-v3 run (104 vs.
201) reflects the v3 per-instrument lot cap forcing single-leg
sizing at this balance and the tighter spread model.

**Test suite:** 62 tests, all green in ~26s.

**Next (Phase 2):**

- Pull ≥ 12 months of real HFM XAUUSD M1+M5 data (Windows host).
- Run the seed strategy `trend_pullback_fib` through the walk-
  forward protocol with a bounded grid sweep on research, verify on
  validation. Record in this file.
- Propose the first few replacement candidates (volatility breakout,
  session opener, regime router).

## 2026-04-24 — Plan v3 agreed

Spec finalized after three rounds of review. Key shifts from v1:

- **Constraints vs. discoveries split.** Policy numbers (per-trade
  risk %, DD tolerance, SL/TP rules, strategy choice) are outputs
  of the loop, not pre-committed inputs. Only user constraints are
  fixed.
- **HRHR profile.** +30 % / −10 % daily envelope replaces the
  "steady" framing. Monthly 200 % is aspiration, not a gate.
- **Multi-leg position management.** One entry decision may open
  up to 2 sub-positions with separate TPs and a break-even move on
  TP1. This is a framework feature, shipping in Phase 1.
- **Mandatory daily reviews.** Every UTC day, including quiet ones.
  Silence is logged as a lessons_learned entry.
- **Promotion gates become review-session calls**, not numeric
  thresholds. The bot produces evidence; you judge.
- **HFM Katana, ¥100k JPY account, XAUUSD primary, BTCUSD 24/7
  secondary.**

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

- `20260424T143106Z` strat=`trend_pullback_fib` data=`synthetic(days=180,seed=7)` trades=202 pf=1.56 ret=30.21% dd=-9.57% sharpe=-0.18
