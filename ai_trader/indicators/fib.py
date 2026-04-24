"""Fibonacci retracement zone of the last impulse leg."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FibZone:
    """Price band between two fib levels of an impulse.

    ``low`` / ``high`` are absolute prices (low <= high).
    ``impulse_low`` / ``impulse_high`` are the endpoints of the
    impulse leg that generated the zone.
    """

    low: float
    high: float
    impulse_low: float
    impulse_high: float

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


def fib_retracement_zone(
    impulse_low: float,
    impulse_high: float,
    level_min: float = 0.382,
    level_max: float = 0.500,
) -> FibZone:
    """Return the retracement zone for an upward impulse.

    For a downward impulse, pass ``impulse_low > impulse_high`` and
    the same formula works — the zone is computed relative to the
    retracement from the *end* of the impulse back toward its start.
    """
    if not (0.0 <= level_min <= level_max <= 1.0):
        raise ValueError("levels must satisfy 0 <= min <= max <= 1")

    # For an up-impulse (end > start): retrace = end - (end-start)*level.
    # For a down-impulse (end < start): retrace = end + (start-end)*level.
    # Both reduce to: start + (end - start) * (1 - level).
    start, end = impulse_low, impulse_high
    span = end - start

    p_min = start + span * (1.0 - level_max)
    p_max = start + span * (1.0 - level_min)
    low, high = (p_min, p_max) if p_min <= p_max else (p_max, p_min)
    return FibZone(low=low, high=high, impulse_low=impulse_low, impulse_high=impulse_high)
