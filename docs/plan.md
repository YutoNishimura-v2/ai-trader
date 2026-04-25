# Plan — AI Trading Bot (MT5 / HFM Katana)

This file is the spec. Edit it whenever scope changes. Every code and
strategy decision must trace back to something here.

Status: **v3 (final draft)** — agreed 2026-04-24.

## Objective

Fully automated trading bot on MetaTrader 5, running on an HFM Katana
demo account. Primary instrument **XAUUSD**. Starting balance
**¥100,000 JPY**.

> **Update (2026-04-25):** BTCUSD was originally listed as a 24/7
> secondary instrument. After Iter 8 the user explicitly
> deprioritised it: HFM's real BTC spread is around $10 (≈ 100
> points), which makes M1 scalping uneconomic — every BB sweep on
> BTC produced PF < 1. The 24/7 plumbing remains in the codebase
> and is used to test that path. **Multi-instrument expansion is
> now planned along EURUSD/GBPUSD instead** (same USD events for
> `news_fade`). See `docs/HANDOFF.md` and `docs/todo.md` Phase 4.

**Don't blow up the account.** Inside that constraint, optimize for
return. Targets are high-risk / high-reward:

- **Daily profit target:** +30 % of equity → flatten + stop for the day.
- **Daily max loss:** −10 % of equity → flatten + stop for the day.
- **Monthly aspiration:** 200 %. Aspiration, not a promotion gate; if
  multiple iterations show it's unreachable we discuss in a review
  session and adjust.

> **Update (2026-04-25, GOLD-only HRHR revision):** The user has
> explicitly narrowed research back to **XAUUSD only** and authorized
> materially more risk if needed. The primary non-negotiable guardrail
> is now **avoid margin-call / zero-cut ruin**. The historic lot cap,
> daily envelope, and 1:100 leverage remain the conservative HFM
> baseline, but may be loosened in clearly labelled simulation-only
> research runs to discover whether the return ceiling is caused by
> signal quality or by sizing constraints. No martingale, no blind
> averaging down, and no lookahead remain prohibited.

Real-money promotion is **out of scope** until separately approved.

## Two categories, made explicit

This plan deliberately separates what we *fix up front* (user constraints)
from what the iteration loop *discovers* (policy numbers). Earlier drafts
tried to pre-pick policy; v3 lets the loop do it.

### A. User constraints / baseline guardrails

As of the 2026-04-25 GOLD-only HRHR revision, items 1-3 are the
**default executable baseline** rather than immutable research limits.
They stay enforced in normal configs. Aggressive research configs may
vary them only when labelled as simulation-only and only while tracking
ruin metrics (`min_equity_pct`, drawdown, cap hits, and margin-call risk).

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
7. **News blackout.** Default behaviour for *most* strategies: no
   new entries in the ±30 min window around high-impact events. The
   `news_fade` strategy is an explicit exception that *uses* the
   same CSV as a trigger rather than a suppressor, so this rule is
   "blackout unless the strategy is explicitly event-driven." Phase
   1 uses a hand-maintained CSV; the plan still calls for a live
   economic-calendar API in a later phase.
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

### Phase 0 — demo environment ✅ delivered

### Phase 1 — iteration framework ✅ delivered

All deliverables in place: walk-forward splitter (3 modes), bounded
sweep harness with try-cap, pessimistic cost model, JPY-native
accounting + FX, multi-leg Signal + break-even, review packets +
trigger engine, crash-safe state, news CSV. Cross-platform
Dukascopy data loader replaces the Windows-only MT5 fetch script
for research; MT5 fetch is kept for live HFM data fetches once a
Windows host is available.

### Phase 2 — strategy discovery loop (in progress)

- 9 strategy families tried; `news_fade` is the only walk-forward
  winner so far. Detailed scoreboard in `docs/HANDOFF.md`.
- Hard cap on parameter tries per iteration enforced by the
  sweep harness (`max_trials`).
- Every iteration writes `progress.md` + `lessons_learned.md`,
  including the negative ones (most of them).
- Promotion to demo is the user's call.

### Phase 3 — HFM demo (blocked on Windows host)

- Originally specified as 1 week. Revised in HANDOFF.md to
  2 weeks for `news_fade` because event-driven means ~3 events
  per week — 1 week is too small a sample.
- `run_demo.py` runs the promoted strategy on the HFM demo
  account.
- Mandatory EOD review packet every UTC day.
- Trigger packets on 2-SL, rule violations, weekly wrap.
- Pass = approval in the final review session.

### Phase 4 — GOLD-only high-risk research expansion (revised 2026-04-25)

The user rejected multi-instrument expansion for now: each symbol has
different character, so all search effort should remain on XAUUSD.

- Expand GOLD-only strategy search substantially: event fade,
  event continuation, regime-routed VWAP/BB/BOS, session sweep/reclaim,
  momentum pullback, and squeeze breakout.
- Include high-risk position-management sweeps in the user's practical
  range (2-4% per trade, plus stress variants), with TP1/TP2 and
  break-even moves emphasized.
- Track ruin explicitly: minimum equity %, max drawdown, cap hits,
  and whether a configuration approaches margin-call-style failure.
- Keep tournament discipline: broad research/validation exploration is
  allowed, but held-out tournament windows are still not tuned against.

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
