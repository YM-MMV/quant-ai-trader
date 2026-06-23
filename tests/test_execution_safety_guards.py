"""Final safety guards (M28) — codify the whole-codebase safety review.

These tests are *regression locks* for the project's hard safety rules (see
``SAFETY.md`` and ``docs/FINAL_SAFETY_REVIEW.md``). They assert the invariants
statically (scanning the production source) and behaviourally (exercising the
gates), so any future change that opens an execution bypass fails CI:

* ``order_send`` may be called **only** inside ``mt5_gateway.py``.
* ``MetaTrader5`` may be imported **only** in the two guarded MT5 modules, and
  only lazily (never as an unconditional top-level import).
* every order requires an **approved** ``RiskDecision`` that matches the intent;
* paper is the **default** trading mode and live is **disabled by default**;
* ``live`` cannot be half-enabled (both switches are required);
* the agent is paper-only and cannot self-approve;
* no real secrets are committed (``.env`` is git-ignored; ``.env.example`` holds
  placeholders only).

Pure static + in-memory checks — no MT5, no network, no secrets.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.agent.agent_config import (
    DEFAULT_AGENT_CONFIG,
    AgentConfig,
    AgentPermissionError,
    assert_paper_only,
)
from apps.agent.tools import create_paper_trade
from services.config_loader import PROJECT_ROOT, Settings, load_app_config
from services.execution_service.base_gateway import (
    OrderRejectedError,
    approval_problem,
)
from services.execution_service.paper_execution import PaperExecutionService
from services.models import OrderIntent, RiskDecision, Side, TradingMode
from services.risk_service.risk_manager import RiskContext, RiskManager

# Production source trees that must obey the execution guards (tests excluded —
# tests legitimately reference the names to assert on them).
SOURCE_DIRS = ("services", "apps", "scripts")

# The only modules allowed to touch the MetaTrader5 package, each for a single,
# narrow purpose.
MT5_DATA_MODULE = "mt5_data.py"        # read-only candle download, no order_send
MT5_GATEWAY_MODULE = "mt5_gateway.py"  # the one locked execution gateway


def _source_files() -> list[Path]:
    files: list[Path] = []
    for base in SOURCE_DIRS:
        root = PROJECT_ROOT / base
        if root.exists():
            files.extend(root.rglob("*.py"))
    return files


def _hermetic_settings(**env: str) -> Settings:
    """Settings without reading any on-disk ``.env`` (hermetic, like config tests)."""
    return Settings(_env_file=None, **env)


# --------------------------------------------------------------------------- #
# order_send is encapsulated in the single locked gateway
# --------------------------------------------------------------------------- #
def test_order_send_called_only_in_mt5_gateway():
    """A literal ``order_send(`` call may appear only in mt5_gateway.py."""
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if "order_send(" in text and path.name != MT5_GATEWAY_MODULE:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"order_send( found outside the gateway: {offenders}"


def test_mt5_data_module_never_sends_orders():
    """The read-only data bridge must never *call* any order-placement function.

    Prose mentions in the module docstring (e.g. "no ``order_send``") are fine;
    what is forbidden is an actual call, so we match the call forms.
    """
    src = (PROJECT_ROOT / "services" / "data_service" / MT5_DATA_MODULE).read_text("utf-8")
    for forbidden in ("order_send(", ".order_send", "OrderSend(",
                      "order_check(", "positions_get("):
        assert forbidden not in src, f"{forbidden!r} must not appear in mt5_data.py"


# --------------------------------------------------------------------------- #
# MetaTrader5 import is confined and lazy
# --------------------------------------------------------------------------- #
def test_metatrader5_imported_only_in_allowed_modules():
    """Only the two guarded MT5 modules may import the MetaTrader5 package."""
    offenders = []
    allowed = {MT5_DATA_MODULE, MT5_GATEWAY_MODULE}
    for path in _source_files():
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                a.name == "MetaTrader5" for a in node.names
            ):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
            elif isinstance(node, ast.ImportFrom) and node.module == "MetaTrader5":
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"MetaTrader5 imported outside allowed modules: {offenders}"


@pytest.mark.parametrize("module", [
    "services/data_service/mt5_data.py",
    "services/execution_service/mt5_gateway.py",
])
def test_metatrader5_import_is_lazy_guarded(module):
    """The MetaTrader5 import must be guarded (inside try/except), never top-level.

    A guarded import is what lets the whole project (and CI) run without the
    Windows-only package present.
    """
    tree = ast.parse((PROJECT_ROOT / module).read_text(encoding="utf-8"))
    top_level_import = any(
        isinstance(node, ast.Import) and any(a.name == "MetaTrader5" for a in node.names)
        for node in tree.body  # module body only — not nested in try/except
    )
    assert not top_level_import, f"{module} imports MetaTrader5 unguarded at top level"


# --------------------------------------------------------------------------- #
# Every order requires an approved, matching RiskDecision
# --------------------------------------------------------------------------- #
def _intent(symbol: str = "EURUSD") -> OrderIntent:
    return OrderIntent(
        symbol=symbol, side=Side.BUY, volume=0.01,
        price=1.10, stop_loss=1.09, take_profit=1.12, strategy_id="t",
    )


def _decision(intent: OrderIntent, *, approved: bool) -> RiskDecision:
    return RiskDecision(
        intent=intent, approved=approved, mode=TradingMode.PAPER,
        reasons=[] if approved else ["blocked"], checks={},
    )


def test_approval_gate_rejects_missing_decision():
    assert approval_problem(_intent(), None) is not None


def test_approval_gate_rejects_unapproved_decision():
    intent = _intent()
    assert approval_problem(intent, _decision(intent, approved=False)) is not None


def test_approval_gate_rejects_reused_decision_for_other_intent():
    """An approval issued for one intent cannot be replayed for another."""
    approved_other = _decision(_intent("EURUSD"), approved=True)
    assert approval_problem(_intent("XAUUSD"), approved_other) is not None


def test_approval_gate_accepts_matching_approved_decision():
    intent = _intent()
    assert approval_problem(intent, _decision(intent, approved=True)) is None


def test_paper_execution_requires_a_risk_decision():
    """PaperExecutionService cannot turn anything into a trade without a decision."""
    ctx = RiskContext(mode=TradingMode.PAPER, allowlist=("EURUSD",))
    with pytest.raises(TypeError):
        PaperExecutionService().execute(object(), ctx, timeframe="H1")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Paper is default; live is disabled by default and cannot be half-enabled
# --------------------------------------------------------------------------- #
def test_paper_is_the_default_mode():
    assert _hermetic_settings().trading_mode is TradingMode.PAPER
    assert load_app_config().default_mode is TradingMode.PAPER
    assert DEFAULT_AGENT_CONFIG.trading_mode is TradingMode.PAPER


def test_live_is_disabled_by_default():
    assert _hermetic_settings().allow_live_trading is False


def test_live_requires_both_switches():
    """TRADING_MODE=live with ALLOW_LIVE_TRADING=false must be rejected at load."""
    with pytest.raises(ValidationError):
        _hermetic_settings(TRADING_MODE="live", ALLOW_LIVE_TRADING="false")
    s = _hermetic_settings(TRADING_MODE="live", ALLOW_LIVE_TRADING="true")
    assert s.trading_mode is TradingMode.LIVE and s.allow_live_trading is True


# --------------------------------------------------------------------------- #
# The agent is paper-only and cannot bypass the risk gate
# --------------------------------------------------------------------------- #
def test_agent_default_is_paper_only():
    assert_paper_only(DEFAULT_AGENT_CONFIG)  # must not raise


def test_agent_rejects_non_paper_mode():
    with pytest.raises(AgentPermissionError):
        assert_paper_only(AgentConfig(trading_mode=TradingMode.LIVE))


def test_create_paper_trade_reruns_risk_and_blocks_unallowlisted():
    """A non-allowlisted symbol is rejected; no trade is created, nothing executes."""
    result = create_paper_trade(
        {"symbol": "DOGEUSD", "side": "buy", "volume": 0.01,
         "price": 0.1, "stop_loss": 0.09, "take_profit": 0.12, "strategy_id": "t"},
        reference_price=0.1, account_balance=10_000.0,
    )
    assert result["created"] is False
    assert result["approved"] is False
    assert any("allowlist" in r.lower() for r in result["reasons"])


# --------------------------------------------------------------------------- #
# No secrets are committed
# --------------------------------------------------------------------------- #
def test_env_is_gitignored_and_example_is_not():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "!.env.example" in gitignore  # the placeholder file stays tracked


def test_no_committed_dotenv_with_secrets():
    """Only .env.example may exist on disk; a real .env must never be present."""
    stray = [
        str(p.relative_to(PROJECT_ROOT))
        for p in PROJECT_ROOT.rglob(".env*")
        if p.is_file() and p.name != ".env.example" and ".venv" not in p.parts
    ]
    assert stray == [], f"unexpected dotenv files present: {stray}"


def test_env_example_holds_placeholders_only():
    """Secret-bearing keys in .env.example must be empty, and defaults paper-safe."""
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()

    # Secrets must carry no value in the committed example.
    for secret_key in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
                       "MT5_TERMINAL_PATH", "OPENBB_PAT"):
        assert values.get(secret_key, "") == "", f"{secret_key} must be blank in .env.example"

    # Safety defaults must be encoded in the example itself.
    assert values.get("TRADING_MODE") == "paper"
    assert values.get("ALLOW_LIVE_TRADING") == "false"
