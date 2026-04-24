# Plan — AI Trading Bot (MT5 / HFM Katana)

This file is the spec. Edit it whenever scope changes. Every code and
strategy decision must trace back to something here.

Status: **v3 (final draft)** — agreed 2026-04-24.

## Objective

Fully automated trading bot on MetaTrader 5, running on an HFM Katana
demo account. Primary instrument **XAUUSD**, secondary **BTCUSD** (24/7).
Starting balance **¥100,000 JPY**.

**Don't blow up the account.** Inside that constraint, optimize for
return. Targets are high-risk / high-reward:

- **Daily profit target:** +30 % of equity → flatten + stop for the day.
- **Daily max loss:** −10 % of equity → flatten + stop for the day.
- **Monthly aspiration:** 200 %. Aspiration, not a promotion gate; if
  multiple iterations show it's unreachable we discuss in a review
  session and adjust.

Real-money promotion is **out of scope** until separately approved.

## Two categories, made explicit

This plan deliberately separates what we *fix up front* (user constraints)
from what the iteration loop *discovers* (policy numbers). Earlier drafts
tried to pre-pick policy; v3 lets the loop do it.

### A. User constraints (locked, enforced in code)

1. **Leverage cap:** effective leverage on account notional ≤ **1:100**.
2. **Position cap, per instrument:** lots ≤ `0.1 × balance_JPY / 100_000`,
   further clamped by (1).
3. **Daily envelope (account-wide, combined realized P&L):**
   **+30 % / −10 %** → flatten everything, stop for the day.
4. **Pullback-only entries.** No martingale; no averaging down; size
   is derived only from current equity + SL distance.
5. **Flexible position management (one decision, up to two sub-legs).**
   A single entry decision per instrument at a time, but that decision
   may open **up to 2 sub-positions** at the same entry price, each
   with its own TP (TP1 < TP2 for longs) and a shared initial SL.
   On TP1 fill, the SL on the remaining sub-position moves to
   break-even (entry price). Sub-positions may also trail further by
   a strategy-defined rule.
6. **Weekend handling.** XAUUSD: flatten before Friday close. BTCUSD:
   runs 24/7 (no weekend flatten).
7. **News blackout.** For **both** XAUUSD and BTCUSD: no new entries
   in the ±30 min window around high-impact events. Phase 1 uses a
   hand-maintained CSV; replaced with a live economic-calendar API
   in a later phase.
8. **Crash-safe.** On restart, reconcile with the broker (adopt
   existing positions, do not re-submit). Persist the daily kill-
   switch state + day ledger + review-pause state to disk.
9. **Withdrawal tracking.** Half of realized daily profit is logged
   as a "you can withdraw this" hint; actual transfer to myWallet is
   manual. The hint is tracked as a sub-ledger so sizing equity is
   correct even though the funds are still in the trading account.
10. **Review-session triggers** (bot posts a review packet + pauses;
    you boot me up, I read it, we decide, I resume the bot):
    - End of UTC day — **mandatory every day, including quiet days.**
      Quiet days still produce a "nothing unusual, hypothesis held"
      lessons_learned entry. Silence is data.
    - **Two consecutive SL-hit trades** on any instrument.
    - Any non-negotiable-rule violation.
    - Weekly wrap.

### B. Discoveries (outputs of iteration, not inputs)

These are *not* pre-specified. The loop finds them.

- Per-trade risk % (can vary per strategy).
- Strategy choice and parameters; SL/TP methodology; TP1/TP2 layout;
  break-even/trail rules; cooldowns; session/volatility filters.
- Realistic max-DD tolerance for promotion (reported; judged in
  review, not a hard numeric gate).
- Profit factor, expectancy, win rate, daily-target hit rate —
  reported, judged in review.
- Whether the +30 / −10 envelope is reachable in practice, and if
  not, what's reachable.

## Promotion gates

Gates are **reporting minimums + human review**, not hard numbers.
The bot is the evidence-producer; you are the judge.

