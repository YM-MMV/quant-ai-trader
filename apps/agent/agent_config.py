"""AI agent configuration & hard permission rules (M19).

This module encodes *what the agent is allowed to do* as immutable data, so the
rules live in one auditable place and cannot be quietly flipped at runtime.

The agent is **paper-only** (see SAFETY.md / AGENTS.md):

* it **may** research, propose order intents, and backtest;
* it **may** create paper trades — but *only* after a `RiskManager` approval;
* it **may not** execute live trades;
* it **may not** change the risk configuration;
* it **may not** approve its own risk bypasses.

:data:`DEFAULT_AGENT_CONFIG` is frozen, so attempting to grant a forbidden
capability (e.g. set ``can_execute_live = True``) raises instead of silently
escalating privilege. :func:`assert_paper_only` re-checks the invariants and is
called by the execution-adjacent tools as a defence in depth.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.models import TradingMode

# Tool names the agent must NEVER have. Tests assert none of these exist in the
# tool registry, and :func:`assert_no_live_tools` enforces it at runtime.
FORBIDDEN_TOOLS: frozenset[str] = frozenset({
    "execute_live_trade",
    "send_live_order",
    "place_live_order",
    "modify_risk_config",
    "set_risk_config",
    "approve_risk_bypass",
    "override_risk_decision",
})


class AgentPermissionError(RuntimeError):
    """Raised when the agent attempts something its permissions forbid."""


@dataclass(frozen=True)
class AgentPermissions:
    """The agent's capability flags. Frozen — escalation must be a code change."""

    can_research: bool = True
    can_propose: bool = True
    can_backtest: bool = True
    # Paper trades are allowed, but every call still re-runs the risk gate.
    can_create_paper_trades: bool = True
    # Hard-off capabilities (never True in a valid config).
    can_execute_live: bool = False
    can_modify_risk_config: bool = False
    can_approve_risk_bypass: bool = False


@dataclass(frozen=True)
class AgentConfig:
    """Top-level agent configuration. Paper-only by construction."""

    # The agent is locked to paper; it cannot select live or demo-live modes.
    trading_mode: TradingMode = TradingMode.PAPER
    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    # Conservative defaults for tool calls that need runtime context.
    default_account_balance: float = 10_000.0


DEFAULT_AGENT_CONFIG = AgentConfig()


def assert_paper_only(config: AgentConfig = DEFAULT_AGENT_CONFIG) -> None:
    """Raise :class:`AgentPermissionError` if any hard-off capability is set."""
    perms = config.permissions
    if config.trading_mode is not TradingMode.PAPER:
        raise AgentPermissionError(
            f"agent trading_mode must be paper, got {config.trading_mode.value!r}"
        )
    if perms.can_execute_live:
        raise AgentPermissionError("agent may not execute live trades")
    if perms.can_modify_risk_config:
        raise AgentPermissionError("agent may not modify the risk configuration")
    if perms.can_approve_risk_bypass:
        raise AgentPermissionError("agent may not approve its own risk bypasses")


def assert_no_live_tools(tool_names: "frozenset[str] | set[str] | list[str]") -> None:
    """Raise if any forbidden (live/bypass) tool name is exposed."""
    offending = sorted(set(tool_names) & FORBIDDEN_TOOLS)
    if offending:
        raise AgentPermissionError(f"forbidden tools must not exist: {offending}")


__all__ = [
    "AgentConfig",
    "AgentPermissions",
    "AgentPermissionError",
    "DEFAULT_AGENT_CONFIG",
    "FORBIDDEN_TOOLS",
    "assert_paper_only",
    "assert_no_live_tools",
]
