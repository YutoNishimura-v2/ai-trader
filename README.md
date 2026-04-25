# ai-trader

Automated XAUUSD (gold) scalping bot for MetaTrader 5 (HFM Katana).

**👉 If you're new here, read [`docs/HANDOFF.md`](docs/HANDOFF.md) first.**
It has everything you need to pick up where the project is now: strategy
scoreboard, current best result (`news_fade`), known gotchas, and the
next planned moves. The rest of `docs/` is supporting material.

## Status (one line)

9 strategy families built and walk-forward evaluated on real 2026 M1
data; `news_fade` is the only one to clear research + validation +
tournament with positive PF (full 4-month: +0.60 %/month, DD −2 %,
daily Sharpe +1.65). Live demo on HFM blocked on Windows host access.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

To get real data and reproduce the current best result:

```bash
# Pull 2026 M1 XAUUSD from Dukascopy (cross-platform; cached)
python -m ai_trader.scripts.fetch_dukascopy \
    --symbol XAUUSD --timeframe M1 \
    --start 2026-01-01 --end 2026-04-24 \
    --out data/xauusd_m1_2026.csv

# Run the current best strategy on the full window
python -m ai_trader.scripts.run_backtest \
    --config config/news_fade.yaml \
    --csv data/xauusd_m1_2026.csv --no-report
```

For everything else (sweeps, tournament eval, live demo, architecture)
see [`docs/HANDOFF.md`](docs/HANDOFF.md).

## Specification

- `docs/plan.md` — locked spec (constraints, gates, instruments).
- `docs/HANDOFF.md` — current state + how to continue.
- `docs/progress.md` — append-only iteration log with raw numbers.
- `docs/lessons_learned.md` — append-only insights.
- `docs/log.md` — chronological session diary.
- `docs/todo.md` — living task list.
