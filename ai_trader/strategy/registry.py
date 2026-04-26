"""Name → class registry. Strategies self-register on import."""
from __future__ import annotations

from typing import Type

from .base import BaseStrategy


_REGISTRY: dict[str, Type[BaseStrategy]] = {}


def register_strategy(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
    if not cls.name or cls.name == "base":
        raise ValueError(f"Strategy class {cls} must set a unique .name")
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"Duplicate strategy name: {cls.name}")
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str, **params) -> BaseStrategy:
    # Importing here ensures concrete strategies are registered.
    from . import trend_pullback_fib  # noqa: F401
    from . import donchian_retest  # noqa: F401
    from . import bb_scalper  # noqa: F401
    from . import trend_pullback_scalper  # noqa: F401
    from . import bos_retest_scalper  # noqa: F401
    from . import liquidity_sweep  # noqa: F401
    from . import volume_reversion  # noqa: F401
    from . import news_fade  # noqa: F401
    from . import news_breakout  # noqa: F401
    from . import session_sweep_reclaim  # noqa: F401
    from . import regime_router  # noqa: F401
    from . import squeeze_breakout  # noqa: F401
    from . import momentum_pullback  # noqa: F401
    from . import mtf_zigzag_bos  # noqa: F401
    from . import london_orb  # noqa: F401
    from . import vwap_reversion  # noqa: F401
    from . import ensemble  # noqa: F401
    from . import friday_flush  # noqa: F401
    from . import news_anticipation  # noqa: F401
    from . import asian_breakout  # noqa: F401
    from . import news_continuation  # noqa: F401
    from . import fib_pullback_scalper  # noqa: F401
    from . import pivot_bounce  # noqa: F401
    from . import vwap_sigma_reclaim  # noqa: F401
    from . import bb_squeeze_reversal  # noqa: F401
    from . import momentum_continuation  # noqa: F401
    from . import keltner_mean_reversion  # noqa: F401
    from . import order_block_retest  # noqa: F401
    from . import turn_of_month  # noqa: F401
    from . import asian_break_continuation  # noqa: F401
    from . import atr_squeeze_breakout  # noqa: F401
    from . import ema20_pullback_m15  # noqa: F401
    from . import london_ny_orb  # noqa: F401
    from . import heikin_ashi_trend  # noqa: F401
    from . import three_soldiers  # noqa: F401
    from . import engulfing_reversal  # noqa: F401
    from . import ema_cross_pullback  # noqa: F401
    from . import keltner_breakout  # noqa: F401
    from . import pin_bar_reversal  # noqa: F401
    if name not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {name}. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**params)


def list_strategies() -> list[str]:
    from . import trend_pullback_fib  # noqa: F401
    from . import donchian_retest  # noqa: F401
    from . import bb_scalper  # noqa: F401
    from . import trend_pullback_scalper  # noqa: F401
    from . import bos_retest_scalper  # noqa: F401
    from . import liquidity_sweep  # noqa: F401
    from . import volume_reversion  # noqa: F401
    from . import news_fade  # noqa: F401
    from . import news_breakout  # noqa: F401
    from . import session_sweep_reclaim  # noqa: F401
    from . import regime_router  # noqa: F401
    from . import squeeze_breakout  # noqa: F401
    from . import momentum_pullback  # noqa: F401
    from . import mtf_zigzag_bos  # noqa: F401
    from . import london_orb  # noqa: F401
    from . import vwap_reversion  # noqa: F401
    from . import ensemble  # noqa: F401
    from . import friday_flush  # noqa: F401
    from . import news_anticipation  # noqa: F401
    from . import asian_breakout  # noqa: F401
    from . import news_continuation  # noqa: F401
    from . import fib_pullback_scalper  # noqa: F401
    from . import pivot_bounce  # noqa: F401
    from . import vwap_sigma_reclaim  # noqa: F401
    from . import bb_squeeze_reversal  # noqa: F401
    from . import momentum_continuation  # noqa: F401
    from . import keltner_mean_reversion  # noqa: F401
    from . import order_block_retest  # noqa: F401
    from . import turn_of_month  # noqa: F401
    from . import asian_break_continuation  # noqa: F401
    from . import atr_squeeze_breakout  # noqa: F401
    from . import ema20_pullback_m15  # noqa: F401
    from . import london_ny_orb  # noqa: F401
    from . import heikin_ashi_trend  # noqa: F401
    from . import three_soldiers  # noqa: F401
    from . import engulfing_reversal  # noqa: F401
    from . import ema_cross_pullback  # noqa: F401
    from . import keltner_breakout  # noqa: F401
    from . import pin_bar_reversal  # noqa: F401
    return sorted(_REGISTRY)
