"""Stability harness for validation→test consistency (Iter30).

This module replaces the project's prior single-window scoring habit
with a *rolling battery* of N non-overlapping (research, validation,
test) triples. A configuration is judged by:

1. Its consistency: a per-window ``generalization_score`` that is
   only positive if validation AND test both have PF >= 1, no cap
   violations, no ruin, and same-sign returns.
2. Its 3x-month potential: ``best_month_pct`` over the full dataset
   (reported only — full-period is NOT a tuning objective).

Every opening of a test window is audit-logged to a JSONL file with
the config hash, the literal opt-in token, the window date span and
the observed numbers. This produces an external paper trail showing
that each test window was opened exactly once per candidate, which
is the discipline the project keeps drifting away from when it
chases peak headlines.

Public API
----------

- :class:`Window`              — one (research, validation, test) triple.
- :func:`build_rolling_windows` — partition a dataset into N triples.
- :class:`WindowResult`         — per-window metrics and verdict.
- :func:`evaluate_config`       — run a backtest on every window.
- :func:`score_config`          — aggregate per-window results.
- :func:`generalization_score`  — single per-window score.
- :func:`promotion_status`      — promotable / candidate / falsified.
- :func:`compute_best_month`    — best calendar-month return on the full set.

Backwards compatibility: this module does NOT modify any existing
runtime path. It only consumes :class:`BacktestEngine`,
:class:`RiskManager`, :class:`PaperBroker`, and :func:`compute_metrics`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..backtest.engine import BacktestEngine, BacktestResult
from ..backtest.metrics import compute_metrics
from ..backtest.sweep import risk_kwargs_from_config
from ..broker.paper import PaperBroker
from ..risk.fx import FixedFX
from ..risk.manager import InstrumentSpec, RiskManager
from ..strategy.registry import get_strategy


_AUDIT_OPT_IN_TOKEN = "i_know_this_is_tournament_evaluation=True"

# Sentinel used by generalization_score when a window is disqualified.
DISQUALIFIED_SCORE = float("-inf")


@dataclass(frozen=True)
class Window:
    """A single (research, validation, test) triple sharing a calendar.

    All three slices are tz-aware UTC DataFrames carved out of the
    same source frame. The slices are temporally adjacent in the
    order ``research → validation → test`` (no overlap) so a
    backtest can be warmed up on research+validation and the test
    can then be opened exactly once.
    """

    label: str
    research: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    research_span: tuple[pd.Timestamp, pd.Timestamp]
    validation_span: tuple[pd.Timestamp, pd.Timestamp]
    test_span: tuple[pd.Timestamp, pd.Timestamp]

    @property
    def warmup(self) -> pd.DataFrame:
        """research+validation concatenation, used to score validation
        without leaking test into the strategy state."""
        return pd.concat([self.research, self.validation])


def _ensure_utc(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def build_rolling_windows(
    df: pd.DataFrame,
    *,
    n_windows: int = 4,
    research_days: int = 30,
    validation_days: int = 14,
    test_days: int = 14,
    step_days: int = 14,
    min_research_bars: int = 5_000,
    min_validation_bars: int = 1_000,
    min_test_bars: int = 1_000,
) -> list[Window]:
    """Carve a dataset into N non-overlapping (R, V, T) windows.

    Layout: the freshest window's test slice ends at the dataset's
    last bar; preceding windows are stepped backward by
    ``step_days``. ``step_days == test_days`` produces non-overlapping
    test slices (the default).

    Each window's research span is ``research_days`` long, its
    validation span ``validation_days`` long, its test span
    ``test_days`` long, and they are temporally contiguous.

    Windows that don't satisfy the per-slice minimum bar counts (a
    typical M1-XAUUSD weekend leaves only thin slices around major
    holidays) are dropped silently. A ``ValueError`` is raised if
    the result has fewer than two surviving windows since the
    harness becomes meaningless.
    """
    if df.empty:
        raise ValueError("cannot build windows from an empty DataFrame")
    if not df.index.is_monotonic_increasing:
        raise ValueError("DataFrame index must be sorted ascending")
    if research_days <= 0 or validation_days <= 0 or test_days <= 0:
        raise ValueError("research/validation/test days must all be > 0")
    if step_days <= 0:
        raise ValueError("step_days must be > 0")

    idx = _ensure_utc(df.index)
    df = df.copy()
    df.index = idx

    last = idx[-1]
    out: list[Window] = []
    for w in range(n_windows):
        # The freshest window is appended LAST so the resulting list is
        # ordered W1 .. Wn from oldest to newest, matching the docs.
        offset = (n_windows - 1 - w) * step_days
        test_end = last - pd.Timedelta(days=offset)
        test_start = test_end - pd.Timedelta(days=test_days)
        val_end = test_start
        val_start = val_end - pd.Timedelta(days=validation_days)
        res_end = val_start
        res_start = res_end - pd.Timedelta(days=research_days)

        if res_start < idx[0]:
            continue

        research = df[(df.index >= res_start) & (df.index < res_end)]
        validation = df[(df.index >= val_start) & (df.index < val_end)]
        test = df[(df.index >= test_start) & (df.index < test_end)]

        if (
            len(research) < min_research_bars
            or len(validation) < min_validation_bars
            or len(test) < min_test_bars
        ):
            continue

        out.append(
            Window(
                label=f"W{w + 1}",
                research=research,
                validation=validation,
                test=test,
                research_span=(res_start, res_end),
                validation_span=(val_start, val_end),
                test_span=(test_start, test_end),
            )
        )

    if len(out) < 2:
        raise ValueError(
            f"build_rolling_windows produced only {len(out)} window(s); "
            "harness requires at least 2. Reduce minimum bar counts or "
            "research_days, or fetch more data."
        )

    # Verify that test slices are non-overlapping (audit-trail invariant).
    for i in range(len(out) - 1):
        assert out[i].test_span[1] <= out[i + 1].test_span[0], (
            f"overlapping test slices at {i}: {out[i].test_span} vs "
            f"{out[i + 1].test_span}"
        )
    return out


def generalization_score(val: dict[str, Any], test: dict[str, Any]) -> float:
    """Disqualify-or-score a single (validation, test) pair.

    A window passes only when:
      - both windows have ``cap_violations == 0``
      - neither window has ``ruin_flag == True``
      - both have profit_factor >= 1.0
      - returns share the same sign

    The returned score is ``min(val.return_pct, test.return_pct)``
    when the window passes (so a great validation paired with a
    barely-positive test is bottlenecked by the test, exactly as we
    want), and :data:`DISQUALIFIED_SCORE` otherwise.
    """
    if val.get("cap_violations", 0) > 0 or test.get("cap_violations", 0) > 0:
        return DISQUALIFIED_SCORE
    if val.get("ruin_flag", False) or test.get("ruin_flag", False):
        return DISQUALIFIED_SCORE
    if val.get("profit_factor", 0.0) < 1.0 or test.get("profit_factor", 0.0) < 1.0:
        return DISQUALIFIED_SCORE
    val_ret = float(val.get("return_pct", 0.0))
    test_ret = float(test.get("return_pct", 0.0))
    if (val_ret < 0) != (test_ret < 0):
        return DISQUALIFIED_SCORE
    return min(val_ret, test_ret)


@dataclass
class WindowResult:
    """Per-window outcome for a single config."""

    label: str
    val_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    score: float
    research_span: tuple[pd.Timestamp, pd.Timestamp]
    validation_span: tuple[pd.Timestamp, pd.Timestamp]
    test_span: tuple[pd.Timestamp, pd.Timestamp]

    @property
    def passed(self) -> bool:
        return self.score > DISQUALIFIED_SCORE


@dataclass
class ConfigEvaluation:
    """Aggregate result for a single config."""

    config_path: Path | None
    config_hash: str
    windows: list[WindowResult]
    full_metrics: dict[str, Any]
    best_month_pct: float
    best_month_label: str
    worst_month_pct: float = 0.0
    worst_month_label: str = ""
    mar_return_pct: float | None = None
    apr_return_pct: float | None = None
    full_cap_violations: int = 0
    full_ruin_flag: bool = False

    @property
    def windows_passing(self) -> int:
        return sum(1 for w in self.windows if w.passed)

    @property
    def worst_score(self) -> float:
        passing = [w.score for w in self.windows if w.passed]
        return min(passing) if passing else DISQUALIFIED_SCORE

    @property
    def mean_score(self) -> float:
        passing = [w.score for w in self.windows if w.passed]
        return sum(passing) / len(passing) if passing else DISQUALIFIED_SCORE


def _config_hash(cfg: dict[str, Any]) -> str:
    payload = json.dumps(cfg, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _instrument_from_cfg(cfg: dict[str, Any]) -> InstrumentSpec:
    inst_cfg = cfg["instrument"]
    return InstrumentSpec(
        symbol=inst_cfg["symbol"],
        contract_size=float(inst_cfg["contract_size"]),
        tick_size=float(inst_cfg["tick_size"]),
        tick_value=float(inst_cfg["tick_value"]),
        quote_currency=inst_cfg.get("quote_currency", "USD"),
        min_lot=float(inst_cfg.get("min_lot", 0.01)),
        lot_step=float(inst_cfg.get("lot_step", 0.01)),
        is_24_7=bool(inst_cfg.get("is_24_7", False)),
    )


def _build_runtime(cfg: dict[str, Any]):
    """Build a fresh (strategy, risk, broker, starting_balance) tuple
    for one independent backtest run. Always built fresh per slice so
    no state leaks between research+validation and test."""
    instrument = _instrument_from_cfg(cfg)
    fx = FixedFX.from_config(cfg.get("fx") or {}) if cfg.get("fx") else None
    risk = RiskManager(
        starting_balance=float(cfg["account"]["starting_balance"]),
        max_leverage=float(cfg["account"]["max_leverage"]),
        instrument=instrument,
        account_currency=cfg["account"].get("currency", "USD"),
        fx=fx,
        **risk_kwargs_from_config(cfg["risk"]),
    )
    exec_cfg = cfg["execution"]
    broker = PaperBroker(
        instrument=instrument,
        spread_points=int(exec_cfg["spread_points"]),
        slippage_points=int(exec_cfg["slippage_points"]),
        commission_per_lot=float(exec_cfg.get("commission_per_lot", 0.0)),
    )
    strat_params = cfg["strategy"].get("params") or {}
    strat = get_strategy(cfg["strategy"]["name"], **strat_params)
    return strat, risk, broker, float(cfg["account"]["starting_balance"])


def _run_one(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    """Run a single backtest on a single dataframe and return metrics."""
    strat, risk, broker, sb = _build_runtime(cfg)
    res: BacktestResult = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(df)
    return compute_metrics(res, starting_balance=sb)


def compute_best_month(metrics_full: dict[str, Any]) -> tuple[float, str]:
    """Return (best_month_pct, label) from a full-period metrics dict.

    Uses :func:`compute_metrics`'s ``monthly_returns`` map. If the dict
    is empty, returns ``(0.0, "")``.
    """
    monthly = metrics_full.get("monthly_returns") or {}
    if not monthly:
        return 0.0, ""
    label, value = max(monthly.items(), key=lambda kv: float(kv[1]))
    return float(value), str(label)


def compute_worst_month(metrics_full: dict[str, Any]) -> tuple[float, str]:
    """Return (worst_month_pct, label) from full-period ``monthly_returns``."""
    monthly = metrics_full.get("monthly_returns") or {}
    if not monthly:
        return 0.0, ""
    label, value = min(monthly.items(), key=lambda kv: float(kv[1]))
    return float(value), str(label)


def monthly_returns_meet_floor(
    metrics_full: dict[str, Any], *, floor_pct: float
) -> tuple[bool, str | None]:
    """True iff every reported calendar month is >= ``floor_pct``."""
    monthly = metrics_full.get("monthly_returns") or {}
    if not monthly:
        return False, "no monthly_returns in metrics"
    for lab, v in monthly.items():
        if float(v) < float(floor_pct):
            return False, f"month {lab} return {float(v):.2f}% < floor {floor_pct}%"
    return True, None


def mar_apr_returns(
    metrics_full: dict[str, Any], *, year: int = 2026
) -> tuple[float | None, float | None]:
    """March and April monthly % from ``monthly_returns`` keys ``YYYY-03``."""
    monthly = metrics_full.get("monthly_returns") or {}
    mk = f"{year}-03"
    ak = f"{year}-04"
    mar = float(monthly[mk]) if mk in monthly else None
    apr = float(monthly[ak]) if ak in monthly else None
    return mar, apr


def evaluate_config(
    cfg: dict[str, Any],
    *,
    full_df: pd.DataFrame,
    windows: list[Window],
    config_path: Path | None = None,
    audit_path: Path | None = None,
    label: str = "iter30",
    i_know_this_is_tournament_evaluation: bool = False,
) -> ConfigEvaluation:
    """Run a config across the full set and every window, scoring it.

    The ``i_know_this_is_tournament_evaluation`` flag exists to make
    test-window openings unambiguous in the audit log. Callers must
    pass ``True`` (matching the project-wide grep token); a false
    value still runs the harness but stamps the audit log entries
    with ``"audit_violation"``. We do NOT silently drop the run —
    the audit log is the system of record.
    """
    if not windows:
        raise ValueError("evaluate_config requires at least one window")
    chash = _config_hash(cfg)

    full_metrics = _run_one(full_df, cfg)
    best_month_pct, best_month_label = compute_best_month(full_metrics)
    worst_month_pct, worst_month_label = compute_worst_month(full_metrics)
    mar_ret, apr_ret = mar_apr_returns(full_metrics)

    audit_entries: list[dict[str, Any]] = []
    window_results: list[WindowResult] = []
    for w in windows:
        # Score validation by running on (research+validation) as a
        # contiguous warmup-into-validation slice. compute_metrics
        # is then computed on the FULL trade list across that slice;
        # the validation read is the trades whose close_time falls in
        # the validation span.
        val_metrics = _run_segment_metrics(
            cfg,
            warmup=w.research,
            scoring=w.validation,
        )
        # Test is opened ONCE: warmup on research+validation, score
        # on the test slice only.
        test_metrics = _run_segment_metrics(
            cfg,
            warmup=w.warmup,
            scoring=w.test,
        )
        score = generalization_score(val_metrics, test_metrics)
        window_results.append(
            WindowResult(
                label=w.label,
                val_metrics=val_metrics,
                test_metrics=test_metrics,
                score=score,
                research_span=w.research_span,
                validation_span=w.validation_span,
                test_span=w.test_span,
            )
        )
        audit_entries.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "label": label,
                "window": w.label,
                "config_hash": chash,
                "config_path": str(config_path) if config_path is not None else None,
                "audit_token": (
                    _AUDIT_OPT_IN_TOKEN
                    if i_know_this_is_tournament_evaluation
                    else "audit_violation"
                ),
                "research_span": [
                    w.research_span[0].isoformat(),
                    w.research_span[1].isoformat(),
                ],
                "validation_span": [
                    w.validation_span[0].isoformat(),
                    w.validation_span[1].isoformat(),
                ],
                "test_span": [
                    w.test_span[0].isoformat(),
                    w.test_span[1].isoformat(),
                ],
                "val_return_pct": val_metrics.get("return_pct"),
                "val_profit_factor": val_metrics.get("profit_factor"),
                "val_cap_violations": val_metrics.get("cap_violations"),
                "test_return_pct": test_metrics.get("return_pct"),
                "test_profit_factor": test_metrics.get("profit_factor"),
                "test_cap_violations": test_metrics.get("cap_violations"),
                "score": score if math.isfinite(score) else None,
            }
        )

    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            for e in audit_entries:
                f.write(json.dumps(e, default=str) + "\n")

    return ConfigEvaluation(
        config_path=config_path,
        config_hash=chash,
        windows=window_results,
        full_metrics=full_metrics,
        best_month_pct=best_month_pct,
        best_month_label=best_month_label,
        worst_month_pct=worst_month_pct,
        worst_month_label=worst_month_label,
        mar_return_pct=mar_ret,
        apr_return_pct=apr_ret,
        full_cap_violations=int(full_metrics.get("cap_violations", 0)),
        full_ruin_flag=bool(full_metrics.get("ruin_flag", False)),
    )


def _run_segment_metrics(
    cfg: dict[str, Any],
    *,
    warmup: pd.DataFrame,
    scoring: pd.DataFrame,
) -> dict[str, Any]:
    """Run engine on warmup+scoring concatenated, but compute metrics
    only on the trades whose close_time falls inside the scoring slice.

    This is the standard "warm the strategy state, score on the
    test/validation slice" pattern. The engine doesn't expose a
    "scoring window" concept directly, so we run the full
    concatenation and post-filter trades by close_time. Equity-curve
    metrics (DD, min_eq) are recomputed against the scoring-slice
    equity restricted to that window.
    """
    if scoring.empty:
        return _empty_metrics()
    combined = pd.concat([warmup, scoring]) if not warmup.empty else scoring
    if not combined.index.is_monotonic_increasing:
        combined = combined.sort_index()
    # Drop duplicate timestamps if any (shouldn't happen on M1).
    combined = combined[~combined.index.duplicated(keep="first")]

    strat, risk, broker, sb = _build_runtime(cfg)
    res = BacktestEngine(strategy=strat, risk=risk, broker=broker).run(combined)

    scoring_start = scoring.index[0]
    scoring_end = scoring.index[-1]

    def _to_utc_ts(t: Any) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts

    scoring_trades = [
        t
        for t in res.trades
        if scoring_start <= _to_utc_ts(t.close_time) <= scoring_end
    ]

    scoring_eq = res.equity_curve[
        (res.equity_curve.index >= scoring_start)
        & (res.equity_curve.index <= scoring_end)
    ]

    # Reuse compute_metrics by constructing a synthetic BacktestResult
    # holding only the scoring slice. ``starting_balance`` is the
    # equity at the START of the scoring window so per-slice returns
    # are measured against the right baseline.
    if scoring_eq.empty:
        slice_sb = sb
    else:
        # Equity series is total-account; the bar BEFORE scoring_start
        # is the slice's starting equity. Fall back to ``sb`` if the
        # combined frame begins at scoring_start (e.g. empty warmup).
        prior = res.equity_curve[res.equity_curve.index < scoring_start]
        slice_sb = float(prior.iloc[-1]) if len(prior) else float(sb)

    synthetic = BacktestResult(
        equity_curve=scoring_eq,
        trades=scoring_trades,
        final_balance=(
            float(scoring_eq.iloc[-1]) if not scoring_eq.empty else slice_sb
        ),
        withdrawn_total=0.0,
    )
    return compute_metrics(synthetic, starting_balance=slice_sb)


def _empty_metrics() -> dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "cap_violations": 0,
        "ruin_flag": False,
        "min_equity_pct": 100.0,
        "monthly_returns": {},
    }


@dataclass
class PromotionVerdict:
    status: str
    reasons: list[str] = field(default_factory=list)


def promotion_status(
    ev: ConfigEvaluation,
    *,
    min_windows_passing: int = 3,
    min_val_pf: float = 1.5,
    min_test_pf: float = 1.2,
    min_best_month_pct: float = 200.0,
    # Iter31: optional Mar/Apr + monthly-floor gate (disabled when None).
    min_mar_apr_return_pct: float | None = None,
    min_month_floor_pct: float | None = None,
) -> PromotionVerdict:
    """Apply generalization + headline-month gate + optional Mar/Apr gate.

    When ``min_mar_apr_return_pct`` and ``min_month_floor_pct`` are set
    (Iter31), a **mar_apr_month_floor** gate must also pass for
    ``promotable``: March and April returns must meet
    ``min_mar_apr_return_pct``, and every calendar month must be >=
    ``min_month_floor_pct``, with zero cap violations and no ruin on
    the full-period metrics.

    Returns one of:
      - ``promotable``: all enabled gates pass.
      - ``candidate``: one gate passes, the other doesn't.
      - ``falsified``: neither gate passes.
      - ``disqualified``: any reported window has cap_violations or
        ruin (the score function already disqualifies these per
        window; this status surfaces them at config level).
    """
    reasons: list[str] = []

    if any(
        w.val_metrics.get("cap_violations", 0) > 0
        or w.test_metrics.get("cap_violations", 0) > 0
        or w.val_metrics.get("ruin_flag", False)
        or w.test_metrics.get("ruin_flag", False)
        for w in ev.windows
    ):
        reasons.append("cap_violations or ruin_flag on at least one reported window")
    if ev.full_cap_violations > 0:
        reasons.append(f"full-period cap_violations={ev.full_cap_violations}")
    if ev.full_ruin_flag:
        reasons.append("full-period ruin_flag=True")

    # Per-window PF gates apply only to passing windows.
    pf_ok = True
    for w in ev.windows:
        if not w.passed:
            continue
        if w.val_metrics.get("profit_factor", 0.0) < min_val_pf:
            pf_ok = False
            reasons.append(
                f"{w.label} val PF {w.val_metrics.get('profit_factor', 0.0):.2f} "
                f"< {min_val_pf}"
            )
        if w.test_metrics.get("profit_factor", 0.0) < min_test_pf:
            pf_ok = False
            reasons.append(
                f"{w.label} test PF {w.test_metrics.get('profit_factor', 0.0):.2f} "
                f"< {min_test_pf}"
            )

    generalization_pass = (
        ev.windows_passing >= min_windows_passing and pf_ok and not reasons
    )

    month_pass = (
        ev.best_month_pct >= min_best_month_pct
        and ev.full_cap_violations == 0
        and not ev.full_ruin_flag
    )
    if not month_pass:
        reasons.append(
            f"best_month_pct={ev.best_month_pct:.2f} (need >= {min_best_month_pct})"
        )

    mar_apr_pass = True
    if min_mar_apr_return_pct is not None and min_month_floor_pct is not None:
        mar_apr_pass = ev.full_cap_violations == 0 and not ev.full_ruin_flag
        if ev.mar_return_pct is None or ev.apr_return_pct is None:
            mar_apr_pass = False
            reasons.append("mar_apr gate: missing March or April in monthly_returns")
        elif (
            ev.mar_return_pct < float(min_mar_apr_return_pct)
            or ev.apr_return_pct < float(min_mar_apr_return_pct)
        ):
            mar_apr_pass = False
            reasons.append(
                f"mar_apr gate: Mar={ev.mar_return_pct:.2f}% Apr={ev.apr_return_pct:.2f}% "
                f"(need >= {min_mar_apr_return_pct}%)"
            )
        ok_floor, floor_msg = monthly_returns_meet_floor(
            ev.full_metrics, floor_pct=float(min_month_floor_pct)
        )
        if not ok_floor:
            mar_apr_pass = False
            if floor_msg:
                reasons.append(f"mar_apr gate: {floor_msg}")
    elif min_mar_apr_return_pct is not None or min_month_floor_pct is not None:
        reasons.append(
            "mar_apr gate misconfigured: set both min_mar_apr_return_pct "
            "and min_month_floor_pct or neither"
        )
        mar_apr_pass = False

    if generalization_pass and month_pass and mar_apr_pass:
        return PromotionVerdict("promotable", reasons=[])
    if generalization_pass or month_pass or mar_apr_pass:
        return PromotionVerdict("candidate", reasons=reasons)
    if any("cap_violations" in r or "ruin_flag" in r for r in reasons):
        return PromotionVerdict("disqualified", reasons=reasons)
    return PromotionVerdict("falsified", reasons=reasons)


def score_config(ev: ConfigEvaluation) -> dict[str, Any]:
    """Flatten a :class:`ConfigEvaluation` into a leaderboard row."""
    row: dict[str, Any] = {
        "config_hash": ev.config_hash,
        "config_path": str(ev.config_path) if ev.config_path else "",
        "windows_passing": ev.windows_passing,
        "n_windows": len(ev.windows),
        "worst_score": ev.worst_score,
        "mean_score": ev.mean_score,
        "best_month_pct": ev.best_month_pct,
        "best_month_label": ev.best_month_label,
        "worst_month_pct": ev.worst_month_pct,
        "worst_month_label": ev.worst_month_label,
        "mar_return_pct": ev.mar_return_pct,
        "apr_return_pct": ev.apr_return_pct,
        "full_return_pct": float(ev.full_metrics.get("return_pct", 0.0)),
        "full_profit_factor": float(ev.full_metrics.get("profit_factor", 0.0)),
        "full_cap_violations": ev.full_cap_violations,
        "full_ruin_flag": ev.full_ruin_flag,
    }
    for w in ev.windows:
        row[f"{w.label}_val_pf"] = float(w.val_metrics.get("profit_factor", 0.0))
        row[f"{w.label}_val_ret"] = float(w.val_metrics.get("return_pct", 0.0))
        row[f"{w.label}_val_cap"] = int(w.val_metrics.get("cap_violations", 0))
        row[f"{w.label}_test_pf"] = float(w.test_metrics.get("profit_factor", 0.0))
        row[f"{w.label}_test_ret"] = float(w.test_metrics.get("return_pct", 0.0))
        row[f"{w.label}_test_cap"] = int(w.test_metrics.get("cap_violations", 0))
        row[f"{w.label}_score"] = w.score if math.isfinite(w.score) else "DQ"
    return row
