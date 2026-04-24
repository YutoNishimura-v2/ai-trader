"""Currency conversion for multi-currency accounts.

The trading account is in one currency (JPY on HFM Katana); each
instrument's P&L is in its quote currency (USD for XAUUSD, BTCUSD).
``FXConverter`` turns a (amount, quote_ccy, account_ccy) triple into
an account-currency amount.

For backtests we want deterministic behaviour, so a plain ``FixedFX``
with a config-provided USD/JPY rate is the default. Live demo can
swap in a live-rate implementation that polls MT5 (not shipped yet —
wire point is ``FXConverter``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


class FXConverter:
    """Abstract rate source. Subclasses override ``rate``."""

    def rate(self, from_ccy: str, to_ccy: str) -> float:
        raise NotImplementedError

    def convert(self, amount: float, from_ccy: str, to_ccy: str) -> float:
        if from_ccy == to_ccy:
            return amount
        return amount * self.rate(from_ccy, to_ccy)


@dataclass
class FixedFX(FXConverter):
    """Static rate table.

    Rates are stored as ``(from_ccy, to_ccy) -> rate``. Reverse rates
    are computed automatically: if ``USD -> JPY = 150`` is stored,
    ``JPY -> USD`` returns ``1 / 150``. Same-currency conversion is
    always 1.0.
    """

    rates: dict[tuple[str, str], float] = field(default_factory=dict)

    def rate(self, from_ccy: str, to_ccy: str) -> float:
        if from_ccy == to_ccy:
            return 1.0
        if (from_ccy, to_ccy) in self.rates:
            return self.rates[(from_ccy, to_ccy)]
        if (to_ccy, from_ccy) in self.rates:
            return 1.0 / self.rates[(to_ccy, from_ccy)]
        raise KeyError(f"no FX rate for {from_ccy} -> {to_ccy}")

    @classmethod
    def from_config(cls, d: dict) -> "FixedFX":
        """Accepts ``{'USDJPY': 150.0, 'USDBTC': ...}``-style shorthand."""
        rates: dict[tuple[str, str], float] = {}
        for k, v in d.items():
            k = k.upper()
            if len(k) != 6:
                raise ValueError(f"FX key must be 6 chars (e.g. USDJPY), got {k!r}")
            rates[(k[:3], k[3:])] = float(v)
        return cls(rates=rates)
