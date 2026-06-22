"""Concrete strategy adapters.

Each module here ports one quant-trading strategy into a
:class:`~services.strategy_service.base.StrategyAdapter`. Porting is gradual:
strategies start as empty/abstaining adapters (see :class:`NullAdapter`) and
gain real logic over later milestones, advancing their ``porting_status`` in the
inventory (see ``strategies/inventory/quant_trading_inventory.json``).

Adapters are NOT auto-registered on import — register them explicitly into a
``StrategyRegistry`` (or use the ``@register_adapter`` decorator) so importing
this package has no side effects.

To add a new adapter:

1. Subclass ``StrategyAdapter`` in a new module here.
2. Implement ``get_metadata()`` and ``_compute_signal()`` (deterministic; no MT5,
   no AI, no look-ahead). Use ``self.make_signal(...)`` / ``self.none_signal(...)``.
3. Register it where adapters are wired up.
"""
from services.strategy_service.adapters.null_adapter import NullAdapter

__all__ = ["NullAdapter"]
