"""Risk service: the deterministic RiskManager + position sizing.

The only gate to execution. All risk logic is pure, deterministic code — no AI,
no MT5, no network — so the same intent + context + config always yields the
same `RiskDecision`.
"""
from services.risk_service.position_sizing import SizingResult, compute_lot_size
from services.risk_service.risk_manager import RiskContext, RiskManager
from services.risk_service.symbol_specs import (
    SymbolSpec,
    broker_symbol,
    canonical_symbol,
    get_symbol_spec,
    known_symbols,
)

__all__ = [
    "RiskManager",
    "RiskContext",
    "compute_lot_size",
    "SizingResult",
    "SymbolSpec",
    "get_symbol_spec",
    "broker_symbol",
    "canonical_symbol",
    "known_symbols",
]
