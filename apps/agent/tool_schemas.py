"""Anthropic tool-call schemas for the agent tools (M29).

The tools themselves live in :mod:`apps.agent.tools` as plain Python callables.
This module gives each one a JSON-schema description in the shape the Anthropic
Messages API expects (``{"name", "description", "input_schema"}``) so an LLM can
*call* them, and adds a terminal ``submit_decision`` tool the runner uses to
collect a structured final decision.

Keeping the schemas next to a single ``TOOL_SCHEMAS`` map (mirroring
``apps.agent.tools.AGENT_TOOLS``) makes the model's exact capabilities explicit
and reviewable. No schema here exposes a live-execution tool — the runner only
dispatches names that exist in ``AGENT_TOOLS`` plus ``submit_decision``.
"""
from __future__ import annotations

from typing import Any

from apps.agent.prompts import TOOL_GUIDANCE

# Shared fragments ---------------------------------------------------------- #
_SYMBOL = {"type": "string", "description": "Canonical symbol, e.g. 'XAUUSD' or 'EURUSD'."}
_TIMEFRAME = {"type": "string", "description": "Timeframe, e.g. 'H1', 'M15', 'D1'.", "default": "H1"}
_SOURCE = {
    "type": "string",
    "enum": ["sample", "mt5"],
    "description": "Candle source. 'mt5' = real, recent terminal candles (use this "
                   "for live decisions); 'sample' = deterministic offline data.",
    "default": "sample",
}
_N = {"type": "integer", "description": "Number of recent candles.", "default": 200}


def _tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap an input schema with the tool name + its human-readable guidance."""
    return {
        "name": name,
        "description": TOOL_GUIDANCE.get(name, name),
        "input_schema": {"type": "object", **schema},
    }


# One entry per callable in apps.agent.tools.AGENT_TOOLS. ------------------- #
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_candles": _tool("get_candles", {
        "properties": {"symbol": _SYMBOL, "timeframe": _TIMEFRAME, "n": _N, "source": _SOURCE},
        "required": ["symbol"],
    }),
    "get_market_features": _tool("get_market_features", {
        "properties": {"symbol": _SYMBOL, "timeframe": _TIMEFRAME, "n": _N, "source": _SOURCE},
        "required": ["symbol"],
    }),
    "get_kronos_prediction": _tool("get_kronos_prediction", {
        "properties": {
            "symbol": _SYMBOL, "timeframe": _TIMEFRAME, "n": _N, "source": _SOURCE,
            "mode": {"type": "string", "description": "Predictor mode.", "default": "mock"},
        },
        "required": ["symbol"],
    }),
    "list_strategy_inventory": _tool("list_strategy_inventory", {"properties": {}}),
    "list_strategy_adapters": _tool("list_strategy_adapters", {"properties": {}}),
    "run_backtest": _tool("run_backtest", {
        "properties": {
            "strategy": {"type": "string", "description": "Adapter name from list_strategy_adapters."},
            "symbol": _SYMBOL, "timeframe": _TIMEFRAME,
            "n": {"type": "integer", "description": "Number of candles.", "default": 400},
            "source": _SOURCE,
        },
        "required": ["strategy", "symbol"],
    }),
    "score_backtest": _tool("score_backtest", {
        "properties": {
            "metrics": {
                "type": "object",
                "description": "A run_backtest result (or its 'metrics' dict) to score 0-1.",
            },
        },
        "required": ["metrics"],
    }),
    "validate_strategy": _tool("validate_strategy", {
        "properties": {
            "strategy": {"type": "string", "description": "Adapter name to validate."},
            "symbol": _SYMBOL, "timeframe": _TIMEFRAME,
            "n": {"type": "integer", "description": "Number of candles.", "default": 600},
            "source": _SOURCE,
        },
        "required": ["strategy", "symbol"],
    }),
    "propose_order_intent": _tool("propose_order_intent", {
        "properties": {
            "symbol": _SYMBOL,
            "side": {"type": "string", "enum": ["buy", "sell"]},
            "volume": {"type": "number", "description": "Lot size (e.g. 0.10)."},
            "stop_loss": {"type": "number", "description": "Absolute stop-loss price."},
            "take_profit": {"type": "number", "description": "Absolute take-profit price."},
            "strategy_id": {"type": "string", "description": "Adapter this is based on."},
            "comment": {"type": "string"},
        },
        "required": ["symbol", "side", "volume", "stop_loss", "take_profit"],
    }),
    "risk_check_order_intent": _tool("risk_check_order_intent", {
        "properties": {
            "intent": {"type": "object", "description": "A propose_order_intent result."},
            "reference_price": {"type": "number"},
            "account_balance": {"type": "number"},
            "spread_points": {"type": "number", "default": 0.0},
        },
        "required": ["intent"],
    }),
    "create_paper_trade": _tool("create_paper_trade", {
        "properties": {
            "intent": {"type": "object", "description": "A propose_order_intent result."},
            "reference_price": {"type": "number"},
            "account_balance": {"type": "number"},
        },
        "required": ["intent"],
    }),
}

# Terminal tool: the model calls this once to deliver its final decision. ---- #
SUBMIT_DECISION_SCHEMA: dict[str, Any] = {
    "name": "submit_decision",
    "description": (
        "Submit your FINAL trading decision for this bar. Call this exactly once, "
        "after you have researched the data, backtested/validated a strategy and "
        "(optionally) risk-checked a proposal. Use action='hold' to abstain — "
        "abstaining is a correct, common outcome."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "close", "hold"],
                "description": "open = enter a new position; close = exit the open "
                               "position; hold = do nothing this bar.",
            },
            "side": {
                "type": "string",
                "enum": ["buy", "sell"],
                "description": "Required when action='open'.",
            },
            "strategy": {
                "type": "string",
                "description": "Adapter name your decision is based on (for provenance "
                               "and the validation gate).",
            },
            "stop_loss": {"type": "number", "description": "Absolute stop-loss price (action='open')."},
            "take_profit": {"type": "number", "description": "Absolute take-profit price (action='open')."},
            "confidence": {"type": "number", "description": "0.0-1.0 confidence in this decision."},
            "rationale": {"type": "string", "description": "One or two sentences explaining the decision."},
        },
        "required": ["action", "rationale"],
    },
}

# The name the runner intercepts to end the loop.
SUBMIT_DECISION_TOOL = "submit_decision"


def decision_tools() -> list[dict[str, Any]]:
    """Tool list for a trading-decision run: research/backtest/validate/propose/
    risk-check tools + ``submit_decision``.

    ``create_paper_trade`` is intentionally excluded — the always-on loop owns
    execution (and routes paper *or* MT5-demo); the AI only researches, proposes
    and submits a decision. This keeps a single execution path and accounting.
    """
    names = [n for n in TOOL_SCHEMAS if n != "create_paper_trade"]
    return [TOOL_SCHEMAS[n] for n in names] + [SUBMIT_DECISION_SCHEMA]


def all_tools() -> list[dict[str, Any]]:
    """Every tool schema (all 11 callables + submit_decision)."""
    return list(TOOL_SCHEMAS.values()) + [SUBMIT_DECISION_SCHEMA]


__all__ = [
    "TOOL_SCHEMAS",
    "SUBMIT_DECISION_SCHEMA",
    "SUBMIT_DECISION_TOOL",
    "decision_tools",
    "all_tools",
]
