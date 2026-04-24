"""YAML config loader with simple `extends:` support.

Keeps config loading dependency-light (no pydantic) because the whole
spec fits in a flat dict and the schema is small enough to validate
inline at the call sites that need it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


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
