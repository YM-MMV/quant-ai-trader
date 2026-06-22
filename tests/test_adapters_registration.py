"""Cross-cutting tests for the M8 technical-indicator adapters.

Covers the package-level wiring (registration helper, metadata contract) and the
framework guarantees that must hold for *every* ported adapter: provenance,
asset compatibility, declared timeframes, and a fail-safe ``NONE`` on tiny data.
"""
import pytest

from services.strategy_service import adapters
from services.strategy_service.adapters import (
    TECHNICAL_INDICATOR_ADAPTERS,
    register_technical_indicator_adapters,
)
from services.strategy_service.base import AdapterSignal, SignalSide
from services.strategy_service.registry import StrategyRegistry, default_registry

REPO = "https://github.com/je-suis-tm/quant-trading"
EXPECTED_NAMES = {
    "macd_oscillator",
    "heikin_ashi",
    "london_breakout",
    "awesome_oscillator",
    "dual_thrust",
    "parabolic_sar",
    "bollinger_bands_pattern",
    "rsi_pattern",
    "shooting_star",
}


def test_nine_adapters_listed():
    assert len(TECHNICAL_INDICATOR_ADAPTERS) == 9


def test_register_all_into_isolated_registry():
    reg = StrategyRegistry()
    register_technical_indicator_adapters(reg)
    assert set(reg.names()) == EXPECTED_NAMES
    assert len(reg) == 9


def test_import_has_no_side_effects_on_default_registry():
    # Importing the package must not register anything globally.
    assert EXPECTED_NAMES.isdisjoint(set(default_registry.names()))


@pytest.mark.parametrize("cls", TECHNICAL_INDICATOR_ADAPTERS, ids=lambda c: c.__name__)
def test_metadata_contract(cls):
    meta = cls().get_metadata()
    assert meta.version == "1.0.0"
    assert meta.source_repo_url == REPO
    assert meta.asset_classes == ["forex", "metal", "crypto"]
    assert meta.supported_timeframes  # non-empty list of supported TFs
    assert meta.min_candles >= 1
    assert meta.source_strategy  # canonical name filled in


@pytest.mark.parametrize("cls", TECHNICAL_INDICATOR_ADAPTERS, ids=lambda c: c.__name__)
def test_fail_safe_none_on_tiny_data(cls, from_close):
    a = cls()
    sig = a.generate_signal(from_close([1.1, 1.1, 1.1]))
    assert isinstance(sig, AdapterSignal)
    assert sig.side is SignalSide.NONE  # never an actionable call on too little data


def test_adapters_are_subclasses():
    from services.strategy_service.base import StrategyAdapter

    for cls in TECHNICAL_INDICATOR_ADAPTERS:
        assert issubclass(cls, StrategyAdapter)
    # And the package exposes them all by name.
    for name in (
        "MACDOscillatorAdapter", "HeikinAshiAdapter", "LondonBreakoutAdapter",
        "AwesomeOscillatorAdapter", "DualThrustAdapter", "ParabolicSARAdapter",
        "BollingerBandsPatternAdapter", "RSIPatternAdapter", "ShootingStarAdapter",
    ):
        assert hasattr(adapters, name)
