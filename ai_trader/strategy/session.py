"""Trading-session gate.

Published gold-scalping frameworks converge on "London + NY only"
because (a) spreads widen materially outside those hours and (b)
the moves that make the edge are driven by London-open / NY-open /
London-close volatility. Asian session on gold is mostly noise.

UTC ranges used here (approximate, deliberately generous at the
edges):

- London session:   07:00 - 16:00 UTC
- New York session: 12:00 - 21:00 UTC
- Overlap only:     12:00 - 16:00 UTC

This module is stateless and fast: it takes a timestamp, returns
True/False. Strategies decide whether to call it.
"""
from __future__ import annotations

from datetime import time


LONDON_OPEN_UTC = time(7, 0)
LONDON_CLOSE_UTC = time(16, 0)
NY_OPEN_UTC = time(12, 0)
NY_CLOSE_UTC = time(21, 0)


def in_london(t: time) -> bool:
    return LONDON_OPEN_UTC <= t < LONDON_CLOSE_UTC


def in_ny(t: time) -> bool:
    return NY_OPEN_UTC <= t < NY_CLOSE_UTC


def in_overlap(t: time) -> bool:
    return in_london(t) and in_ny(t)


def in_london_or_ny(t: time) -> bool:
    return in_london(t) or in_ny(t)


def check_session(t: time, mode: str) -> bool:
    """Dispatch helper for config-driven strategies.

    ``mode``: "always" | "london" | "ny" | "overlap" | "london_or_ny"
    """
    if mode == "always":
        return True
    if mode == "london":
        return in_london(t)
    if mode == "ny":
        return in_ny(t)
    if mode == "overlap":
        return in_overlap(t)
    if mode == "london_or_ny":
        return in_london_or_ny(t)
    raise ValueError(f"unknown session mode: {mode!r}")
