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

## Phase 1 — iteration framework (next)

Rework PR #1 to match plan v3. These are framework features, not
strategy tuning.

- [ ] **Multi-leg Signal.** Replace `Signal.take_profit: float` with
      `Signal.legs: list[Leg]` where each leg has lots + TP + an
      optional break-even trigger price. One entry decision may open
      1 or 2 sub-positions (plan §A.5).
- [ ] **Broker.modify_sl** on `Broker` / `PaperBroker` / `MT5LiveBroker`
      so break-even can move stops on live legs.
- [ ] **Engine-side break-even orchestration.** On TP1 fill, move SL
      on the remaining leg to entry.
- [ ] **JPY-native accounting.** `tick_value_jpy` computed from live
      USD/JPY (or config override) so sizing + reporting are in JPY.
- [ ] **Walk-forward splitter** (research / validation / tournament).
      Tournament window held out at the file-loader level so no
      backtest accidentally touches it.
- [ ] **Parameter-sweep harness** with hard try-cap per iteration.
      Every tried combo gets a hash + logged run.
- [ ] **Review-packet generator** writing
      `artifacts/reviews/<ts>/review.md` + `review.json`.
- [ ] **Review-trigger engine.** Plumb: end-of-UTC-day (mandatory),
      2-consecutive-SL, rule violation, weekly wrap. Bot pauses on
      trigger; resumes on human signal.
- [ ] **State persistence.** Day ledger, kill-switch, review-pause
      flag, open positions — survive process restarts and are
      reconciled on boot.
- [ ] **News blackout CSV.** Both instruments. Skip new entries in
      ±30 min windows.
- [ ] **BTCUSD instrument spec + config** (distinct from XAUUSD).
- [ ] **HFM data fetch script** verified on a Windows host
      (need you or a remote Windows runner to do this one).

## Phase 2 — strategy discovery loop

- [ ] Run seed strategy `trend_pullback_fib` through the framework on
      12 mo of HFM XAUUSD. Publish review packet.
- [ ] Parameter sweep on the seed strategy within the try-cap.
- [ ] Propose & backtest 2–3 new candidates (volatility breakout,
      session opener, regime router).
- [ ] Iterate until one candidate clears the promotion review.

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
