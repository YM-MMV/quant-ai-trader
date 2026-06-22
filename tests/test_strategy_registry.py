"""Tests for the strategy registry (services/strategy_service/registry.py).

Uses isolated registries (never the process-wide default) to avoid global
state leaking between tests.
"""
from typing import Any, Optional

import pytest

from services.strategy_service.adapters import NullAdapter
from services.strategy_service.base import AdapterMetadata, SignalSide, StrategyAdapter
from services.strategy_service.registry import (
    StrategyRegistry,
    default_registry,
    register_adapter,
)


class _Dummy(StrategyAdapter):
    def __init__(self, name="dummy", symbols=None, timeframes=None):
        self._name = name
        self._symbols = symbols
        self._timeframes = timeframes

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name=self._name, version="1.0",
            supported_symbols=self._symbols, supported_timeframes=self._timeframes,
        )

    def _compute_signal(self, candles: Any, features: Any, kronos_prediction: Optional[Any]):
        return self.none_signal("dummy")


def test_register_and_get_empty_adapter():
    reg = StrategyRegistry()
    reg.register(NullAdapter())  # an EMPTY adapter can be registered
    assert "null" in reg
    assert len(reg) == 1
    assert reg.get("null").name == "null"
    assert reg.names() == ["null"]


def test_register_duplicate_raises_unless_replace():
    reg = StrategyRegistry()
    reg.register(_Dummy(name="a"))
    with pytest.raises(KeyError):
        reg.register(_Dummy(name="a"))
    # replace=True overwrites.
    other = _Dummy(name="a", symbols=["EURUSD"])
    reg.register(other, replace=True)
    assert reg.get("a") is other


def test_register_rejects_non_adapter():
    reg = StrategyRegistry()
    with pytest.raises(TypeError):
        reg.register(object())  # type: ignore[arg-type]


def test_register_class_instantiates():
    reg = StrategyRegistry()
    adapter = reg.register_class(NullAdapter)
    assert isinstance(adapter, NullAdapter)
    assert "null" in reg


def test_register_class_rejects_non_subclass():
    reg = StrategyRegistry()
    with pytest.raises(TypeError):
        reg.register_class(dict)  # type: ignore[arg-type]


def test_unregister_and_missing_get():
    reg = StrategyRegistry()
    reg.register(_Dummy(name="a"))
    reg.unregister("a")
    assert "a" not in reg
    with pytest.raises(KeyError):
        reg.get("a")
    with pytest.raises(KeyError):
        reg.unregister("a")


def test_find_filters_by_symbol_and_timeframe():
    reg = StrategyRegistry()
    reg.register(_Dummy(name="eur_only", symbols=["EURUSD"], timeframes=["M15"]))
    reg.register(_Dummy(name="any"))  # supports everything
    reg.register(_Dummy(name="gbp_only", symbols=["GBPUSD"]))

    eur_m15 = {a.name for a in reg.find(symbol="EURUSD", timeframe="M15")}
    assert eur_m15 == {"eur_only", "any"}

    gbp = {a.name for a in reg.find(symbol="GBPUSD")}
    assert gbp == {"gbp_only", "any"}

    assert {a.name for a in reg.find()} == {"eur_only", "any", "gbp_only"}


def test_iter_and_adapters_sorted():
    reg = StrategyRegistry()
    reg.register(_Dummy(name="b"))
    reg.register(_Dummy(name="a"))
    assert [a.name for a in reg] == ["a", "b"]
    assert [a.name for a in reg.adapters()] == ["a", "b"]


def test_clear():
    reg = StrategyRegistry()
    reg.register(_Dummy(name="a"))
    reg.clear()
    assert len(reg) == 0


def test_register_adapter_decorator():
    reg = StrategyRegistry()

    @register_adapter(reg)
    class DecoAdapter(_Dummy):
        def get_metadata(self) -> AdapterMetadata:
            return AdapterMetadata(name="deco", version="1.0")

    assert "deco" in reg


def test_default_registry_is_registry_instance():
    assert isinstance(default_registry, StrategyRegistry)
