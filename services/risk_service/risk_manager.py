"""Deterministic risk manager — the single gate before any execution.

The :class:`RiskManager` evaluates a proposed :class:`~services.models.OrderIntent`
against the configured limits plus a runtime :class:`RiskContext`, and returns a
:class:`~services.models.RiskDecision` with ``approved`` and a clear list of
``reasons``. **Every** rule is evaluated (no short-circuit) so a rejected
decision enumerates *all* violations, and a ``checks`` map records each rule's
pass/fail for auditing.

This is pure, deterministic code — **no AI, no MT5, no network**. The same intent
+ context + config always yields the same decision.

Rejection rules (each maps to a key in ``RiskDecision.checks``):

* trading mode not allowed / live trading disabled
* symbol not allowlisted
* stop loss missing / take profit missing
* per-trade risk percent exceeds the limit
* daily loss limit hit / max open trades / max trades per day exceeded
* spread too high / volatility too high
* duplicate trade already open
* strategy not approved / research-only strategy attempting to execute
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from services.config_loader import RiskConfig
from services.models import OrderIntent, RiskDecision, TradingMode
from services.risk_service.symbol_specs import get_symbol_spec

# Applicability values that are allowed to reach execution (mirrors M9: research
# and not-applicable projects never execute).
EXECUTABLE_APPLICABILITIES = frozenset({"direct", "adaptable"})


@dataclass
class RiskContext:
    """Runtime state the limits are evaluated against (caller-supplied)."""

    mode: TradingMode
    allow_live: bool = False
    allowlist: tuple[str, ...] = ()
    account_balance: float = 0.0
    reference_price: Optional[float] = None     # entry price for the risk calc
    spread_points: float = 0.0
    volatility: Optional[float] = None          # same unit as config.max_volatility
    realized_daily_loss: float = 0.0            # positive magnitude of today's loss
    open_trades: tuple[tuple[str, str], ...] = ()  # (symbol, side) currently open
    trades_today: int = 0
    strategy_approved: bool = True
    strategy_applicability: str = "direct"      # MT5Applicability value (M6/M9)
    kill_switch_active: bool = False


def _default_config() -> RiskConfig:
    # A self-contained default mirroring config/risk.yaml, so the manager is
    # usable without touching the filesystem in tests.
    return RiskConfig(
        require_stop_loss=True,
        require_take_profit=True,
        max_daily_loss=100.0,
        max_daily_loss_pct=0.0,
        max_open_trades=3,
        max_spread_points=30,
        max_risk_per_trade_pct=1.0,
        max_total_exposure_pct=5.0,
        kill_switch_enabled=True,
        max_trades_per_day=10,
        max_volatility=None,
    )


class RiskManager:
    """Evaluate an :class:`OrderIntent` against the risk limits + context."""

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or _default_config()

    def evaluate(
        self,
        intent: OrderIntent,
        context: RiskContext,
        *,
        now: Optional[datetime] = None,
    ) -> RiskDecision:
        cfg = self.config
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        def check(name: str, passed: bool, reason: str) -> None:
            checks[name] = bool(passed)
            if not passed:
                reasons.append(reason)

        symbol = getattr(intent, "symbol", None)
        side = getattr(intent, "side", None)
        side_value = getattr(side, "value", side)
        volume = getattr(intent, "volume", None) or 0.0
        stop_loss = getattr(intent, "stop_loss", None)
        take_profit = getattr(intent, "take_profit", None)

        # -- mode / live lock / kill switch -------------------------------- #
        check("trading_mode_allowed", context.mode in cfg.allowed_modes,
              f"trading mode {context.mode.value!r} is not allowed")
        check("live_trading_enabled",
              not (context.mode is TradingMode.LIVE and not context.allow_live),
              "live trading is disabled")
        check("kill_switch_clear",
              not (cfg.kill_switch_enabled and context.kill_switch_active),
              "kill switch is active")

        # -- symbol / strategy gating -------------------------------------- #
        check("symbol_allowlisted", symbol in context.allowlist,
              f"symbol {symbol!r} is not allowlisted")
        check("strategy_approved", bool(context.strategy_approved),
              "strategy is not approved for trading")
        check("not_research_only",
              context.strategy_applicability in EXECUTABLE_APPLICABILITIES,
              f"strategy applicability {context.strategy_applicability!r} is not "
              "executable (research-only)")

        # -- order integrity ----------------------------------------------- #
        check("stop_loss_present",
              not cfg.require_stop_loss or (stop_loss is not None and stop_loss > 0),
              "stop loss is missing")
        check("take_profit_present",
              not cfg.require_take_profit or (take_profit is not None and take_profit > 0),
              "take profit is missing")

        # -- per-trade risk percent ---------------------------------------- #
        spec = get_symbol_spec(symbol) if symbol else None
        risk_pct = self._risk_pct(intent, context, spec, stop_loss, volume)
        if risk_pct is None:
            check("risk_per_trade", spec is not None,
                  f"no contract spec for {symbol!r}; cannot size risk")
        else:
            check("risk_per_trade", risk_pct <= cfg.max_risk_per_trade_pct + 1e-9,
                  f"risk per trade {risk_pct:.3f}% exceeds limit "
                  f"{cfg.max_risk_per_trade_pct}%")

        # -- exposure / frequency ------------------------------------------ #
        daily_limit = self._daily_loss_limit(context)
        if daily_limit is None:
            check("daily_loss_limit", True, "no daily-loss limit configured")
        else:
            check("daily_loss_limit", context.realized_daily_loss < daily_limit,
                  f"daily loss {context.realized_daily_loss} hit limit {daily_limit:.2f}")
        check("max_open_trades", len(context.open_trades) < cfg.max_open_trades,
              f"open trades {len(context.open_trades)} at limit {cfg.max_open_trades}")
        check("max_trades_per_day", context.trades_today < cfg.max_trades_per_day,
              f"trades today {context.trades_today} at limit {cfg.max_trades_per_day}")

        # -- market conditions --------------------------------------------- #
        check("spread_ok", context.spread_points <= cfg.max_spread_points,
              f"spread {context.spread_points} exceeds max {cfg.max_spread_points}")
        vol_ok = (
            cfg.max_volatility is None
            or context.volatility is None
            or context.volatility <= cfg.max_volatility
        )
        check("volatility_ok", vol_ok,
              f"volatility {context.volatility} exceeds max {cfg.max_volatility}")

        # -- duplicate ----------------------------------------------------- #
        is_dupe = (symbol, side_value) in context.open_trades
        check("no_duplicate_trade", not is_dupe,
              f"a {side_value} position in {symbol!r} is already open")

        approved = not reasons
        return RiskDecision(
            intent=intent,
            approved=approved,
            mode=context.mode,
            reasons=reasons,
            checks=checks,
            approved_volume=(volume if approved and volume > 0 else None),
            decided_at=now,
        )

    # ------------------------------------------------------------------ #
    def _daily_loss_limit(self, context: RiskContext) -> Optional[float]:
        """Effective daily-loss limit: the tighter of the absolute cap and the
        balance-relative cap.

        Each cap is disabled when its config value is 0; the percent cap also
        needs a positive balance. Returns ``None`` when neither is configured
        (no daily-loss limit), so the manager treats the check as a pass.
        """
        cfg = self.config
        limits: list[float] = []
        if cfg.max_daily_loss > 0:
            limits.append(cfg.max_daily_loss)
        pct = getattr(cfg, "max_daily_loss_pct", 0.0)
        if pct > 0 and context.account_balance > 0:
            limits.append(pct / 100.0 * context.account_balance)
        return min(limits) if limits else None

    @staticmethod
    def _risk_pct(intent, context, spec, stop_loss, volume) -> Optional[float]:
        """Per-trade risk as a percent of balance, or None if not computable."""
        if spec is None or stop_loss is None or stop_loss <= 0 or volume <= 0:
            return None
        entry = getattr(intent, "price", None)
        if entry is None:
            entry = context.reference_price
        if entry is None or context.account_balance <= 0:
            return None
        money_at_risk = abs(float(entry) - float(stop_loss)) * volume * spec.contract_size
        return money_at_risk / context.account_balance * 100.0


__all__ = ["RiskManager", "RiskContext", "EXECUTABLE_APPLICABILITIES"]
