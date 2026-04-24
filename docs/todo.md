# TODO

Living task list. Anything crossed out moves to `progress.md`.

Current plan: **v3** (see `docs/plan.md`).

## Phase 0 — demo environment ✅ (on PR #1, to be reworked for v3)

- [x] Spec written (`docs/plan.md`)
- [x] Repo scaffold
- [x] Indicators (swings, trend, fib, ATR)
- [x] BaseStrategy + `TrendPullbackFib` (seed only)
- [x] RiskManager with leverage + daily envelope + half-profit sweep
- [x] Paper broker + MT5 live adapter stub
- [x] Event-driven backtest engine + metrics
- [x] Synthetic OHLCV generator
- [x] CLIs for backtest / demo / fetch
- [x] pytest suite

## Phase 1 — iteration framework ✅

Framework deliverables for plan v3. Strategy tuning happens in
Phase 2; nothing in this phase tunes parameters.

- [x] Multi-leg `Signal` with up to 2 sub-legs sharing an initial SL
      and having distinct TPs (plan §A.5).
- [x] `Broker.modify_sl` on the interface + PaperBroker +
      MT5LiveBroker adapter.
- [x] Engine-side break-even orchestration: on TP1 fill, move the
      runner's SL to `move_sl_to_on_fill` (typically entry price).
- [x] JPY-native accounting: `InstrumentSpec.quote_currency`,
      `RiskManager.account_currency` + `FXConverter`, plan v3 §A.2
      balance-scaled lot cap (`lot_cap_per_unit_balance`).
- [x] Walk-forward splitter with research / validation / tournament
      windows. Held-out loader gated by an explicit opt-in flag.
- [x] Bounded grid sweep harness with `max_trials` cap, per-trial
      JSONL log, hashed param+window fingerprint.
- [x] Review-packet generator (`review.md` + `review.json`).
- [x] Review-trigger engine: EOD (mandatory), weekly wrap,
      2-consecutive-SL, kill-switch. Once-per-day rate limiting.
- [x] State persistence: day ledger, kill-switch, `consecutive_sl`,
      `withdrawn_total` survive restarts via atomic JSON store.
- [x] News blackout CSV loader + engine filter (both XAUUSD and
      BTCUSD).
- [x] BTCUSD instrument config (24/7 flag).
- [ ] **Needs Windows host:** run `scripts/fetch_mt5_history.py` on
      HFM demo and commit the CSV (or attach as release asset).
      Blocked on you or a remote Windows runner.

## Phase 2 — strategy discovery loop

- [x] Run seed `trend_pullback_fib` on 1 yr real XAUUSD (Dukascopy).
      Result: research PF 1.50 → validation PF 0.33. Overfit caught
      by the walk-forward ratchet. See `progress.md` 2026-04-24.
- [x] First bounded parameter sweep on seed strategy (18 trials,
      sweep id `seed-xau-2024-v1`).
- [x] Pull 22 months of real XAUUSD (2024-06 → 2026-04).
- [x] Recent-regime sweep (`seed-xau-recent-v1`): seed strategy
      takes ~0 trades in the post-March-2026 window. Confirmed
      with regime profile. Not a candidate as-is.
- [x] Sweep seed strategy with larger `risk_per_trade_pct`:
      lot-cap is the silence cause, but DD catastrophic. Not
      promotable.
- [x] Donchian-retest volatility-breakout family. Net-negative on
      research in both regimes. Not promotable.
- [x] **BB-scalper on 2026 M1.** Tournament PF 1.10 (6d) then
      confirmed PF 1.14 (12d). Candidate.
- [x] **Trend-pullback scalper (user's original strategy 1).**
      Research/validation cleared; tournament failed in a choppy
      regime. Regime-dependent; hold for router.
- [ ] **Regime router.** ADX + realized-vol classifier;
      route trending bars to `trend_pullback_scalper`, choppy
      bars to `bb_scalper`. Same research/validation/tournament
      discipline on the ensemble.
- [ ] Session filter (London + NY overlap).
- [ ] Populated 2026 news-blackout CSV.
- [ ] Fresh-week tournament pass after time passes.
- [ ] Equity-curve + trade-log visualisation for the review
      session.
- [ ] Review session → promote / reject / iterate.

## Phase 3 — 1-week HFM demo

- [ ] Windows host / VM w/ MT5 + HFM Katana demo account.
- [ ] `run_demo.py` wired with trigger engine.
- [ ] 7 daily review packets collected, weekly wrap at the end.
- [ ] Final review session → accept / reject.

## Phase 4 — BTCUSD

- [ ] BTCUSD Phase 1 hardening (instrument-specific costs, 24/7
      trigger cadence).
- [ ] BTCUSD Phase 2 discovery loop.
- [ ] BTCUSD Phase 3 demo.

## Parking lot (revisit in review sessions)

- [ ] Replace news CSV with a live economic-calendar API.
- [ ] Reconsider the +30 / −10 envelope if iteration shows it's
      unreachable.
- [ ] Decide if daily mandatory reviews should become weekly
      post-Phase 3.