- **Backtest → Demo.** The candidate has run on ≥ 6 months of real
  HFM data with a held-out tournament window, and has produced a
  standardized review packet (PF, DD, expectancy, trade count,
  equity curve, regime breakdown, full trade log). You approve.
- **Demo → "ready".** 1 week on HFM demo. Mandatory daily review
  packet. No non-negotiable-rule violations. You approve in the
  final review session. We document explicitly that 1 week is a
  sanity check, not statistical proof — the backtest stays the
  primary evidence.
- **Real money.** Separate approval, not granted by this plan.

**Automatic reject** (non-discretionary): any constraint in §A is
violated in backtest or demo.

## Roadmap

### Phase 0 — demo environment ✅

Delivered. Will be reworked on the current branch to match v3 (multi-
leg Signal, modify_sl, JPY accounting, review packets).

### Phase 1 — iteration framework

Framework comes before strategy tuning. Deliverables:

- HFM OHLCV loader (from `scripts/fetch_mt5_history.py` CSV)
- Walk-forward splitter: research / validation / tournament
- Per-strategy declarative config (risk %, TP1/TP2, break-even
  rules, cooldowns, params)
- Parameter-sweep harness with **hard try-cap per iteration** to
  prevent p-hacking; every tried combination logged with a hash
- Pessimistic cost model: spread ×1.5 + 2-tick slippage default,
  tunable
- JPY-native accounting: FX-aware `tick_value` so sizing & reporting
  live in JPY even though XAUUSD P&L comes in USD
- **Multi-leg Signal** + `Broker.modify_sl` + engine-side break-even
  orchestration (rule A.5)
- Review-packet generator: `artifacts/reviews/<ts>/review.md` +
  `review.json`
- State persistence: open positions, day ledger, kill-switch,
  review-pause flag
- News blackout CSV plumbing (both instruments)
- Pause-on-trigger + resume-from-review mechanics

### Phase 2 — strategy discovery loop

- Seed strategy = your strategy A (trend pullback + fib).
- I propose candidates, run them through the Phase 1 framework,
  report to you. Your A/B/C ideas are **starting points**, not the
  endpoint; I may propose volatility-breakout, session-opener,
  regime-routed ensembles, etc. as the data justifies.
- Hard cap on parameter tries per iteration.
- Every iteration (including negative ones) writes a `progress.md`
  entry and a `lessons_learned.md` entry.
- Promotion to demo is your call in a review session.

### Phase 3 — 1-week HFM demo

- `run_demo.py` runs the promoted strategy on the HFM demo account.
- Mandatory EOD review packet every UTC day.
- Trigger packets on 2-SL, rule violations, weekly wrap.
- Pass = your approval in the final review session.

### Phase 4 — BTCUSD

- Separate instrument spec (contract size, tick size, tick value).
- Separate Phase 1 / Phase 2 iterations (strategies do **not**
  transfer blindly).
- 24/7 loop; same news blackout calendar.

## Research methodology (how the loop stays honest)

- **Walk-forward + tournament.** Tournament window only revealed at
  promotion time. It is never tuned against.
- **Pessimistic costs** in backtests by default (spread ×1.5, 2-tick
  slippage, spread-only commission per HFM Katana).
- **Capped parameter tries** per iteration.
- Every iteration writes `lessons_learned.md`, including failures.

## Human-in-the-loop split

- **You approve:** promotion between phases, adding a strategy to
  the registry, any edit to §A.1–10, any tournament-window evaluation,
  any move toward real money.
- **I do autonomously:** writing/tuning strategies within caps,
  backtests, review packets, plan tweaks that *tighten* safety rules,
  framework hardening.
- **Reporting:** standardized review packet at every trigger in
  §A.10.

## The framework is the product

The durable value here is the iteration harness + the safety envelope
+ the decision log. Specific strategies are disposable seeds; the
loop can replace them.

## Open items to revisit in a review session

- If the +30 / −10 envelope proves consistently unreachable on
  demo: revise.
- If hand-maintained news CSV becomes a bottleneck: switch to a
  live economic-calendar API.
- If daily reviews become noise: consolidate to weekly + trigger-
  only (not before demo phase).
