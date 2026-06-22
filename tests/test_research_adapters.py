"""Cross-cutting tests for the M9 research adapters.

Asserts every remaining quant-trading project is represented and classified, the
full set of output types is used, and — the key safety property — that no
research output can be routed to execution.
"""
from datetime import datetime
from pathlib import Path

import pytest

from services.strategy_service.inventory_scanner import load_inventory
from services.strategy_service.research_adapters import (
    RESEARCH_ADAPTERS,
    build_research_adapters,
    register_research_adapters,
)
from services.strategy_service.research_adapters.base import (
    MT5Applicability,
    NotExecutableError,
    OutputType,
    ResearchOutput,
    ensure_not_executed,
)

REPO = "https://github.com/je-suis-tm/quant-trading"
INVENTORY = (
    Path(__file__).resolve().parents[1]
    / "strategies" / "inventory" / "quant_trading_inventory.json"
)
# Categories handled by M8 technical adapters, not by research adapters.
TECHNICAL_CATEGORIES = {"technical_indicator", "breakout", "candlestick_pattern"}

EXPECTED_NAMES = {
    "pair_trading", "options_straddle", "vix_calculator", "monte_carlo_project",
    "oil_money_project", "ore_money_project", "smart_farmers_project",
    "portfolio_optimization", "wisdom_of_crowd",
}


def test_nine_research_adapters_listed():
    assert len(RESEARCH_ADAPTERS) == 9
    assert set(build_research_adapters()) == EXPECTED_NAMES


@pytest.mark.parametrize("cls", RESEARCH_ADAPTERS, ids=lambda c: c.__name__)
def test_metadata_contract(cls):
    meta = cls().get_metadata()
    assert meta.version == "1.0.0"
    assert meta.source_repo_url == REPO
    assert meta.source_strategy
    assert meta.required_datasets  # must declare its data needs
    assert meta.reason  # why (not) tradable on MT5
    # None of the ported research projects are directly tradable.
    assert meta.applicability is not MT5Applicability.DIRECT


def test_all_output_types_are_used():
    used = {cls().get_metadata().output_type for cls in RESEARCH_ADAPTERS}
    assert used == set(OutputType)  # signal/feature/report/ranking/risk_context


@pytest.mark.parametrize("cls", RESEARCH_ADAPTERS, ids=lambda c: c.__name__)
def test_output_never_executable_and_rejected(cls):
    out = cls().run()  # no inputs → requirements report, but still a valid output
    assert isinstance(out, ResearchOutput)
    assert out.is_executable() is False
    with pytest.raises(NotExecutableError):
        ensure_not_executed(out)
    with pytest.raises(NotExecutableError):
        out.to_strategy_signal(
            symbol="EURUSD", timeframe="H1", timestamp=datetime(2024, 1, 1),
            strategy_id="x",
        )


def test_register_research_adapters_into_dict():
    reg: dict = {}
    register_research_adapters(reg)
    assert set(reg) == EXPECTED_NAMES
    with pytest.raises(KeyError):  # duplicate without replace
        register_research_adapters(reg)
    register_research_adapters(reg, replace=True)  # ok


# --------------------------------------------------------------------------- #
# Coverage: every non-technical inventory project is represented + classified
# --------------------------------------------------------------------------- #
def test_inventory_research_projects_are_represented():
    items = load_inventory(INVENTORY)
    research_items = [i for i in items if i.category not in TECHNICAL_CATEGORIES]
    adapters_by_strategy = {
        a.get_metadata().source_strategy: a for a in build_research_adapters().values()
    }
    for item in research_items:
        assert item.name in adapters_by_strategy, f"{item.name} has no research adapter"


def test_research_adapter_applicability_matches_inventory():
    items = {i.name: i for i in load_inventory(INVENTORY)}
    for adapter in build_research_adapters().values():
        meta = adapter.get_metadata()
        item = items.get(meta.source_strategy)
        if item is None:  # e.g. Ore Money Project is not in the baseline inventory
            continue
        assert meta.applicability.value == item.mt5_applicability
