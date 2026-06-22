"""Per-symbol contract specifications for deterministic position sizing.

A :class:`SymbolSpec` captures everything lot sizing needs that is *not* market
data: the contract size (units per 1.0 lot), pip/point size, the broker's
min/max/step lot constraints, price digits, and the broker-specific symbol name.

This is a small static table for the instruments we trade (forex majors, gold,
a crypto example). It is deterministic and offline — no MT5, no network. Values
are conventional defaults; a real deployment would reconcile them against the
broker's ``symbol_info`` at startup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SymbolSpec:
    """Contract specification for one tradable symbol."""

    symbol: str               # canonical name
    broker_alias: str         # broker-specific name (mapping target)
    asset_class: str          # forex | metal | crypto
    pip_size: float           # price value of one pip
    point_size: float         # price value of one point (1/10 pip on 3/5-digit)
    contract_size: float      # base units per 1.0 lot
    min_lot: float
    max_lot: float
    lot_step: float
    digits: int

    def money_per_price_unit(self, lots: float) -> float:
        """Account-currency PnL for a 1.0 price move of ``lots`` lots."""
        return lots * self.contract_size


# Conventional specs (quote-currency = USD; account currency assumed USD).
_SPECS: dict[str, SymbolSpec] = {
    "EURUSD": SymbolSpec("EURUSD", "EURUSD", "forex", 0.0001, 0.00001, 100_000,
                         0.01, 100.0, 0.01, 5),
    "GBPUSD": SymbolSpec("GBPUSD", "GBPUSD", "forex", 0.0001, 0.00001, 100_000,
                         0.01, 100.0, 0.01, 5),
    "USDJPY": SymbolSpec("USDJPY", "USDJPY", "forex", 0.01, 0.001, 100_000,
                         0.01, 100.0, 0.01, 3),
    "AUDUSD": SymbolSpec("AUDUSD", "AUDUSD", "forex", 0.0001, 0.00001, 100_000,
                         0.01, 100.0, 0.01, 5),
    # Gold: 1.0 lot = 100 troy oz, quoted to 2 dp.
    "XAUUSD": SymbolSpec("XAUUSD", "GOLD", "metal", 0.01, 0.01, 100,
                         0.01, 50.0, 0.01, 2),
    # Crypto example: 1.0 lot = 1 coin.
    "BTCUSD": SymbolSpec("BTCUSD", "BTCUSD", "crypto", 0.01, 0.01, 1,
                         0.01, 10.0, 0.01, 2),
}

# Reverse map for broker → canonical lookups.
_BY_BROKER: dict[str, str] = {spec.broker_alias: sym for sym, spec in _SPECS.items()}


def get_symbol_spec(symbol: str) -> Optional[SymbolSpec]:
    """Return the spec for a canonical symbol, or ``None`` if unknown."""
    return _SPECS.get(symbol)


def broker_symbol(symbol: str) -> Optional[str]:
    """Broker-specific name for a canonical symbol (mapping)."""
    spec = _SPECS.get(symbol)
    return spec.broker_alias if spec else None


def canonical_symbol(broker_alias: str) -> Optional[str]:
    """Canonical symbol for a broker-specific name (reverse mapping)."""
    return _BY_BROKER.get(broker_alias)


def known_symbols() -> list[str]:
    return sorted(_SPECS)


__all__ = [
    "SymbolSpec",
    "get_symbol_spec",
    "broker_symbol",
    "canonical_symbol",
    "known_symbols",
]
