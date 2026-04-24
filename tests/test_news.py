"""News blackout (plan v3 §A.7)."""
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from ai_trader.news.calendar import (
    NewsCalendar,
    NewsEvent,
    NoNewsCalendar,
    load_news_csv,
)


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 2, hour, minute, tzinfo=timezone.utc)


def _nfp() -> NewsEvent:
    return NewsEvent(time=_t(12, 30), impact="high", instrument="*", event="US NFP")


def test_in_blackout_within_window():
    cal = NewsCalendar(events=[_nfp()], window_minutes=30)
    assert cal.in_blackout("XAUUSD", _t(12, 0)) is not None
    assert cal.in_blackout("XAUUSD", _t(12, 30)) is not None
    assert cal.in_blackout("XAUUSD", _t(13, 0)) is not None


def test_outside_blackout():
    cal = NewsCalendar(events=[_nfp()], window_minutes=30)
    assert cal.in_blackout("XAUUSD", _t(11, 29)) is None
    assert cal.in_blackout("XAUUSD", _t(13, 1)) is None


def test_instrument_scoping():
    ev = NewsEvent(time=_t(12, 30), impact="high", instrument="XAUUSD", event="US NFP")
    cal = NewsCalendar(events=[ev], window_minutes=30)
    assert cal.in_blackout("XAUUSD", _t(12, 0)) is not None
    assert cal.in_blackout("BTCUSD", _t(12, 0)) is None


def test_impact_filter_excludes_low_impact():
    low = NewsEvent(time=_t(12, 30), impact="low", instrument="*", event="minor")
    cal = NewsCalendar(events=[low], window_minutes=30, impact_filter=("high",))
    assert cal.in_blackout("XAUUSD", _t(12, 30)) is None


def test_no_news_calendar_is_never_in_blackout():
    cal = NoNewsCalendar()
    assert cal.in_blackout("XAUUSD", _t(12, 30)) is None


def test_load_from_csv(tmp_path: Path):
    p = tmp_path / "news.csv"
    p.write_text(
        "time,impact,instrument,event\n"
        "2026-05-02T12:30:00Z,high,*,US NFP\n"
        "2026-05-13T12:30:00Z,high,XAUUSD,US CPI\n"
    )
    events = load_news_csv(p)
    assert len(events) == 2
    assert events[0].impact == "high"
    assert events[1].instrument == "XAUUSD"
