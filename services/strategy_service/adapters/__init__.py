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
3. Register it where adapters are wired up — e.g. add it to
   ``TECHNICAL_INDICATOR_ADAPTERS`` and call :func:`register_technical_indicator_adapters`.
"""
from services.strategy_service.adapters.awesome_oscillator import AwesomeOscillatorAdapter
from services.strategy_service.adapters.bollinger_bands_pattern import (
    BollingerBandsPatternAdapter,
)
from services.strategy_service.adapters.dual_thrust import DualThrustAdapter
from services.strategy_service.adapters.heikin_ashi import HeikinAshiAdapter
from services.strategy_service.adapters.london_breakout import LondonBreakoutAdapter
from services.strategy_service.adapters.macd_oscillator import MACDOscillatorAdapter
from services.strategy_service.adapters.null_adapter import NullAdapter
from services.strategy_service.adapters.parabolic_sar import ParabolicSARAdapter
from services.strategy_service.adapters.rsi_pattern import RSIPatternAdapter
from services.strategy_service.adapters.shooting_star import ShootingStarAdapter

# The M8 technical-indicator / price-action strategies ported from
# je-suis-tm/quant-trading. Listed (not auto-registered) so importing this
# package stays side-effect free.
TECHNICAL_INDICATOR_ADAPTERS = (
    MACDOscillatorAdapter,
    HeikinAshiAdapter,
    LondonBreakoutAdapter,
    AwesomeOscillatorAdapter,
    DualThrustAdapter,
    ParabolicSARAdapter,
    BollingerBandsPatternAdapter,
    RSIPatternAdapter,
    ShootingStarAdapter,
)


def register_technical_indicator_adapters(registry, *, replace: bool = False):
    """Register every M8 adapter into ``registry`` (explicit, opt-in).

    Importing this package never registers anything; callers wire adapters up by
    calling this with the registry they want populated.
    """
    for cls in TECHNICAL_INDICATOR_ADAPTERS:
        registry.register_class(cls, replace=replace)
    return registry


__all__ = [
    "NullAdapter",
    "MACDOscillatorAdapter",
    "HeikinAshiAdapter",
    "LondonBreakoutAdapter",
    "AwesomeOscillatorAdapter",
    "DualThrustAdapter",
    "ParabolicSARAdapter",
    "BollingerBandsPatternAdapter",
    "RSIPatternAdapter",
    "ShootingStarAdapter",
    "TECHNICAL_INDICATOR_ADAPTERS",
    "register_technical_indicator_adapters",
]
