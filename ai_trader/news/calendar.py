"""News blackout calendar (plan v3 §A.7).

For both XAUUSD and BTCUSD: no new entries in a ±window around
high-impact events. Phase 1 uses a hand-maintained CSV; replacing
with a live economic-calendar API later is a pure implementation
swap behind the ``NewsCalendar`` interface.

CSV schema (``data/news/<source>.csv``):

    time,impact,instrument,event
    2026-05-02T12:30:00Z,high,XAUUSD,US NFP
    2026-05-03T14:00:00Z,high,XAUUSD,US CPI

- ``time`` is ISO-8601 UTC.
- ``impact`` is free-form but ``high`` is the one we care about.
- ``instrument`` is an exact symbol match; ``*`` means all.
- ``event`` is human-readable; used in review packets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class NewsEvent:
    time: datetime
    impact: str
    instrument: str  # "*" or exact symbol
    event: str

    def affects(self, symbol: str) -> bool:
        return self.instrument == "*" or self.instrument == symbol


class NewsCalendar:
    def __init__(
        self,
        events: Iterable[NewsEvent] = (),
        window_minutes: int = 30,
        impact_filter: tuple[str, ...] = ("high",),
    ) -> None:
        self.events: list[NewsEvent] = sorted(events, key=lambda e: e.time)
        self.window = timedelta(minutes=window_minutes)
        self.impact_filter = impact_filter

    def in_blackout(self, symbol: str, now: datetime) -> NewsEvent | None:
        """Return the matching event if ``now`` is inside its blackout."""
        now = now.astimezone(timezone.utc)
        for e in self.events:
            if e.impact not in self.impact_filter:
                continue
            if not e.affects(symbol):
                continue
            # events are sorted; once event.time - window > now we can break.
            if e.time - self.window > now:
                break
            if e.time - self.window <= now <= e.time + self.window:
                return e
        return None


class NoNewsCalendar(NewsCalendar):
    """Null object: never in blackout. Default for backtests without
    a configured calendar."""

    def __init__(self) -> None:
        super().__init__(events=())

    def in_blackout(self, symbol: str, now: datetime) -> NewsEvent | None:
        return None


def load_news_csv(path: str | Path) -> list[NewsEvent]:
    import csv

    out: list[NewsEvent] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_raw = row["time"].strip()
            # Normalise the common 'Z' suffix.
            if ts_raw.endswith("Z"):
                ts_raw = ts_raw[:-1] + "+00:00"
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append(
                NewsEvent(
                    time=ts,
                    impact=row.get("impact", "").strip().lower(),
                    instrument=row.get("instrument", "*").strip(),
                    event=row.get("event", "").strip(),
                )
            )
    return out
