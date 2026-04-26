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
from ..strategy.base import BaseStrategy, ClosedTradeContext
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
                    self._fire_close_callback(closed, pnl_account=closed.pnl, now=now)
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

            # Enrich the order's meta with entry_risk_price so the
            # close callback can compute R-multiples in the same way
            # as the BacktestEngine. This keeps sim/live equivalence.
            order_meta = dict(sig.meta) if sig.meta else {}
            order_meta.setdefault("entry_risk_price", abs(ref - sig.stop_loss))
            order_meta.setdefault("strategy_reason", sig.reason)
            mn = _member_name_from_reason(sig.reason)
            if mn is not None:
                order_meta.setdefault("member_name", mn)
            order = Order(
                side=sig.side,
                lots=decision.lots,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                comment=sig.reason,
                meta=order_meta,
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

    # ------------------------------------------------------------------
    def _fire_close_callback(
        self, closed, *, pnl_account: float, now: datetime
    ) -> None:
        """Build and dispatch a :class:`ClosedTradeContext`.

        Mirrors :meth:`BacktestEngine._book_close` so the same hook
        fires identically in simulation and live.
        """
        meta = dict(closed.position.meta) if closed.position.meta else None
        member_name = (meta or {}).get("member_name") or _member_name_from_reason(
            closed.position.comment
        )
        entry_risk_price = (meta or {}).get("entry_risk_price")
        instrument = self.risk.instrument
        if (
            instrument.quote_currency != self.risk.account_currency
            and self.risk.fx is not None
        ):
            fx_factor = self.risk.fx.convert(
                1.0, instrument.quote_currency, self.risk.account_currency
            )
        else:
            fx_factor = 1.0
        r_mult: float | None
        if entry_risk_price and float(entry_risk_price) > 0 and closed.position.lots > 0:
            risk_account = (
                float(closed.position.lots)
                * float(instrument.contract_size)
                * float(entry_risk_price)
                * float(fx_factor)
            )
            r_mult = float(pnl_account) / risk_account if risk_account > 0 else None
        else:
            r_mult = None
        ctx = ClosedTradeContext(
            member_name=member_name,
            pnl=float(pnl_account),
            r_multiple=r_mult,
            entry_time=closed.position.open_time,
            close_time=closed.close_time,
            reason=closed.reason,
            comment=closed.position.comment,
            meta=meta,
        )
        try:
            self.strategy.on_trade_closed(ctx)
        except Exception:
            get_logger("ai_trader.live").exception(
                "strategy.on_trade_closed raised"
            )


def _member_name_from_reason(reason: str) -> str | None:
    if not reason or not reason.startswith("["):
        return None
    end = reason.find("]")
    if end < 0:
        return None
    inside = reason[1:end]
    if "|" in inside:
        inside = inside.split("|", 1)[0]
    return inside.strip() or None
