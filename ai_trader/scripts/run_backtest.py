"""Run a backtest and append a summary to docs/progress.md.

Examples:

    python -m ai_trader.scripts.run_backtest \\
        --config config/default.yaml --synthetic --days 180 --seed 7

    python -m ai_trader.scripts.run_backtest \\
        --config config/default.yaml --csv data/xauusd_m5.csv
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..backtest.engine import BacktestEngine
from ..backtest.metrics import compute_metrics
from ..broker.paper import PaperBroker
from ..config import load_config
from ..data.csv_loader import load_ohlcv_csv
from ..data.synthetic import generate_synthetic_ohlcv
from ..news.calendar import NewsCalendar, NoNewsCalendar, load_news_csv
from ..risk.fx import FixedFX
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy
from ..utils.logging import get_logger


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", type=Path, help="OHLCV CSV produced by fetch_mt5_history.py")
    src.add_argument("--synthetic", action="store_true")
    ap.add_argument("--days", type=int, default=180, help="synthetic: days of history")
    ap.add_argument("--seed", type=int, default=7, help="synthetic: RNG seed")
    ap.add_argument("--no-report", action="store_true", help="do not append to docs/progress.md")
    ap.add_argument("--trades-out", type=Path, default=None,
                    help="optional CSV path for closed trades")
    ap.add_argument("--equity-out", type=Path, default=None,
                    help="optional CSV path for the total-account equity curve")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    log = get_logger("ai_trader.backtest")
    cfg = load_config(args.config)

    inst_cfg = cfg["instrument"]
    instrument = InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )

    account_ccy = cfg["account"].get("currency", "USD")
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None

    risk_cfg = cfg["risk"]
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        risk_per_trade_pct=float(risk_cfg["risk_per_trade_pct"]),
        daily_profit_target_pct=float(risk_cfg["daily_profit_target_pct"]),
        daily_max_loss_pct=float(risk_cfg["daily_max_loss_pct"]),
        withdraw_half_of_daily_profit=bool(risk_cfg.get("withdraw_half_of_daily_profit", True)),
        max_concurrent_positions=int(risk_cfg.get("max_concurrent_positions", 1)),
        lot_cap_per_unit_balance=float(risk_cfg.get("lot_cap_per_unit_balance", 0.0)),
        account_currency=account_ccy,
        fx=fx,
        dynamic_risk_enabled=bool(risk_cfg.get("dynamic_risk_enabled", False)),
        min_risk_per_trade_pct=(
            float(risk_cfg["min_risk_per_trade_pct"])
            if risk_cfg.get("min_risk_per_trade_pct") is not None
            else None
        ),
        max_risk_per_trade_pct=(
            float(risk_cfg["max_risk_per_trade_pct"])
            if risk_cfg.get("max_risk_per_trade_pct") is not None
            else None
        ),
        confidence_risk_floor=float(risk_cfg.get("confidence_risk_floor", 0.75)),
        confidence_risk_ceiling=float(risk_cfg.get("confidence_risk_ceiling", 1.5)),
        drawdown_soft_limit_pct=float(risk_cfg.get("drawdown_soft_limit_pct", 12.0)),
        drawdown_hard_limit_pct=float(risk_cfg.get("drawdown_hard_limit_pct", 25.0)),
        drawdown_soft_multiplier=float(risk_cfg.get("drawdown_soft_multiplier", 0.7)),
        drawdown_hard_multiplier=float(risk_cfg.get("drawdown_hard_multiplier", 0.4)),
    )

    exec_cfg = cfg["execution"]
    broker = PaperBroker(
        instrument=instrument,
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )

    strat_cfg = cfg["strategy"]
    strategy = get_strategy(strat_cfg["name"], **strat_cfg.get("params", {}))

    if args.synthetic:
        log.info("loading synthetic data: days=%s seed=%s tf=%s", args.days, args.seed, inst_cfg["timeframe"])
        df = generate_synthetic_ohlcv(
            days=args.days,
            timeframe=inst_cfg["timeframe"],
            seed=args.seed,
        )
        data_tag = f"synthetic(days={args.days},seed={args.seed})"
    else:
        log.info("loading CSV: %s", args.csv)
        df = load_ohlcv_csv(args.csv)
        data_tag = f"csv({args.csv})"

    news_cfg = cfg.get("news", {}) or {}
    news_csv = news_cfg.get("csv")
    news: NewsCalendar
    if news_csv:
        news = NewsCalendar(
            events=load_news_csv(news_csv),
            window_minutes=int(news_cfg.get("window_minutes", 30)),
            impact_filter=tuple(news_cfg.get("impact_filter", ["high"])),
        )
        log.info("loaded %s news events from %s", len(news.events), news_csv)
    else:
        news = NoNewsCalendar()

    log.info("running backtest on %s bars", len(df))
    engine = BacktestEngine(strategy=strategy, risk=risk, broker=broker, news=news, log=log.info)
    result = engine.run(df)
    metrics = compute_metrics(result, starting_balance=float(cfg["account"]["starting_balance"]))

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path("artifacts/runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_ts}.json"
    out_path.write_text(
        json.dumps(
            {
                "timestamp": run_ts,
                "config": str(args.config),
                "data": data_tag,
                "strategy": strat_cfg["name"],
                "strategy_params": strat_cfg.get("params", {}),
                "metrics": metrics,
            },
            indent=2,
            default=_json_default,
        )
    )
    log.info("wrote %s", out_path)
    if args.trades_out:
        args.trades_out.parent.mkdir(parents=True, exist_ok=True)
        _write_trades_csv(result.trades, args.trades_out)
        log.info("wrote %s", args.trades_out)
    if args.equity_out:
        args.equity_out.parent.mkdir(parents=True, exist_ok=True)
        result.equity_curve.rename("equity").to_frame().to_csv(args.equity_out, index_label="time")
        log.info("wrote %s", args.equity_out)
    print(json.dumps(metrics, indent=2, default=_json_default))

    if not args.no_report:
        _append_progress(run_ts, data_tag, strat_cfg["name"], metrics)

    return 0


def _append_progress(run_ts: str, data_tag: str, strat: str, metrics: dict[str, Any]) -> None:
    path = Path("docs/progress.md")
    if not path.exists():
        return
    line = (
        f"\n- `{run_ts}` strat=`{strat}` data=`{data_tag}` "
        f"trades={metrics['trades']} pf={metrics['profit_factor']:.2f} "
        f"ret={metrics['return_pct']:.2f}% dd={metrics['max_drawdown_pct']:.2f}% "
        f"sharpe={metrics['sharpe_daily']:.2f}\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def _json_default(o: Any) -> Any:
    if isinstance(o, float) and (o == float("inf") or o != o):
        return str(o)
    return str(o)


def _write_trades_csv(trades: list, path: Path) -> None:
    import csv

    fields = [
        "open_time", "close_time", "side", "lots", "entry", "exit",
        "pnl", "reason", "group_id", "leg_index",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow({k: getattr(t, k) for k in fields})


if __name__ == "__main__":
    raise SystemExit(main())
