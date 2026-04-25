"""Walk-forward splitter.

The iteration loop (plan v3) must not peek at the tournament window
while tuning. A split returns three non-overlapping DataFrames:

- ``research``:   where hyperparameter search is allowed
- ``validation``: used to confirm the research winner is not
                  overfit; no parameter choices may be made here
- ``tournament``: revealed only when we decide "we're done";
                  touched exactly once per promotion attempt

Two split strategies are offered:

- ``split`` (ratio-based): useful when no regime constraint applies.
- ``split_by_date`` (absolute boundaries): required when the most
  recent N days matter more than a statistically-representative
  sample. Per the user's direction (2026-04-24), performance on the
  recent regime dominates: we pin the tournament and validation
  windows to *specific* recent calendar ranges rather than
  proportional slices, so the final verdict is always "does this
  work right now" and not "does this work on average over history".

The splitter is pure on an indexed DataFrame; anti-overfitting
discipline is enforced outside the splitter by the sweep harness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd


@dataclass(frozen=True)
class Split:
    research: pd.DataFrame
    validation: pd.DataFrame
    tournament: pd.DataFrame

    def __repr__(self) -> str:
        return (
            f"Split(research={len(self.research)} bars, "
            f"validation={len(self.validation)} bars, "
            f"tournament={len(self.tournament)} bars)"
        )


@dataclass(frozen=True)
class InterleavedSplit:
    """Block-based split.

    Each role holds a list of contiguous DataFrame blocks. Blocks
    are independent sub-backtests; the engine runs once per block
    and the metrics are aggregated. Inside each block, the no-
    lookahead discipline holds: ``prepare()`` only sees that block.

    This is the right fit for "mix all months together" — each
    block samples a different regime so the resulting research /
    validation / tournament aren't dominated by any single month.
    """

    research: list[pd.DataFrame]
    validation: list[pd.DataFrame]
    tournament: list[pd.DataFrame]

    @property
    def research_bars(self) -> int:
        return sum(len(b) for b in self.research)

    @property
    def validation_bars(self) -> int:
        return sum(len(b) for b in self.validation)

    @property
    def tournament_bars(self) -> int:
        return sum(len(b) for b in self.tournament)

    def __repr__(self) -> str:
        return (
            f"InterleavedSplit(research={len(self.research)} blocks/{self.research_bars} bars, "
            f"validation={len(self.validation)} blocks/{self.validation_bars} bars, "
            f"tournament={len(self.tournament)} blocks/{self.tournament_bars} bars)"
        )


def split(
    df: pd.DataFrame,
    *,
    research_ratio: float = 0.75,
    validation_ratio: float = 0.17,
) -> Split:
    """Split a time-indexed OHLCV frame into research / validation /
    tournament.

    Ratios must sum to < 1.0; the remainder goes to the tournament
    window. The split is strictly temporal: research < validation <
    tournament in time.
    """
    if research_ratio <= 0 or validation_ratio <= 0:
        raise ValueError("ratios must be positive")
    if research_ratio + validation_ratio >= 1.0:
        raise ValueError(
            "research_ratio + validation_ratio must be < 1.0 "
            "to leave a non-empty tournament window"
        )
    if not df.index.is_monotonic_increasing:
        raise ValueError("DataFrame index must be sorted ascending")

    n = len(df)
    i1 = int(n * research_ratio)
    i2 = i1 + int(n * validation_ratio)
    research = df.iloc[:i1]
    validation = df.iloc[i1:i2]
    tournament = df.iloc[i2:]
    if len(research) == 0 or len(validation) == 0 or len(tournament) == 0:
        raise ValueError(f"split produced an empty window for n={n}")
    return Split(research=research, validation=validation, tournament=tournament)


def split_by_date(
    df: pd.DataFrame,
    *,
    tournament_start: pd.Timestamp | datetime | str,
    validation_start: pd.Timestamp | datetime | str,
) -> Split:
    """Split by absolute calendar cutoffs.

    - ``research`` = rows with index < validation_start
    - ``validation`` = validation_start <= index < tournament_start
    - ``tournament`` = index >= tournament_start

    Both cutoffs must be UTC-aware timestamps; naive inputs are
    assumed UTC. All three windows must be non-empty.

    This is the preferred splitter when recent-regime performance
    dominates (plan v3 stance as of 2026-04-24): the tournament is
    always the user-specified recent slice, not a proportion of
    history.
    """
    if not df.index.is_monotonic_increasing:
        raise ValueError("DataFrame index must be sorted ascending")
    v = _to_utc_ts(validation_start)
    t = _to_utc_ts(tournament_start)
    if not v < t:
        raise ValueError("validation_start must be earlier than tournament_start")

    # Ensure frame index is tz-aware so comparisons are well-defined.
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
        df = df.copy()
        df.index = idx

    research = df[df.index < v]
    validation = df[(df.index >= v) & (df.index < t)]
    tournament = df[df.index >= t]
    if len(research) == 0 or len(validation) == 0 or len(tournament) == 0:
        raise ValueError(
            f"date-based split produced empty window "
            f"(r={len(research)}, v={len(validation)}, t={len(tournament)})"
        )
    return Split(research=research, validation=validation, tournament=tournament)


def split_recent_tournament(
    df: pd.DataFrame,
    *,
    tournament_days: int = 30,
    validation_days: int = 60,
    now: datetime | None = None,
) -> Split:
    """Convenience wrapper for the plan-v3 recent-regime stance.

    Tournament = last ``tournament_days`` (default 30).
    Validation = the ``validation_days`` before that (default 60).
    Research   = everything earlier.

    ``now`` defaults to the DataFrame's last timestamp so splits are
    reproducible across invocations.
    """
    if now is None:
        last = df.index[-1]
        last = last.to_pydatetime() if hasattr(last, "to_pydatetime") else last
        now = last
    now_ts = _to_utc_ts(now)
    tournament_start = now_ts - pd.Timedelta(days=tournament_days)
    validation_start = tournament_start - pd.Timedelta(days=validation_days)
    return split_by_date(
        df,
        tournament_start=tournament_start,
        validation_start=validation_start,
    )


def _to_utc_ts(x: pd.Timestamp | datetime | str) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


class TournamentHeldOutError(RuntimeError):
    """Raised by the guarded loader when somebody accidentally asks
    for the tournament window without the explicit opt-in token."""


def load_with_tournament_held_out(
    df: pd.DataFrame,
    *,
    research_ratio: float = 0.75,
    validation_ratio: float = 0.17,
    i_know_this_is_tournament_evaluation: bool = False,
) -> Split:
    """Loader that protects the tournament window from accidental use.

    Strategy-tuning code and CI must call this **without** the opt-in
    flag. The returned ``Split.tournament`` is an empty DataFrame in
    that case, so a backtest that accidentally concatenates all three
    windows still gets research+validation only.

    The sweep harness and the promotion-evaluation script must call
    it with ``i_know_this_is_tournament_evaluation=True`` — and we
    grep the codebase for that phrase in review.
    """
    s = split(df, research_ratio=research_ratio, validation_ratio=validation_ratio)
    if i_know_this_is_tournament_evaluation:
        return s
    # Return the tournament window zeroed-out so naive consumers
    # cannot tune against it.
    empty = df.iloc[0:0]
    return Split(research=s.research, validation=s.validation, tournament=empty)


def load_recent_held_out(
    df: pd.DataFrame,
    *,
    tournament_days: int = 30,
    validation_days: int = 60,
    now: datetime | None = None,
    i_know_this_is_tournament_evaluation: bool = False,
) -> Split:
    """Date-based held-out loader for the plan-v3 recent-regime stance.

    Like ``load_with_tournament_held_out`` but the tournament window
    is a fixed recent calendar range (default: last 30 days) rather
    than a proportion. Everything else about the opt-in contract
    is unchanged.
    """
    s = split_recent_tournament(
        df,
        tournament_days=tournament_days,
        validation_days=validation_days,
        now=now,
    )
    if i_know_this_is_tournament_evaluation:
        return s
    empty = df.iloc[0:0]
    return Split(research=s.research, validation=s.validation, tournament=empty)


def split_interleaved(
    df: pd.DataFrame,
    *,
    block_bars: int = 5760,            # ~4 days of M1 bars
    research_per_cycle: int = 3,       # 3 research blocks per cycle
    validation_per_cycle: int = 1,
    tournament_per_cycle: int = 1,
) -> InterleavedSplit:
    """Round-robin block split.

    Chops the frame into ``block_bars``-sized blocks. Each cycle of
    ``R+V+T`` blocks deals R blocks to research, then V to validation,
    then T to tournament, then repeat. Result: every role samples
    every regime, addressing the user concern that a contiguous
    Jan-Feb research / Mar-Apr validation split is unfair when
    regimes change mid-window.

    Each block is a contiguous time window so prepare()'s causal
    indicators are still well-defined inside it; cross-block
    boundaries are NEVER stitched together (that would create a
    fake price jump that looks like a swing).
    """
    if not df.index.is_monotonic_increasing:
        raise ValueError("DataFrame index must be sorted ascending")
    if block_bars <= 0:
        raise ValueError("block_bars must be > 0")
    if research_per_cycle <= 0 or validation_per_cycle <= 0 or tournament_per_cycle <= 0:
        raise ValueError("each per_cycle count must be > 0")

    n = len(df)
    blocks: list[pd.DataFrame] = []
    for start in range(0, n, block_bars):
        end = min(start + block_bars, n)
        if end - start >= block_bars // 2:   # drop runt at the tail
            blocks.append(df.iloc[start:end])

    research: list[pd.DataFrame] = []
    validation: list[pd.DataFrame] = []
    tournament: list[pd.DataFrame] = []
    cycle_len = research_per_cycle + validation_per_cycle + tournament_per_cycle
    for i, block in enumerate(blocks):
        pos = i % cycle_len
        if pos < research_per_cycle:
            research.append(block)
        elif pos < research_per_cycle + validation_per_cycle:
            validation.append(block)
        else:
            tournament.append(block)

    if not research or not validation or not tournament:
        raise ValueError(
            f"interleaved split too small: {len(blocks)} blocks; need cycle_len={cycle_len}"
        )
    return InterleavedSplit(research=research, validation=validation, tournament=tournament)


def load_interleaved_held_out(
    df: pd.DataFrame,
    *,
    block_bars: int = 5760,
    research_per_cycle: int = 3,
    validation_per_cycle: int = 1,
    tournament_per_cycle: int = 1,
    i_know_this_is_tournament_evaluation: bool = False,
) -> InterleavedSplit:
    """Defensive loader for ``split_interleaved`` mirroring the
    contiguous helpers' opt-in contract."""
    s = split_interleaved(
        df,
        block_bars=block_bars,
        research_per_cycle=research_per_cycle,
        validation_per_cycle=validation_per_cycle,
        tournament_per_cycle=tournament_per_cycle,
    )
    if i_know_this_is_tournament_evaluation:
        return s
    return InterleavedSplit(research=s.research, validation=s.validation, tournament=[])


