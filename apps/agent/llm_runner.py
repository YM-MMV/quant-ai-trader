"""LLM runner (M29) — the AI brain that drives the agent tools.

This is the piece that was missing: it connects a real language model to the
existing, paper-safe tools in :mod:`apps.agent.tools` via the Anthropic
Messages function-calling loop, and returns a single structured
:class:`AgentDecision` ("open/close/hold", with SL/TP and a rationale).

Design:

* **Provider-agnostic core.** :func:`run_decision` talks to an :class:`LLMClient`
  abstraction (provider-neutral :class:`LLMResponse` blocks), so the same loop
  works with the default :class:`AnthropicClient`, a user-supplied client behind
  the same interface, or the offline :class:`MockLLMClient` used by tests.
* **The model cannot execute.** The runner only dispatches tool names that exist
  in ``apps.agent.tools.AGENT_TOOLS`` (research/backtest/validate/propose/
  risk-check) plus the terminal ``submit_decision``. There is no live-execution
  tool here; the AI's decision is a *proposal* that the always-on loop routes
  through the deterministic RiskManager + gateway behind the hard locks.
* **Configurable model.** :func:`build_client` reads ``AI_MODEL`` / ``AI_API_KEY``
  / ``AI_BASE_URL`` from settings, defaulting to ``claude-opus-4-8``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.agent.prompts import SYSTEM_PROMPT
from apps.agent.tool_schemas import SUBMIT_DECISION_TOOL, decision_tools
from apps.agent.tools import get_tool

DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_ITERATIONS = 12


# --------------------------------------------------------------------------- #
# Provider-neutral response blocks
# --------------------------------------------------------------------------- #
@dataclass
class TextBlock:
    text: str

    def to_api(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class LLMResponse:
    """One model turn: a list of content blocks and why it stopped."""

    content: list[Any]
    stop_reason: str = "end_turn"

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def assistant_message(self) -> dict[str, Any]:
        return {"role": "assistant", "content": [b.to_api() for b in self.content]}


class LLMClient(Protocol):
    """Anything that can produce a model turn given system/messages/tools."""

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# The structured decision the runner extracts
# --------------------------------------------------------------------------- #
class AgentDecision(BaseModel):
    """The AI brain's final, structured move for one bar.

    A *proposal only* — the always-on loop re-runs the deterministic RiskManager
    and routes execution. ``action='hold'`` is a valid, common abstention.
    """

    model_config = ConfigDict(extra="ignore")

    action: str = "hold"  # open | close | hold
    side: Optional[str] = None  # buy | sell (required for action=open)
    strategy: Optional[str] = None
    stop_loss: Optional[float] = Field(None, gt=0)
    take_profit: Optional[float] = Field(None, gt=0)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("action")
    @classmethod
    def _norm_action(cls, v: str) -> str:
        v = (v or "hold").strip().lower()
        return v if v in ("open", "close", "hold") else "hold"

    @field_validator("side")
    @classmethod
    def _norm_side(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        return v if v in ("buy", "sell") else None

    @property
    def is_open(self) -> bool:
        return self.action == "open" and self.side in ("buy", "sell")

    @property
    def is_close(self) -> bool:
        return self.action == "close"

    @classmethod
    def hold(cls, rationale: str = "") -> "AgentDecision":
        return cls(action="hold", rationale=rationale)

    @classmethod
    def from_tool_input(cls, data: dict[str, Any]) -> "AgentDecision":
        """Build from a ``submit_decision`` payload, failing safe to hold.

        An ``open`` without a valid side (or SL/TP) is downgraded to a hold so a
        malformed proposal can never become an order.
        """
        try:
            decision = cls.model_validate(data)
        except Exception as exc:  # noqa: BLE001 — never crash on a bad payload
            return cls.hold(f"unparseable decision ({type(exc).__name__}); abstaining")
        if decision.action == "open" and not decision.is_open:
            return cls.hold(
                f"open proposed without a valid side; abstaining "
                f"({decision.rationale})".strip()
            )
        return decision


# --------------------------------------------------------------------------- #
# Run result + the loop
# --------------------------------------------------------------------------- #
@dataclass
class RunResult:
    decision: AgentDecision
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    iterations: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)


def _tool_result_block(tool_use_id: str, payload: Any) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(payload, default=str),
    }


def _parse_decision_text(text: str) -> Optional[AgentDecision]:
    """Best-effort: pull a JSON decision object out of free-form model text."""
    for match in re.finditer(r"\{[^{}]*\}", text, flags=re.DOTALL):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "action" in data:
            return AgentDecision.from_tool_input(data)
    return None


def run_decision(
    task: str,
    *,
    client: LLMClient,
    tools: Optional[list[dict[str, Any]]] = None,
    dispatch: Callable[[str], Callable[..., Any]] = get_tool,
    system: str = SYSTEM_PROMPT,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> RunResult:
    """Run the function-calling loop until the model submits a decision.

    The model researches via the tools, then calls ``submit_decision`` (or ends
    its turn with a JSON decision). Tool errors are returned to the model as
    ``tool_result`` payloads so it can recover rather than crashing the loop.
    """
    tools = tools if tools is not None else decision_tools()
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    tool_calls: list[tuple[str, dict[str, Any]]] = []
    decision: Optional[AgentDecision] = None

    iterations = 0
    for iterations in range(1, max_iterations + 1):
        response = client.respond(system=system, messages=messages, tools=tools)
        messages.append(response.assistant_message())
        uses = response.tool_uses()

        if not uses:
            decision = _parse_decision_text(response.text())
            break

        results: list[dict[str, Any]] = []
        submitted: Optional[AgentDecision] = None
        for use in uses:
            if use.name == SUBMIT_DECISION_TOOL:
                submitted = AgentDecision.from_tool_input(use.input)
                results.append(_tool_result_block(use.id, {"ok": True, "status": "decision recorded"}))
                if on_event:
                    on_event("decision", use.input)
                continue
            tool_calls.append((use.name, use.input))
            if on_event:
                on_event("tool_call", {"name": use.name, "input": use.input})
            try:
                payload = dispatch(use.name)(**use.input)
            except Exception as exc:  # noqa: BLE001 — feed the error back to the model
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            results.append(_tool_result_block(use.id, payload))

        messages.append({"role": "user", "content": results})
        if submitted is not None:
            decision = submitted
            break

    if decision is None:
        decision = AgentDecision.hold("no decision submitted within iteration budget")
    return RunResult(decision=decision, tool_calls=tool_calls, iterations=iterations, messages=messages)


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #
class AnthropicClient:
    """Default :class:`LLMClient` backed by the Anthropic Messages API.

    The ``anthropic`` package is an optional dependency (``pip install -e .[ai]``)
    and imported lazily, so the rest of the system — and the mock-driven tests —
    load without it.
    """

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-8",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        try:
            import anthropic  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on host install
            raise RuntimeError(
                "the 'anthropic' package is required for the live AI brain; "
                "install it with `pip install -e .[ai]` (or run the loop with "
                "--mock-ai for an offline demo)"
            ) from exc
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        content: list[Any] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                content.append(TextBlock(getattr(block, "text", "")))
            elif btype == "tool_use":
                raw_input = getattr(block, "input", {}) or {}
                content.append(ToolUseBlock(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    input=dict(raw_input),
                ))
        return LLMResponse(content=content, stop_reason=getattr(resp, "stop_reason", "end_turn"))


class MockLLMClient:
    """Offline :class:`LLMClient` that replays scripted turns (tests/demos).

    ``script`` is a list of :class:`LLMResponse` objects returned in order; once
    exhausted it submits a final ``hold`` so a run always terminates. A callable
    may be passed instead for state-dependent scripting:
    ``fn(system, messages, tools) -> LLMResponse``.
    """

    def __init__(
        self,
        script: Optional[list[LLMResponse]] = None,
        *,
        responder: Optional[Callable[..., LLMResponse]] = None,
    ) -> None:
        self._script = list(script or [])
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self._responder is not None:
            return self._responder(system=system, messages=messages, tools=tools)
        if self._script:
            return self._script.pop(0)
        # Exhausted: terminate cleanly with a hold so run_decision always ends.
        return LLMResponse(
            content=[ToolUseBlock(id="auto-hold", name=SUBMIT_DECISION_TOOL,
                                  input={"action": "hold", "rationale": "mock: script exhausted"})],
            stop_reason="tool_use",
        )


def build_client(settings: Any = None) -> LLMClient:
    """Construct the configured :class:`LLMClient` from settings/env.

    Defaults to Anthropic + ``claude-opus-4-8``; honours ``AI_MODEL`` /
    ``AI_API_KEY`` / ``AI_BASE_URL``. Pass a settings object to override, else
    the project ``Settings`` (env/.env) are loaded.
    """
    if settings is None:
        from services.config_loader import get_settings
        settings = get_settings()
    provider = (getattr(settings, "ai_provider", "anthropic") or "anthropic").lower()
    if provider != "anthropic":
        raise ValueError(
            f"unsupported AI_PROVIDER {provider!r}; only 'anthropic' is built in. "
            "Point AI_BASE_URL at an Anthropic-compatible endpoint, or add a client."
        )
    return AnthropicClient(
        model=getattr(settings, "ai_model", "claude-opus-4-8"),
        api_key=getattr(settings, "ai_api_key", None),
        base_url=getattr(settings, "ai_base_url", None),
    )


__all__ = [
    "AgentDecision",
    "RunResult",
    "run_decision",
    "LLMClient",
    "LLMResponse",
    "TextBlock",
    "ToolUseBlock",
    "AnthropicClient",
    "MockLLMClient",
    "build_client",
]
