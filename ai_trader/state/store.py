"""Bot state persistence.

Plan v3 §A.8 requires that the live runner survives crashes:

- Open positions are reconciled from the broker on boot, not
  re-submitted.
- The *daily kill-switch* state must survive a restart so the bot
  can't accidentally keep trading after a mid-day target/loss hit.
- The *review-pause* state must survive so a pause set during
  trading is respected after a restart.
- Consecutive-SL counters must survive so the 2-SL trigger doesn't
  reset on restart.

This module is the single disk-backed source of truth for those
things. Everything is JSON; atomic writes via tmp+rename.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class BotState:
    """Everything that must survive a restart.

    - ``day`` and ``day_realized_pnl`` let us rebuild the day ledger.
    - ``kill_switch`` and ``kill_reason`` enforce §A.3 across
      restarts.
    - ``review_paused`` enforces §A.10 across restarts. When True
      the live runner must not open new trades.
    - ``consecutive_sl`` counts toward the 2-SL trigger.
    - ``withdrawn_total`` tracks the half-profit sweep ledger.
    """

    day: str | None = None            # ISO date string (UTC)
    day_starting_equity: float = 0.0
    day_realized_pnl: float = 0.0
    kill_switch: bool = False
    kill_reason: str = ""
    review_paused: bool = False
    review_reason: str = ""
    consecutive_sl: int = 0
    withdrawn_total: float = 0.0
    last_seen_bar_time: str | None = None
    # Freeform metadata (strategy name, version, etc.)
    meta: dict[str, Any] = field(default_factory=dict)

    def touch_day(self, now: datetime, starting_equity: float) -> bool:
        """Return True if this call rolled over to a new UTC day."""
        today = now.astimezone(timezone.utc).date().isoformat()
        if self.day != today:
            self.day = today
            self.day_starting_equity = float(starting_equity)
            self.day_realized_pnl = 0.0
            self.kill_switch = False
            self.kill_reason = ""
            return True
        return False


class StateStore:
    """JSON-on-disk persistence.

    Intentionally tiny: one file per bot instance. Atomic writes
    through a tmp file + os.replace so a crash mid-write leaves the
    previous good state.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        allowed = set(BotState.__dataclass_fields__.keys())
        clean = {k: v for k, v in raw.items() if k in allowed}
        return BotState(**clean)

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(state), indent=2, default=str)
        fd, tmp = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
