"""Interleaved + recent-only splitter modes."""
from datetime import timezone

import pandas as pd
import pytest

from ai_trader.backtest.splitter import (
    InterleavedSplit,
    load_interleaved_held_out,
    load_recent_only_held_out,
    split_interleaved,
    split_recent_only,
)


def _bars(n: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz=timezone.utc)
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=idx,
    )


def test_interleaved_round_robin_distribution():
    df = _bars(20_000)
    s = split_interleaved(
        df, block_bars=1000,
        research_per_cycle=3, validation_per_cycle=1, tournament_per_cycle=1,
    )
    assert isinstance(s, InterleavedSplit)
    # 20 blocks / 5-block cycles = 4 cycles; 12 R, 4 V, 4 T.
    assert len(s.research) == 12
    assert len(s.validation) == 4
    assert len(s.tournament) == 4


def test_interleaved_blocks_are_contiguous():
    """Each block must be a contiguous time slice (no gaps WITHIN a
    block); cross-block jumps are allowed and expected."""
    df = _bars(10_000)
    s = split_interleaved(df, block_bars=1000, research_per_cycle=2,
                          validation_per_cycle=1, tournament_per_cycle=1)
    for block in s.research + s.validation + s.tournament:
        # Within a block: index must be evenly spaced (1min freq).
        deltas = block.index.to_series().diff().dropna().unique()
        assert len(deltas) == 1
        assert deltas[0] == pd.Timedelta("1min")


def test_interleaved_rejects_too_few_blocks():
    df = _bars(500)  # less than one cycle of blocks
    with pytest.raises(ValueError, match="too small"):
        split_interleaved(df, block_bars=1000)


def test_load_interleaved_hides_tournament_by_default():
    df = _bars(20_000)
    s = load_interleaved_held_out(df, block_bars=1000)
    assert s.tournament == []
    assert len(s.research) > 0
    assert len(s.validation) > 0


def test_load_interleaved_reveals_on_opt_in():
    df = _bars(20_000)
    s = load_interleaved_held_out(
        df, block_bars=1000, i_know_this_is_tournament_evaluation=True,
    )
    assert len(s.tournament) > 0


def test_split_recent_only_orders_windows_temporally():
    # 60 days of M1 = 86400 bars
    df = _bars(60 * 24 * 60)
    s = split_recent_only(df, research_days=20, validation_days=10, tournament_days=5)
    assert s.research.index.max() < s.validation.index.min()
    assert s.validation.index.max() < s.tournament.index.min()
    # Tournament span ~ 5 days.
    span = (s.tournament.index.max() - s.tournament.index.min()).total_seconds() / 86400
    assert 4.5 <= span <= 5.5


def test_load_recent_only_held_out_hides_tournament():
    df = _bars(60 * 24 * 60)
    s = load_recent_only_held_out(df, research_days=20, validation_days=10, tournament_days=5)
    assert len(s.tournament) == 0
    assert len(s.research) > 0
    assert len(s.validation) > 0
