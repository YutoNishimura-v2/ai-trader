# ai-trader

Automated XAUUSD (GOLD) trading bot for MetaTrader 5.

Goal: **steady, consistent profit**, not explosive gains. See
[`docs/plan.md`](docs/plan.md) for the full specification and
[`docs/todo.md`](docs/todo.md) for the current roadmap.

## Status

Phase 0 (demo environment) is complete. The repo can:

- backtest strategies against MT5 CSV data or synthetic OHLCV;
- simulate execution with spread, slippage, and commission;
- enforce the user's trading rules (leverage cap, daily target/loss,
  half-profit withdrawal, pullback-only entries);
- run against a real MT5 demo account on Windows (stubbed adapter).

Phase 1 (real-data backtest + demo run) is the next milestone.

## Install

```bash
python -m venv .venv
source .venv/bin/activate         # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`MetaTrader5` is only required on Windows and is listed in
`requirements-live.txt`.

## Run a synthetic backtest

```bash
python -m ai_trader.scripts.run_backtest \
    --config config/default.yaml \
    --synthetic --days 180 --seed 7
```

This produces a metrics JSON under `artifacts/runs/` and appends a
summary line to `docs/progress.md`.

## Run against a real MT5 CSV

```bash
python -m ai_trader.scripts.fetch_mt5_history \
    --symbol XAUUSD --timeframe M5 --months 12 --out data/xauusd_m5.csv
python -m ai_trader.scripts.run_backtest \
    --config config/default.yaml --csv data/xauusd_m5.csv
```

The `fetch_mt5_history` script must be run on a Windows host with
MetaTrader 5 and the `MetaTrader5` Python package installed.

## Run a live demo

```bash
python -m ai_trader.scripts.run_demo --config config/demo.yaml
```

## Tests

```bash
pytest -q
```

## Layout

See [`docs/plan.md §5`](docs/plan.md) for the architecture.