def split_recent_only(
    df: pd.DataFrame,
    *,
    research_days: int = 30,
    validation_days: int = 14,
    tournament_days: int = 7,
    now: datetime | None = None,
) -> Split:
    """Recent-only contiguous split.

    All three windows are pulled from the tail of the frame, in
    order: research is the oldest, tournament is the newest.
    Total span = research_days + validation_days + tournament_days
    (default 51 days). Use when "the most recent regime is the
    only one that matters and we want all three windows inside it."
    """
    if now is None:
        last = df.index[-1]
        now = last.to_pydatetime() if hasattr(last, "to_pydatetime") else last
    now_ts = _to_utc_ts(now)
    t_start = now_ts - pd.Timedelta(days=tournament_days)
    v_start = t_start - pd.Timedelta(days=validation_days)
    r_start = v_start - pd.Timedelta(days=research_days)
    return split_by_date(
        df.loc[df.index >= r_start] if df.index.tz is not None else df,
        validation_start=v_start,
        tournament_start=t_start,
    )


def load_recent_only_held_out(
    df: pd.DataFrame,
    *,
    research_days: int = 30,
    validation_days: int = 14,
    tournament_days: int = 7,
    now: datetime | None = None,
    i_know_this_is_tournament_evaluation: bool = False,
) -> Split:
    s = split_recent_only(
        df,
        research_days=research_days,
        validation_days=validation_days,
        tournament_days=tournament_days,
        now=now,
    )
    if i_know_this_is_tournament_evaluation:
        return s
    empty = df.iloc[0:0]
    return Split(research=s.research, validation=s.validation, tournament=empty)
