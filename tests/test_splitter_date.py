"""Date-based splitter (plan v3 recent-regime stance)."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from ai_trader.backtest.splitter import (
    load_recent_held_out,
    split_by_date,
    split_recent_tournament,
)


def _bars_days(days: int) -> pd.DataFrame:
    n = days * 24 * 12  # M5 bars
    idx = pd.date_range("2025-01-01", periods=n, freq="5min", tz=timezone.utc)
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=idx,
    )


def test_split_by_date_basic():
    df = _bars_days(120)
    s = split_by_date(
        df,
        validation_start="2025-03-01",
        tournament_start="2025-04-01",
    )
    assert s.research.index.max() < pd.Timestamp("2025-03-01", tz="UTC")
    assert s.validation.index.min() >= pd.Timestamp("2025-03-01", tz="UTC")
    assert s.validation.index.max() < pd.Timestamp("2025-04-01", tz="UTC")
    assert s.tournament.index.min() >= pd.Timestamp("2025-04-01", tz="UTC")


def test_split_by_date_rejects_reversed_boundaries():
    df = _bars_days(60)
    with pytest.raises(ValueError, match="earlier than"):
        split_by_date(
            df,
            validation_start="2025-02-15",
            tournament_start="2025-02-01",
        )


def test_split_recent_tournament_uses_last_timestamp():
    df = _bars_days(120)  # ends ~2025-05-01
    s = split_recent_tournament(df, tournament_days=30, validation_days=60)
    # Tournament is the last ~30 days.
    tournament_span = (s.tournament.index.max() - s.tournament.index.min()).days
    assert 28 <= tournament_span <= 31
    # Validation is the 60 days before tournament.
    val_span = (s.validation.index.max() - s.validation.index.min()).days
    assert 58 <= val_span <= 61
    # Temporal non-overlap.
    assert s.research.index.max() < s.validation.index.min()
    assert s.validation.index.max() < s.tournament.index.min()


def test_load_recent_held_out_hides_tournament_by_default():
    df = _bars_days(120)
    s = load_recent_held_out(df, tournament_days=30, validation_days=60)
    assert len(s.tournament) == 0
    assert len(s.research) > 0
    assert len(s.validation) > 0


def test_load_recent_held_out_reveals_on_opt_in():
    df = _bars_days(120)
    s = load_recent_held_out(
        df, tournament_days=30, validation_days=60,
        i_know_this_is_tournament_evaluation=True,
    )
    assert len(s.tournament) > 0


def test_split_by_date_rejects_empty_window():
    df = _bars_days(30)
    # Tournament boundary past the end of the data → empty tournament.
    with pytest.raises(ValueError, match="empty window"):
        split_by_date(
            df,
            validation_start="2025-01-10",
            tournament_start="2025-12-01",
        )
