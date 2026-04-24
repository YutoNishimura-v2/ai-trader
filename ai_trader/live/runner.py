"""Real-time / demo runner.

Wires a Strategy + RiskManager + Broker together and polls for new
bars. MT5-specific bar fetching is imported lazily so this module
can be unit-tested on Linux with a stub fetcher.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

from ..broker.base import Broker, Order
from ..risk.manager import RiskManager
from ..strategy.base import BaseStrategy
from ..utils.logging import get_logger


BarFetcher = Callable[[int], pd.DataFrame]


@dataclass
class LiveRunner:
    strategy: BaseStrategy
    risk: RiskManager
    broker: Broker
    fetch_bars: BarFetcher
    history_bars: int = 500
    poll_seconds: int = 5
    max_iterations: Optional[int] = None

    def run(self) -> None:
        log = get_logger("ai_trader.live")
        log.info("live runner starting, strategy=%s", self.strategy.name)
        last_bar_time: Optional[pd.Timestamp] = None
        iteration = 0
        while True:
            iteration += 1
            if self.max_iterations is not None and iteration > self.max_iterations:
                log.info("max_iterations reached, stopping")
                return
            try:
                df = self.fetch_bars(self.history_bars)
            except Exception as exc:  # pragma: no cover
                log.exception("fetch_bars failed: %s", exc)
                time.sleep(self.poll_seconds)
                continue

            if df.empty:
                time.sleep(self.poll_seconds)
                continue

            latest = df.index[-1]
            if last_bar_time is not None and latest <= last_bar_time:
                time.sleep(self.poll_seconds)
                continue
            last_bar_time = latest

            now = datetime.now(timezone.utc)
            # Flatten on kill-switch first.
            ledger = self.risk._ensure_day(now)
            if ledger.kill_switch:
                for pos in list(self.broker.open_positions()):
                    closed = self.broker.close(
                        pos.id, price=float(df.iloc[-1]["close"]), now=now, reason="kill-switch"
                    )
                    self.risk.on_trade_closed(closed.pnl, when=now)
                time.sleep(self.poll_seconds)
                continue

            sig = self.strategy.on_bar(df)
            if sig is None:
                time.sleep(self.poll_seconds)
                continue

            ref = float(df.iloc[-1]["close"])
            decision = self.risk.evaluate(
                sig,
                ref_price=ref,
                open_positions=len(self.broker.open_positions()),
                now=now,
            )
            if not decision.approved:
                log.info("signal rejected: %s", decision.reason)
                time.sleep(self.poll_seconds)
                continue

            order = Order(
                side=sig.side,
                lots=decision.lots,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                comment=sig.reason,
            )
            res = self.broker.submit(order, ref_price=ref, now=now)
            if res.ok:
                log.info("opened %s %s lots=%s entry=%s sl=%s tp=%s",
                         sig.side.value, self.strategy.name, decision.lots,
                         res.position.entry_price if res.position else "?",
                         sig.stop_loss, sig.take_profit)
            else:
                log.warning("order submit failed: %s", res.error)

            time.sleep(self.poll_seconds)
