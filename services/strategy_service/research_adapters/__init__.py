"""Research adapters for quant-trading projects that are not directly tradable.

These wrap pair/stat-arb, options, VIX, Monte-Carlo, portfolio-optimisation and
the various quantamental projects so they are *represented and classified* in the
system rather than ignored. Every output declares its
:class:`~services.strategy_service.research_adapters.base.OutputType`, its
:class:`~services.strategy_service.strategy_classifier.MT5Applicability`, the
datasets it needs, and why it is (or is not) tradable on MT5 — and only a
``DIRECT`` signal could ever be executed, which none of these are.

Importing this package has no side effects; wire adapters up explicitly via
:func:`register_research_adapters`.
"""
from services.strategy_service.research_adapters.base import (
    NotExecutableError,
    OutputType,
    ResearchAdapter,
    ResearchAdapterMetadata,
    ResearchOutput,
    ensure_not_executed,
)
from services.strategy_service.research_adapters.monte_carlo_project import (
    MonteCarloProjectAdapter,
)
from services.strategy_service.research_adapters.oil_money_project import (
    OilMoneyProjectAdapter,
)
from services.strategy_service.research_adapters.options_straddle import (
    OptionsStraddleAdapter,
)
from services.strategy_service.research_adapters.ore_money_project import (
    OreMoneyProjectAdapter,
)
from services.strategy_service.research_adapters.pair_trading import PairTradingAdapter
from services.strategy_service.research_adapters.portfolio_optimization import (
    PortfolioOptimizationAdapter,
)
from services.strategy_service.research_adapters.smart_farmers_project import (
    SmartFarmersProjectAdapter,
)
from services.strategy_service.research_adapters.vix_calculator import VIXCalculatorAdapter
from services.strategy_service.research_adapters.wisdom_of_crowd import (
    WisdomOfCrowdAdapter,
)

# Every research project ported in M9 (import-order independent).
RESEARCH_ADAPTERS = (
    PairTradingAdapter,
    OptionsStraddleAdapter,
    VIXCalculatorAdapter,
    MonteCarloProjectAdapter,
    OilMoneyProjectAdapter,
    OreMoneyProjectAdapter,
    SmartFarmersProjectAdapter,
    PortfolioOptimizationAdapter,
    WisdomOfCrowdAdapter,
)


def build_research_adapters() -> dict[str, ResearchAdapter]:
    """Instantiate every research adapter, keyed by its metadata name."""
    instances = [cls() for cls in RESEARCH_ADAPTERS]
    return {a.name: a for a in instances}


def register_research_adapters(registry: dict, *, replace: bool = False) -> dict:
    """Populate a ``name -> adapter`` dict registry with all research adapters."""
    for name, adapter in build_research_adapters().items():
        if name in registry and not replace:
            raise KeyError(f"research adapter {name!r} already registered")
        registry[name] = adapter
    return registry


__all__ = [
    "ResearchAdapter",
    "ResearchAdapterMetadata",
    "ResearchOutput",
    "OutputType",
    "NotExecutableError",
    "ensure_not_executed",
    "PairTradingAdapter",
    "OptionsStraddleAdapter",
    "VIXCalculatorAdapter",
    "MonteCarloProjectAdapter",
    "OilMoneyProjectAdapter",
    "OreMoneyProjectAdapter",
    "SmartFarmersProjectAdapter",
    "PortfolioOptimizationAdapter",
    "WisdomOfCrowdAdapter",
    "RESEARCH_ADAPTERS",
    "build_research_adapters",
    "register_research_adapters",
]
