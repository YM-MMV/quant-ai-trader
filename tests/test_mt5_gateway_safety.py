"""Safety tests for the real MT5 execution gateway (M22).

MetaTrader5 is **mocked** — no test touches a real terminal or sends a real
order. The focus is the live-lock contract: the gateway is off by default and
refuses to place an order unless every lock is satisfied.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.config_loader import PROJECT_ROOT
from services.execution_service.base_gateway import OrderRejectedError
from services.execution_service.mt5_gateway import (
    LIVE_LOCKS,
    LiveTradingDisabledError,
    MT5Gateway,
)
from services.models import OrderIntent, RiskDecision, Side, TradingMode


# --------------------------------------------------------------------------- #
# Fakes — a mock MetaTrader5 module and settings (no real terminal)
# --------------------------------------------------------------------------- #
class FakeMT5:
    """Minimal stand-in for the MetaTrader5 package. Records order_send calls."""

    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0

    def __init__(self):
        self.initialized = False
        self.order_send_calls: list[dict] = []
        self.order_check_calls: list[dict] = []

    def initialize(self, **kwargs):
        self.initialized = True
        return True

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(login=999, server="MockDemo", currency="USD",
                               leverage=100, balance=10_000.0, equity=10_000.0,
                               margin=0.0, margin_free=10_000.0)

    def positions_get(self, symbol=None):
        return []

    def symbol_info_tick(self, broker):
        return SimpleNamespace(bid=1.09995, ask=1.10005)

    def symbol_info(self, broker):
        return None   # unknown by default; tests override for a real spec

    def order_check(self, request):
        self.order_check_calls.append(request)
        return SimpleNamespace(retcode=0, margin=110.0, comment="ok", balance=10_000.0)

    def order_send(self, request):
        self.order_send_calls.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=555,
                               price=request.get("price"), comment="done")


def fake_settings(*, mode=TradingMode.PAPER, allow_live=False, allowlist=("EURUSD",)):
    return SimpleNamespace(
        trading_mode=mode,
        allow_live_trading=allow_live,
        symbol_allowlist=list(allowlist),
        mt5_login="123", mt5_password="secret", mt5_server="MockDemo",
        mt5_terminal_path=None,
    )


def make_intent(symbol="EURUSD", side=Side.BUY):
    return OrderIntent(symbol=symbol, side=side, volume=0.1,
                       stop_loss=1.095, take_profit=1.11)


def approved(intent, *, strategy_ok=True):
    return RiskDecision(
        intent=intent, approved=True, mode=TradingMode.LIVE, approved_volume=0.1,
        checks={"strategy_approved": strategy_ok, "symbol_allowlisted": True},
    )


def denied(intent):
    return RiskDecision(intent=intent, approved=False, mode=TradingMode.PAPER,
                        reasons=["risk denied"])


def gateway(settings, fake=None):
    fake = fake or FakeMT5()
    gw = MT5Gateway(settings=settings, mt5_module=fake)
    return gw, fake


# --------------------------------------------------------------------------- #
# Locked by default (Done-when)
# --------------------------------------------------------------------------- #
def test_live_trading_disabled_by_default():
    # Default settings: paper mode, live not allowed. Even a fully-approved,
    # matching decision must be refused, and no order is sent.
    gw, fake = gateway(fake_settings())  # paper / allow_live False
    intent = make_intent()
    with pytest.raises(LiveTradingDisabledError):
        gw.send_order(intent, approved(intent))
    assert fake.order_send_calls == []


def test_evaluate_live_locks_default_all_blocking():
    gw, _ = gateway(fake_settings())
    intent = make_intent()
    locks = gw.evaluate_live_locks(intent, approved(intent))
    assert locks["mode_is_live"] is False
    assert locks["allow_live_trading"] is False
    # The order-integrity / approval locks still pass on a good intent+decision.
    assert locks["stop_loss_present"] is True
    assert locks["take_profit_present"] is True
    assert locks["symbol_allowlisted"] is True


# --------------------------------------------------------------------------- #
# send_order refuses without approval
# --------------------------------------------------------------------------- #
def test_send_order_refuses_without_decision():
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True))
    with pytest.raises(OrderRejectedError):
        gw.send_order(make_intent(), None)
    assert fake.order_send_calls == []


def test_send_order_refuses_when_risk_denied():
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True))
    intent = make_intent()
    with pytest.raises(OrderRejectedError):
        gw.send_order(intent, denied(intent))
    assert fake.order_send_calls == []


def test_send_order_refuses_mismatched_decision():
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True))
    intent = make_intent("EURUSD")
    other = make_intent("GBPUSD")
    with pytest.raises(OrderRejectedError):
        gw.send_order(intent, approved(other))
    assert fake.order_send_calls == []


# --------------------------------------------------------------------------- #
# send_order refuses if mode is paper / ALLOW_LIVE_TRADING is false
# --------------------------------------------------------------------------- #
def test_send_order_refuses_if_mode_paper():
    # allow_live True but mode paper → still refused (both locks required).
    gw, fake = gateway(fake_settings(mode=TradingMode.PAPER, allow_live=True))
    intent = make_intent()
    with pytest.raises(LiveTradingDisabledError):
        gw.send_order(intent, approved(intent))
    assert fake.order_send_calls == []


def test_send_order_refuses_if_allow_live_false():
    # mode live but ALLOW_LIVE_TRADING false → refused (defence in depth, even
    # though Settings itself would reject this combination).
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=False))
    intent = make_intent()
    with pytest.raises(LiveTradingDisabledError):
        gw.send_order(intent, approved(intent))
    assert fake.order_send_calls == []


def test_send_order_refuses_if_strategy_not_approved():
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True))
    intent = make_intent()
    with pytest.raises(LiveTradingDisabledError):
        gw.send_order(intent, approved(intent, strategy_ok=False))
    assert fake.order_send_calls == []


def test_send_order_refuses_if_symbol_not_allowlisted():
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True,
                                     allowlist=("GBPUSD",)))
    intent = make_intent("EURUSD")
    with pytest.raises(LiveTradingDisabledError):
        gw.send_order(intent, approved(intent))
    assert fake.order_send_calls == []


def test_refused_order_is_logged():
    gw, _ = gateway(fake_settings())  # paper, locked
    intent = make_intent()
    with pytest.raises(LiveTradingDisabledError):
        gw.send_order(intent, approved(intent))
    rejects = [e for e in gw.events if e.action == "send_order" and e.status == "rejected"]
    assert len(rejects) == 1


# --------------------------------------------------------------------------- #
# Happy path: every lock satisfied → order_send is called exactly once
# --------------------------------------------------------------------------- #
def test_send_order_succeeds_when_all_locks_pass():
    gw, fake = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True))
    gw.connect()
    intent = make_intent()
    result = gw.send_order(intent, approved(intent))
    assert result.success is True
    assert result.status == "filled"
    assert result.position_id == 555
    assert len(fake.order_send_calls) == 1  # exactly one real order_send
    # The request carried the mandatory SL/TP.
    sent = fake.order_send_calls[0]
    assert sent["sl"] == 1.095 and sent["tp"] == 1.11


def test_all_locks_true_when_fully_enabled():
    gw, _ = gateway(fake_settings(mode=TradingMode.LIVE, allow_live=True))
    intent = make_intent()
    locks = gw.evaluate_live_locks(intent, approved(intent))
    assert all(locks[name] for name in LIVE_LOCKS)


# --------------------------------------------------------------------------- #
# Connection / queries through the mocked package
# --------------------------------------------------------------------------- #
def test_connect_and_account_info():
    gw, fake = gateway(fake_settings())
    assert gw.connect() is True
    assert fake.initialized is True
    info = gw.account_info()
    assert info.balance == 10_000.0
    assert "password" not in info.model_dump()


def test_get_quote():
    gw, _ = gateway(fake_settings())
    gw.connect()
    q = gw.get_quote("EURUSD")
    assert q.ask > q.bid


def test_broker_symbol_falls_back_to_static_alias_without_terminal_list():
    # FakeMT5 has no symbols_get -> _available stays empty -> static spec alias.
    gw, _ = gateway(fake_settings())
    gw.connect()
    assert gw._broker_symbol("XAUUSD") == "GOLD"


def test_broker_symbol_reconciles_against_terminal_symbols():
    # A broker that exposes gold as XAUUSD (not GOLD): the gateway must route to
    # the name the terminal actually has, reconciled from symbols_get().
    fake = FakeMT5()
    fake.symbols_get = lambda: [
        SimpleNamespace(name=n) for n in ("EURUSD", "XAUUSD", "GBPUSD")
    ]
    gw, _ = gateway(
        fake_settings(mode=TradingMode.LIVE, allow_live=True, allowlist=("XAUUSD",)),
        fake,
    )
    gw.connect()
    assert gw._broker_symbol("XAUUSD") == "XAUUSD"
    assert gw._broker_symbol("EURUSD") == "EURUSD"


def test_min_stop_distance_from_symbol_info():
    # stops level 50 points * point 0.001 = 0.05 in price terms.
    fake = FakeMT5()
    fake.symbol_info = lambda broker: SimpleNamespace(trade_stops_level=50, point=0.001)
    gw, _ = gateway(fake_settings(allowlist=("XAUUSD",)), fake)
    gw.connect()
    assert gw.min_stop_distance("XAUUSD") == pytest.approx(0.05)


def test_min_stop_distance_zero_when_unavailable():
    # FakeMT5 has no symbol_info -> guarded path returns 0.0 (caller falls back).
    gw, _ = gateway(fake_settings())
    gw.connect()
    assert gw.min_stop_distance("EURUSD") == 0.0


# --------------------------------------------------------------------------- #
# order_send must not be exposed outside this gateway
# --------------------------------------------------------------------------- #
def test_order_send_only_called_in_mt5_gateway():
    # Scan the production source: a literal `order_send(` call may appear only in
    # mt5_gateway.py. (Docstrings elsewhere may mention the name without a call.)
    offenders = []
    for base in ("services", "apps"):
        for path in (PROJECT_ROOT / base).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "order_send(" in text and path.name != "mt5_gateway.py":
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"order_send( found outside mt5_gateway.py: {offenders}"


def test_gateway_uses_metatrader5_package():
    # The real gateway imports the official package (lazily/guarded).
    src = (PROJECT_ROOT / "services" / "execution_service" / "mt5_gateway.py").read_text("utf-8")
    assert "import MetaTrader5" in src
