# TODO

Living task list. Anything crossed out moves to `progress.md`.
Current spec: **plan v3** (see `docs/plan.md`).
**For the full picture of where we are, see `docs/HANDOFF.md`.**

## Phase 0 — demo environment ✅

- [x] Spec written (`docs/plan.md`)
- [x] Repo scaffold
- [x] Indicators (swings, trend, fib, ATR; ZigZag added later)
- [x] BaseStrategy + first seed strategy
- [x] RiskManager with leverage + daily envelope + half-profit sweep
- [x] Paper broker + MT5 live adapter stub
- [x] Event-driven backtest engine + metrics
- [x] Synthetic OHLCV generator
- [x] CLIs for backtest / demo / fetch
- [x] pytest suite

## Phase 1 — iteration framework ✅

- [x] Multi-leg `Signal` with up to 2 sub-legs sharing an initial SL
      and having distinct TPs (plan §A.5).
- [x] `Broker.modify_sl` on the interface + PaperBroker +
      MT5LiveBroker adapter.
- [x] Engine-side break-even orchestration: on TP1 fill, move the
      runner's SL to `move_sl_to_on_fill`.
- [x] JPY-native accounting: `InstrumentSpec.quote_currency`,
      `RiskManager.account_currency` + `FXConverter`, plan v3 §A.2
      balance-scaled lot cap.
- [x] Walk-forward splitter with research / validation / tournament
      windows. Held-out loader gated by an explicit opt-in flag.
- [x] Bounded grid sweep harness with `max_trials` cap, per-trial
      JSONL log, hashed param+window fingerprint.
- [x] Review-packet generator + trigger engine (EOD, weekly,
      consecutive-SL, kill-switch). Once-per-day rate limiting.
- [x] State persistence: day ledger, kill-switch, `consecutive_sl`,
      `withdrawn_total` survive restarts via atomic JSON store.
- [x] News blackout CSV loader + engine filter.
- [x] Cross-platform Dukascopy data loader.
- [x] Dukascopy data loader (replaces the Windows-only
      `fetch_mt5_history.py` for research). MT5 fetch script kept
      for live HFM data once a Windows host is available.

## Phase 2 — strategy discovery ✅ (loop continues)

Status: 9 strategy families tried; `news_fade` is the only
walk-forward winner. See `docs/HANDOFF.md` for the full scoreboard.

### Already done

- [x] Falsified: `trend_pullback_fib`, `donchian_retest`,
      `bb_scalper`, `liquidity_sweep`, `volume_reversion`,
      `london_orb`, `trend_pullback_scalper`.
- [x] Sample-too-small: `mtf_zigzag_bos`, `vwap_reversion`.
- [x] Tournament-clearing-but-low-edge: `bos_retest_scalper`.
- [x] **First walk-forward winner: `news_fade`** (PF 3.87 on
      tournament, +0.60 %/month over 4 months, DD −2 %).
- [x] Risk-stack sweeps confirm risk-% > 2 % is counterproductive
      on price-action strategies (returns peak at 2 %).
- [x] Kill-switch fix (intra-bar flatten); equity-curve fix
      (include withdrawn_total); BB break-even fix (yaml).
- [x] Daily + monthly metrics; cap-violation check.
- [x] Three split modes: `recent`, `recent_only`, `interleaved`.
- [x] News blackout CSV populated for 2026 high-impact USD events.
- [x] Session filter implemented in BB / BOS / Liquidity-sweep /
      VWAP / news_fade strategies.

### Active / next

- [x] **GOLD-only high-risk expansion v1**: user redirected the
      project away from multi-instrument expansion and toward
      aggressive XAUUSD-only search with "avoid zero cut" as the
      primary guardrail. Added high-risk configs, ruin diagnostics,
      batch runner, `news_breakout`, and `session_sweep_reclaim`.
      First result: `session_sweep_reclaim` cleared 7d/14d
      tournament (+9.25%/+7.90%) while prior validation winners
      failed tournament.
- [ ] **Risk/BE frontier for `session_sweep_reclaim`**: sweep
      risk 1-5%, TP1/TP2, range/session windows, and SL caps to see
      whether April-positive edge can scale toward 50-100%/month
      without near-zero-cut drawdowns.
- [ ] **Regime router**: arm `vwap_reversion` only in chop, BB-
      family only in low-vol, leave `news_fade` always on.
      Hypothesis: route by regime to recover the validation edges
      that died on tournament.
- [ ] **Richer event calendar**: current CSV has 14 high-impact
      events. Add ISM, retail sales, PPI, ADP, jobless claims for
      2-3× the trade count.
- [ ] **Live demo of `news_fade`** once Windows host with HFM MT5
      access is available.
- [ ] Equity-curve + trade-log visualisation for review.

## Phase 3 — live HFM demo (blocked on Windows host)

- [ ] Windows host / VPS w/ MT5 + HFM Katana demo account.
- [ ] `run_demo.py` wired with trigger engine.
- [ ] 2-week demo run of `news_fade` (~6 events). Plan v3's
      "1 week" sanity check is too short for an event-driven
      strategy.
- [ ] Daily review packets collected.
- [ ] Final review session → accept / reject / iterate.

## Phase 4 — multi-instrument expansion

- [ ] Deferred by user direction: focus exclusively on XAUUSD/GOLD.
      Do not spend research budget on EURUSD/GBPUSD unless the user
      reverses this instruction.
- [ ] *(BTCUSD remains deprioritised — HFM real spread ~$10
      makes M1 scalping uneconomic.)*

## Parking lot

- [ ] Replace hand-curated news CSV with a live economic-calendar
      API (currently a quarterly maintenance burden).
- [ ] Reconsider the +30 / −10 envelope if iteration shows it's
      truly unreachable (current best is +0.6 %/month, well below
      the +30 % daily target).
- [ ] Decide if mandatory daily reviews should become weekly
      post-Phase 3 (scaling problem only matters once we're live).
