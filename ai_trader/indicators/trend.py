"""Trend classifier driven by swing pivots.

We look at the most recent ``min_legs * 2`` pivots and ask:

- Are the last N swing highs monotonically rising AND the last N
  swing lows monotonically rising?  → uptrend
- Mirrored for downtrend.
- Otherwise → range.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .swings import SwingPoint


class TrendState(str, Enum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


@dataclass(frozen=True)
class TrendInfo:
    state: TrendState
    last_high: SwingPoint | None
    last_low: SwingPoint | None
    impulse_start: SwingPoint | None
    impulse_end: SwingPoint | None


def _last_n_of(swings: Sequence[SwingPoint], kind: str, n: int) -> list[SwingPoint]:
    return [s for s in swings if s.kind == kind][-n:]


def classify_trend(swings: Sequence[SwingPoint], min_legs: int = 2) -> TrendInfo:
    """Classify trend from the tail of the swing list.

    ``min_legs`` is the number of confirming highs/lows required on
    each side. 2 means "2 higher highs AND 2 higher lows".
    """
    if min_legs < 2:
        raise ValueError("min_legs must be >= 2")

    highs = _last_n_of(swings, "high", min_legs)
    lows = _last_n_of(swings, "low", min_legs)

    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None

    if len(highs) < min_legs or len(lows) < min_legs:
        return TrendInfo(TrendState.RANGE, last_high, last_low, None, None)

    rising_highs = all(highs[i].price < highs[i + 1].price for i in range(len(highs) - 1))
    rising_lows = all(lows[i].price < lows[i + 1].price for i in range(len(lows) - 1))
    falling_highs = all(highs[i].price > highs[i + 1].price for i in range(len(highs) - 1))
    falling_lows = all(lows[i].price > lows[i + 1].price for i in range(len(lows) - 1))

    if rising_highs and rising_lows:
        # Last impulse leg is from the most recent low to the most recent high.
        if last_low is not None and last_high is not None and last_low.iloc < last_high.iloc:
            return TrendInfo(TrendState.UP, last_high, last_low, last_low, last_high)
        return TrendInfo(TrendState.UP, last_high, last_low, None, None)

    if falling_highs and falling_lows:
        if last_high is not None and last_low is not None and last_high.iloc < last_low.iloc:
            return TrendInfo(TrendState.DOWN, last_high, last_low, last_high, last_low)
        return TrendInfo(TrendState.DOWN, last_high, last_low, None, None)

    return TrendInfo(TrendState.RANGE, last_high, last_low, None, None)
