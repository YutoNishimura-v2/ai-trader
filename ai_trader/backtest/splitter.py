"""Walk-forward splitter.

The iteration loop (plan v3) must not peek at the tournament window
while tuning. ``split`` returns three non-overlapping DataFrames:

- ``research``:   where hyperparameter search is allowed
- ``validation``: used to confirm the research winner is not
                  overfit; no parameter choices may be made here
- ``tournament``: revealed only when we decide "we're done";
                  touched exactly once per promotion attempt

The splitter is intentionally a pure function on an indexed
DataFrame so the sweep harness can call it from anywhere without
global state. Anti-overfitting discipline is enforced outside the
splitter by the sweep harness.

Ratio defaults come from plan v3 (roughly 9 / 2 / 1 months out of
12 = 0.75 / 0.17 / 0.08).
"""
from __future__ import annotations

from dataclasses import dataclass

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
