"""YAML config loader with simple `extends:` support.

Keeps config loading dependency-light (no pydantic) because the whole
spec fits in a flat dict and the schema is small enough to validate
inline at the call sites that need it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_REPLACE_SENTINEL = "__replace__"


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge two dicts.

    Override behaviour:
    - Default: dict values are recursively merged; non-dict values
      are replaced.
    - If a dict in ``override`` contains the key ``__replace__: true``,
      the entire subtree is *replaced* rather than merged. This is
      essential for sections whose key set is strategy-dependent
      (e.g. ``strategy.params`` differs between strategies — merging
      leaks foreign keys into the new strategy's constructor).
    """
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and v.get(_REPLACE_SENTINEL) is True:
            out[k] = _strip_sentinel(v)
        elif k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _strip_sentinel(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _strip_sentinel(v) for k, v in d.items() if k != _REPLACE_SENTINEL}
    if isinstance(d, list):
        return [_strip_sentinel(x) for x in d]
    return d


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML file, resolving a single level of `extends:`.

    `extends:` is interpreted relative to the file that contains it.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    parent_name = cfg.pop("extends", None)
    if parent_name:
        parent_path = path.parent / parent_name
        parent_cfg = load_config(parent_path)
        cfg = _deep_merge(parent_cfg, cfg)

    return cfg
