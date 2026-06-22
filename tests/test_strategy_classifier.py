"""Tests for the strategy classifier (services/strategy_service/strategy_classifier.py).

Pure static classification — no repo, no third-party code.
"""
import pytest

from services.strategy_service.strategy_classifier import (
    ALLOWED_ASSET_CLASSES,
    KNOWLEDGE_BASE,
    Classification,
    MT5Applicability,
    classify,
    known_strategy_names,
    normalize_name,
)

EXPECTED_APPLICABILITY = {
    "MACD Oscillator": MT5Applicability.DIRECT,
    "Heikin-Ashi": MT5Applicability.DIRECT,
    "London Breakout": MT5Applicability.DIRECT,
    "Awesome Oscillator": MT5Applicability.DIRECT,
    "Dual Thrust": MT5Applicability.DIRECT,
    "Parabolic SAR": MT5Applicability.DIRECT,
    "Bollinger Bands Pattern Recognition": MT5Applicability.DIRECT,
    "RSI Pattern Recognition": MT5Applicability.DIRECT,
    "Shooting Star": MT5Applicability.DIRECT,
    "Pair Trading": MT5Applicability.ADAPTABLE,
    "Options Straddle": MT5Applicability.NOT_APPLICABLE,
    "VIX Calculator": MT5Applicability.RESEARCH_ONLY,
    "Monte Carlo Project": MT5Applicability.RESEARCH_ONLY,
    "Oil Money Project": MT5Applicability.RESEARCH_ONLY,
    "Smart Farmers Project": MT5Applicability.RESEARCH_ONLY,
    "Portfolio Optimization Project": MT5Applicability.RESEARCH_ONLY,
    "Wisdom of Crowd Project": MT5Applicability.RESEARCH_ONLY,
}


@pytest.mark.parametrize("name,expected", list(EXPECTED_APPLICABILITY.items()))
def test_known_applicability(name, expected):
    assert classify(name).mt5_applicability == expected


def test_normalization_variants_match():
    base = classify("MACD Oscillator")
    for variant in ["macd oscillator", "MACD  Oscillator", "MACD_Oscillator", "macd-oscillator"]:
        assert classify(variant) == base


def test_heikin_ashi_punctuation_variants():
    assert classify("Heikin-Ashi") == classify("heikin ashi") == classify("HEIKIN_ASHI")


def test_unknown_defaults_to_research_only():
    c = classify("Totally Unknown Strategy 9000")
    assert c.mt5_applicability == MT5Applicability.RESEARCH_ONLY
    assert c.category == "unknown"
    assert "manual review" in c.reason_for_applicability.lower()


def test_options_straddle_not_applicable_reason_mentions_options():
    c = classify("Options Straddle")
    assert c.mt5_applicability == MT5Applicability.NOT_APPLICABLE
    assert "options" in c.reason_for_applicability.lower()


def test_all_kb_entries_valid():
    for key, c in KNOWLEDGE_BASE.items():
        assert isinstance(c, Classification)
        assert c.category and c.description and c.reason_for_applicability
        assert isinstance(c.mt5_applicability, MT5Applicability)
        # asset classes must come from the allowed vocabulary
        assert set(c.supported_asset_classes).issubset(ALLOWED_ASSET_CLASSES)
        # normalisation must be idempotent on the keys
        assert normalize_name(key) == key


def test_known_names_cover_task_list():
    names = set(known_strategy_names())
    for required in EXPECTED_APPLICABILITY:
        assert required in names
    assert "Ore Money Project" in names  # present in KB even if "if present"


def test_executable_strategies_only_direct_or_adaptable():
    # Anything we might execute must be OHLC-on-our-instruments feasible.
    for name, c in [(n, classify(n)) for n in known_strategy_names()]:
        if c.mt5_applicability in (MT5Applicability.DIRECT, MT5Applicability.ADAPTABLE):
            assert {"forex", "metal", "crypto"} & set(c.supported_asset_classes), name
