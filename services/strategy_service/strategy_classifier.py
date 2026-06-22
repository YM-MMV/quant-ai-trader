"""Static classification of quant-trading strategies for MT5 suitability.

Given a strategy/project *name* (e.g. a folder name from
``je-suis-tm/quant-trading``), return how applicable it is to this project's
MT5 forex/gold/crypto execution model, plus the data it needs and which asset
classes it fits.

This is a **static, name-based knowledge table** — it never imports or runs any
third-party code. Names are normalised (case/punctuation-insensitive) before
lookup. Unknown items default to ``research_only`` (the safe choice: analyse,
never auto-execute) and are flagged for manual review.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MT5Applicability(str, Enum):
    """How suitable a strategy is for MT5 forex/gold/crypto execution."""

    DIRECT = "direct"            # works as-is on OHLC of our instruments
    ADAPTABLE = "adaptable"      # needs an adapter / extra inputs, but feasible
    RESEARCH_ONLY = "research_only"  # useful for analysis, not for execution
    NOT_APPLICABLE = "not_applicable"  # cannot run on our instruments at all


class PortingStatus(str, Enum):
    """Lifecycle of porting a strategy into an internal adapter."""

    NOT_STARTED = "not_started"
    ADAPTER_CREATED = "adapter_created"
    TESTED = "tested"
    APPROVED = "approved"
    REJECTED = "rejected"


# Asset classes we recognise (superset of what we actually trade).
ALLOWED_ASSET_CLASSES = frozenset(
    {"forex", "metal", "crypto", "equity", "commodity", "index", "options"}
)


class Classification(BaseModel):
    """Static classification of a single strategy/project."""

    model_config = ConfigDict(extra="forbid")

    category: str
    description: str
    required_data: list[str] = Field(default_factory=list)
    supported_asset_classes: list[str] = Field(default_factory=list)
    mt5_applicability: MT5Applicability
    reason_for_applicability: str


def normalize_name(name: str) -> str:
    """Normalise a strategy name to a lookup key: lower-case, alnum-separated.

    ``"Heikin-Ashi"`` / ``"heikin_ashi"`` / ``"Heikin  Ashi"`` -> ``"heikin ashi"``.
    """
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _c(
    category: str,
    description: str,
    required_data: list[str],
    asset_classes: list[str],
    applicability: MT5Applicability,
    reason: str,
) -> Classification:
    return Classification(
        category=category,
        description=description,
        required_data=required_data,
        supported_asset_classes=asset_classes,
        mt5_applicability=applicability,
        reason_for_applicability=reason,
    )


_OHLC = ["OHLC candles"]
_FXMC = ["forex", "metal", "crypto"]

# Knowledge base keyed by normalised name. Authored from the public layout of
# je-suis-tm/quant-trading; purely descriptive, no third-party code involved.
KNOWLEDGE_BASE: dict[str, Classification] = {
    # --- Technical indicators / patterns -------------------------------- #
    normalize_name("MACD Oscillator"): _c(
        "technical_indicator",
        "Moving Average Convergence Divergence trend/momentum oscillator.",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Pure OHLC indicator; computable and tradable directly on FX/metal/crypto.",
    ),
    normalize_name("Pair Trading"): _c(
        "statistical_arbitrage",
        "Mean-reversion on the spread of two cointegrated instruments.",
        ["two correlated price series (OHLC)"], ["forex", "crypto"],
        MT5Applicability.ADAPTABLE,
        "Needs a cointegrated pair and spread/position bookkeeping; feasible on "
        "correlated FX/crypto pairs via an adapter.",
    ),
    normalize_name("Heikin-Ashi"): _c(
        "candlestick_pattern",
        "Heikin-Ashi smoothed candles for trend clarity.",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Deterministic transform of OHLC; applies directly to our instruments.",
    ),
    normalize_name("London Breakout"): _c(
        "breakout",
        "Trades the breakout of the pre-London session range.",
        _OHLC, ["forex", "metal"], MT5Applicability.DIRECT,
        "Intraday FX session strategy; a natural fit for MT5 forex/gold.",
    ),
    normalize_name("Awesome Oscillator"): _c(
        "technical_indicator",
        "Bill Williams Awesome Oscillator (median-price momentum).",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Pure OHLC indicator; directly applicable.",
    ),
    normalize_name("Dual Thrust"): _c(
        "breakout",
        "Range-based intraday breakout using prior highs/lows.",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "OHLC-only breakout logic; runs directly on our instruments.",
    ),
    normalize_name("Parabolic SAR"): _c(
        "technical_indicator",
        "Parabolic Stop-and-Reverse trailing indicator.",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Pure OHLC indicator; directly applicable (also useful for trailing SL).",
    ),
    normalize_name("Bollinger Bands Pattern Recognition"): _c(
        "technical_indicator",
        "Pattern recognition on Bollinger Bands (squeeze/breakout shapes).",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Built from OHLC + Bollinger Bands; directly applicable.",
    ),
    normalize_name("RSI Pattern Recognition"): _c(
        "technical_indicator",
        "Pattern recognition on the RSI oscillator.",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Built from OHLC + RSI; directly applicable.",
    ),
    normalize_name("Shooting Star"): _c(
        "candlestick_pattern",
        "Shooting Star (and related) candlestick reversal pattern detection.",
        _OHLC, _FXMC, MT5Applicability.DIRECT,
        "Candlestick pattern on OHLC; directly applicable.",
    ),
    # --- Options / volatility ------------------------------------------- #
    normalize_name("Options Straddle"): _c(
        "options",
        "Long/short straddle options strategy around volatility events.",
        ["options chain", "implied volatility"], ["options"],
        MT5Applicability.NOT_APPLICABLE,
        "Requires an options chain and greeks; not available on our spot MT5 "
        "forex/gold/crypto instruments.",
    ),
    normalize_name("VIX Calculator"): _c(
        "options",
        "Computes a VIX-style implied-volatility index from option prices.",
        ["options chain / implied volatility"], ["index", "options"],
        MT5Applicability.RESEARCH_ONLY,
        "An analytics calculator, not a tradable signal; needs options data we "
        "do not have. Useful as a research/volatility input only.",
    ),
    # --- Quantamental / projects ---------------------------------------- #
    normalize_name("Monte Carlo Project"): _c(
        "simulation",
        "Monte Carlo simulation of price paths / strategy outcomes.",
        ["historical price series"], _FXMC, MT5Applicability.RESEARCH_ONLY,
        "A simulation/analysis method rather than an execution strategy; informs "
        "risk research, not order generation.",
    ),
    normalize_name("Oil Money Project"): _c(
        "quantamental",
        "Studies petrocurrency / oil-FX relationships (e.g. CAD, NOK).",
        ["macro/fundamental series", "FX rates"], ["forex", "commodity"],
        MT5Applicability.RESEARCH_ONLY,
        "Macro/fundamental study; can inform FX bias but is not a directly "
        "executable signal.",
    ),
    normalize_name("Ore Money Project"): _c(
        "quantamental",
        "Studies commodity-currency (iron ore / AUD) relationships.",
        ["macro/fundamental series", "FX rates"], ["forex", "commodity"],
        MT5Applicability.RESEARCH_ONLY,
        "Macro/fundamental study; informs FX bias, not directly executable.",
    ),
    normalize_name("Smart Farmers Project"): _c(
        "quantamental",
        "Agricultural-commodity fundamentals study.",
        ["agricultural commodity prices", "fundamental data"], ["commodity"],
        MT5Applicability.RESEARCH_ONLY,
        "Agricultural-commodity research outside our forex/gold/crypto scope.",
    ),
    normalize_name("Portfolio Optimization Project"): _c(
        "portfolio",
        "Mean-variance / efficient-frontier portfolio allocation.",
        ["multi-asset return series"], ["equity", "forex", "crypto"],
        MT5Applicability.RESEARCH_ONLY,
        "Produces allocations, not entry signals; could later inform position "
        "sizing across allowlisted symbols via an adapter.",
    ),
    normalize_name("Wisdom of Crowd Project"): _c(
        "quantamental",
        "Sentiment / crowd-data driven study.",
        ["sentiment/forum data", "price series"], ["equity"],
        MT5Applicability.RESEARCH_ONLY,
        "Depends on external sentiment data; analysis input rather than an "
        "executable MT5 strategy.",
    ),
}


def classify(name: str) -> Classification:
    """Classify a strategy/project by name. Unknown -> safe ``research_only``."""
    key = normalize_name(name)
    if key in KNOWLEDGE_BASE:
        return KNOWLEDGE_BASE[key]
    return _c(
        "unknown",
        f"Unrecognized strategy/project {name!r}; needs manual review.",
        [], [], MT5Applicability.RESEARCH_ONLY,
        "Not in the known classification table; defaulted to research_only "
        "pending manual review (never auto-executed).",
    )


def known_strategy_names() -> list[str]:
    """Canonical display names of all classified strategies (for inventory)."""
    return list(_DISPLAY_NAMES)


# Canonical display names, in a stable presentation order.
_DISPLAY_NAMES: tuple[str, ...] = (
    "MACD Oscillator",
    "Pair Trading",
    "Heikin-Ashi",
    "London Breakout",
    "Awesome Oscillator",
    "Dual Thrust",
    "Parabolic SAR",
    "Bollinger Bands Pattern Recognition",
    "RSI Pattern Recognition",
    "Shooting Star",
    "Options Straddle",
    "VIX Calculator",
    "Monte Carlo Project",
    "Oil Money Project",
    "Ore Money Project",
    "Smart Farmers Project",
    "Portfolio Optimization Project",
    "Wisdom of Crowd Project",
)


__all__ = [
    "MT5Applicability",
    "PortingStatus",
    "Classification",
    "ALLOWED_ASSET_CLASSES",
    "KNOWLEDGE_BASE",
    "normalize_name",
    "classify",
    "known_strategy_names",
]
