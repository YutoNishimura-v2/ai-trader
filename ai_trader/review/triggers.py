"""Review-session trigger engine.

Plan v3 §A.10. The bot posts a review packet and pauses on any of:

- end of UTC day (mandatory, including quiet days — silence is data)
- two consecutive SL-hit trades on any instrument
- any non-negotiable-rule violation
- weekly wrap (Sunday UTC)

This module is pure state-machine logic. It decides *what* triggers
were raised given the current ledger/state; it does NOT do I/O,
packet generation, or pausing. That's the runner's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum


class ReviewTriggerKind(str, Enum):
    EOD = "eod"
    WEEKLY = "weekly"
    CONSECUTIVE_SL = "consecutive_sl"
    RULE_VIOLATION = "rule_violation"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class ReviewTrigger:
    kind: ReviewTriggerKind
    when: datetime
    detail: str = ""


@dataclass
class TriggerEngine:
    """Stateful helper used once per bar tick.

    - ``consecutive_sl_threshold`` defaults to 2 (plan v3).
    - ``weekly_dow`` is the weekday (0=Mon, 6=Sun) at which we emit
      the weekly wrap; default Sunday.
    - The engine *emits* a trigger at most once per (kind, day).
      The runner is responsible for idempotence after pause/resume.
    """

    consecutive_sl_threshold: int = 2
    weekly_dow: int = 6

    _last_day_seen: date | None = None
    _emitted_today: set[ReviewTriggerKind] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._emitted_today = set()

    def _roll_day(self, d: date) -> None:
        if self._last_day_seen != d:
            self._last_day_seen = d
            self._emitted_today = set()

    def tick(
        self,
        now: datetime,
        *,
        consecutive_sl: int,
        kill_switch: bool,
        day_rollover: bool,
    ) -> list[ReviewTrigger]:
        """Evaluate triggers at a moment in time.

        - ``consecutive_sl`` is the current streak counter from the
          RiskManager.
        - ``kill_switch`` is whether the daily envelope was hit.
        - ``day_rollover`` is True on the first tick of a new UTC day
          — the runner emits the *previous* day's EOD packet on that
          tick.
        """
        triggers: list[ReviewTrigger] = []
        today = now.astimezone(timezone.utc).date()
        self._roll_day(today)

        if day_rollover:
            triggers.append(
                ReviewTrigger(
                    kind=ReviewTriggerKind.EOD, when=now, detail="end-of-day wrap"
                )
            )
            # Weekly wrap also fires on the day-rollover tick for the
            # configured weekday. We use the *previous* day's weekday.
            prev_dow = (today.toordinal() - 1) % 7
            # Python weekday(): Mon=0..Sun=6. date.toordinal() % 7 gives
            # a different anchor, so compute via date.
            from datetime import timedelta

            prev_day = today - timedelta(days=1)
            if prev_day.weekday() == self.weekly_dow:
                triggers.append(
                    ReviewTrigger(
                        kind=ReviewTriggerKind.WEEKLY, when=now, detail="weekly wrap"
                    )
                )

        if kill_switch and ReviewTriggerKind.KILL_SWITCH not in self._emitted_today:
            triggers.append(
                ReviewTrigger(
                    kind=ReviewTriggerKind.KILL_SWITCH,
                    when=now,
                    detail="daily envelope hit",
                )
            )
            self._emitted_today.add(ReviewTriggerKind.KILL_SWITCH)

        if (
            consecutive_sl >= self.consecutive_sl_threshold
            and ReviewTriggerKind.CONSECUTIVE_SL not in self._emitted_today
        ):
            triggers.append(
                ReviewTrigger(
                    kind=ReviewTriggerKind.CONSECUTIVE_SL,
                    when=now,
                    detail=f"{consecutive_sl} consecutive SL-hit trades",
                )
            )
            self._emitted_today.add(ReviewTriggerKind.CONSECUTIVE_SL)

        return triggers
