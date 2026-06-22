"""Registry of strategy adapters.

A :class:`StrategyRegistry` keeps a name → adapter mapping so the rest of the
system can discover and look up adapters without importing each one directly.
Adapters are registered by their metadata ``name`` (which must be unique).

A process-wide :data:`default_registry` plus a :func:`register_adapter` class
decorator make it easy to self-register adapters on import; tests use their own
isolated :class:`StrategyRegistry` instances to avoid global state leakage.
"""
from __future__ import annotations

from typing import Optional

from services.strategy_service.base import StrategyAdapter


class StrategyRegistry:
    """An isolated collection of strategy adapters keyed by name."""

    def __init__(self) -> None:
        self._adapters: dict[str, StrategyAdapter] = {}

    # -- registration ------------------------------------------------------ #
    def register(self, adapter: StrategyAdapter, *, replace: bool = False) -> StrategyAdapter:
        """Register an adapter instance. Raises on duplicate unless ``replace``."""
        if not isinstance(adapter, StrategyAdapter):
            raise TypeError(
                f"can only register StrategyAdapter instances, got {type(adapter).__name__}"
            )
        key = adapter.name
        if not key:
            raise ValueError("adapter metadata name must be non-empty")
        if key in self._adapters and not replace:
            raise KeyError(f"adapter {key!r} is already registered")
        self._adapters[key] = adapter
        return adapter

    def register_class(
        self, cls: type[StrategyAdapter], *args, replace: bool = False, **kwargs
    ) -> StrategyAdapter:
        """Instantiate ``cls`` and register the instance."""
        if not (isinstance(cls, type) and issubclass(cls, StrategyAdapter)):
            raise TypeError("register_class expects a StrategyAdapter subclass")
        instance = cls(*args, **kwargs)
        return self.register(instance, replace=replace)

    def unregister(self, name: str) -> None:
        if name not in self._adapters:
            raise KeyError(f"adapter {name!r} is not registered")
        del self._adapters[name]

    def clear(self) -> None:
        self._adapters.clear()

    # -- lookup ------------------------------------------------------------ #
    def get(self, name: str) -> StrategyAdapter:
        if name not in self._adapters:
            raise KeyError(f"adapter {name!r} is not registered; known: {self.names()}")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters)

    def adapters(self) -> list[StrategyAdapter]:
        return [self._adapters[name] for name in self.names()]

    def find(
        self, symbol: Optional[str] = None, timeframe: Optional[str] = None
    ) -> list[StrategyAdapter]:
        """Return adapters supporting the given symbol and/or timeframe."""
        result = []
        for adapter in self.adapters():
            if symbol is not None and not adapter.supports_symbol(symbol):
                continue
            if timeframe is not None and not adapter.supports_timeframe(timeframe):
                continue
            result.append(adapter)
        return result

    # -- dunder ------------------------------------------------------------ #
    def __contains__(self, name: object) -> bool:
        return name in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)

    def __iter__(self):
        return iter(self.adapters())


# Process-wide default registry for self-registering adapters.
default_registry = StrategyRegistry()


def register_adapter(
    registry: StrategyRegistry = default_registry, *, replace: bool = False
):
    """Class decorator that instantiates and registers an adapter.

    The decorated class must be constructible with no required arguments::

        @register_adapter()
        class MyAdapter(StrategyAdapter):
            ...
    """

    def decorator(cls: type[StrategyAdapter]) -> type[StrategyAdapter]:
        registry.register_class(cls, replace=replace)
        return cls

    return decorator


__all__ = ["StrategyRegistry", "default_registry", "register_adapter"]
