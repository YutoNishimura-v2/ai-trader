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
    from . import ensemble  # noqa: F401
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
    from . import ensemble  # noqa: F401
    return sorted(_REGISTRY)
