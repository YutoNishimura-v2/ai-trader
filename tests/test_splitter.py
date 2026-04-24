"""Walk-forward splitter (plan v3 research methodology)."""
from datetime import timezone

import pandas as pd
import pytest

from ai_trader.backtest.splitter import (
    Split,
    load_with_tournament_held_out,
    split,
)
from ai_trader.data.synthetic import generate_synthetic_ohlcv


def _bars(n: int) -> pd.DataFrame:
    df = pd.DataFrame(
        {"open": range(n), "high": range(n), "low": range(n), "close": range(n), "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz=timezone.utc),
    )
    return df


def test_split_sizes_approx_right():
    df = _bars(1200)
    s = split(df, research_ratio=0.75, validation_ratio=0.17)
    assert len(s.research) == 900
    assert len(s.validation) == 204
    assert len(s.tournament) == 96
    assert len(s.research) + len(s.validation) + len(s.tournament) == len(df)


def test_split_is_temporal_nonoverlapping():
    df = generate_synthetic_ohlcv(days=5, timeframe="M5", seed=1)
    s = split(df)
    assert s.research.index.max() < s.validation.index.min()
    assert s.validation.index.max() < s.tournament.index.min()


def test_split_rejects_ratios_that_eat_tournament():
    df = _bars(100)
    with pytest.raises(ValueError, match="< 1.0"):
        split(df, research_ratio=0.6, validation_ratio=0.5)


def test_split_rejects_unsorted_index():
    df = _bars(100).iloc[::-1]
    with pytest.raises(ValueError, match="sorted"):
        split(df)


def test_held_out_loader_hides_tournament_by_default():
    df = _bars(1200)
    s = load_with_tournament_held_out(df)
    assert len(s.tournament) == 0
    # research + validation still populated
    assert len(s.research) > 0
    assert len(s.validation) > 0


def test_held_out_loader_reveals_on_explicit_opt_in():
    df = _bars(1200)
    s = load_with_tournament_held_out(
        df, i_know_this_is_tournament_evaluation=True
    )
    assert len(s.tournament) > 0
