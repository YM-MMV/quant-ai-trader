"""Typed domain models for quant-ai-trader (Pydantic v2).

These models are the shared vocabulary that flows through the system:

    Candle → MarketFeatures → KronosPrediction → StrategySignal
        → OrderIntent → RiskDecision → TradeLog
    BacktestResult summarises a strategy evaluation.

No model here connects to a real service (MT5, OpenBB, Kronos, QuantDinger).
They are pure data containers with validation. Safety rules (see SAFETY.md)
are encoded as validators where they belong to the data itself — most notably
``OrderIntent`` refuses to exist without a stop loss and a take profit.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class TradingMode(str, Enum):
    """The only allowed run modes (see AGENTS.md / SAFETY.md)."""

    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    MT5_DEMO = "mt5_demo"
    LIVE = "live"


class AssetClass(str, Enum):
    FOREX = "forex"
    METAL = "metal"
    CRYPTO = "crypto"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


class PredictionDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class StrategyStatus(str, Enum):
    INVENTORY = "inventory"
    TEMPLATE = "template"
    GENERATED = "generated"
    APPROVED = "approved"
    RESEARCH_ONLY = "research_only"
    NOT_APPLICABLE_TO_MT5 = "not_applicable_to_mt5"


class TradeStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #
class _Model(BaseModel):
    """Shared config: reject unknown fields so typos fail loudly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
class Candle(_Model):
    """A single normalized OHLCV bar."""

    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    timestamp: datetime
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: float = Field(0.0, ge=0)

    @model_validator(mode="after")
    def _check_ohlc_consistency(self) -> "Candle":
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.high < max(self.open, self.close):
            raise ValueError("high must be >= open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= open and close")
        return self


class MarketFeatures(_Model):
    """Engineered features for a symbol at a point in time.

    ``features`` is an open map of indicator name -> value so the schema does
    not need to change every time a new indicator is added. Computed without
    look-ahead bias (the contract lives in the feature engineering service,
    M3); this model only carries the result.
    """

    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    timestamp: datetime
    features: dict[str, float] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def _no_nan(cls, value: dict[str, float]) -> dict[str, float]:
        for key, val in value.items():
            if val != val:  # NaN check without importing math
                raise ValueError(f"feature {key!r} is NaN")
        return value


# --------------------------------------------------------------------------- #
# Predictions
# --------------------------------------------------------------------------- #
class KronosPrediction(_Model):
    """Output of the (mocked, for now) Kronos candlestick model."""

    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    as_of: datetime
    horizon: int = Field(..., gt=0, description="bars ahead being predicted")
    direction: PredictionDirection
    predicted_close: float = Field(..., gt=0)
    probability: float = Field(..., ge=0.0, le=1.0)
    model_name: str = "kronos-mock"
    raw: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #
class StrategyMetadata(_Model):
    """Describes a strategy in the inventory and its applicability."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    source: str = Field("quant-trading", description="origin repo / author")
    category: str = Field(..., min_length=1)
    asset_classes: list[AssetClass] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    status: StrategyStatus = StrategyStatus.INVENTORY
    applicable_to_mt5: bool = True
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class StrategySignal(_Model):
    """A trading signal emitted by a strategy adapter.

    A signal is advice, not an order. Suggested SL/TP are hints the
    RiskManager will later validate; they do not bypass any safety check.
    """

    strategy_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    timestamp: datetime
    action: SignalAction
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    suggested_stop_loss: Optional[float] = Field(None, gt=0)
    suggested_take_profit: Optional[float] = Field(None, gt=0)
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Orders & risk
# --------------------------------------------------------------------------- #
class OrderIntent(_Model):
    """A proposed order. The AI agent may only ever produce one of these;
    it never executes (see SAFETY.md / AGENTS.md).

    Per the hard safety rules, an OrderIntent cannot be constructed without a
    stop loss and a take profit. This is the first line of defence; the
    RiskManager (M7) re-checks everything before any execution.
    """

    symbol: str = Field(..., min_length=1)
    side: Side
    order_type: OrderType = OrderType.MARKET
    volume: float = Field(..., gt=0, description="lots / units")
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    price: Optional[float] = Field(
        None, gt=0, description="limit/stop price; None for market orders"
    )
    strategy_id: Optional[str] = None
    comment: str = ""

    @model_validator(mode="after")
    def _check_pending_price(self) -> "OrderIntent":
        if self.order_type in (OrderType.LIMIT, OrderType.STOP) and self.price is None:
            raise ValueError(f"{self.order_type.value} order requires a price")
        return self

    @model_validator(mode="after")
    def _check_sl_tp_directionality(self) -> "OrderIntent":
        # SL and TP must sit on the correct side of the entry, when known.
        ref = self.price
        if ref is None:
            return self
        if self.side is Side.BUY:
            if self.stop_loss >= ref:
                raise ValueError("buy stop_loss must be below entry price")
            if self.take_profit <= ref:
                raise ValueError("buy take_profit must be above entry price")
        else:  # SELL
            if self.stop_loss <= ref:
                raise ValueError("sell stop_loss must be above entry price")
            if self.take_profit >= ref:
                raise ValueError("sell take_profit must be below entry price")
        return self


class RiskDecision(_Model):
    """The RiskManager's verdict on an OrderIntent. The only gate to execution."""

    intent: OrderIntent
    approved: bool
    mode: TradingMode = TradingMode.PAPER
    reasons: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    approved_volume: Optional[float] = Field(None, gt=0)
    decided_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _denied_needs_reason(self) -> "RiskDecision":
        if not self.approved and not self.reasons:
            raise ValueError("a denied RiskDecision must include at least one reason")
        return self


# --------------------------------------------------------------------------- #
# Logs & results
# --------------------------------------------------------------------------- #
class TradeLog(_Model):
    """An auditable record of a trade's lifecycle."""

    id: str = Field(..., min_length=1)
    timestamp: datetime
    mode: TradingMode
    symbol: str = Field(..., min_length=1)
    side: Side
    volume: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    exit_price: Optional[float] = Field(None, gt=0)
    pnl: Optional[float] = None
    status: TradeStatus = TradeStatus.PENDING
    strategy_id: Optional[str] = None


class BacktestResult(_Model):
    """Summary metrics for a strategy backtest.

    ``includes_friction`` must be True: this project does not accept
    frictionless backtests (see ARCHITECTURE.md / PLAN.md).
    """

    strategy_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    start: datetime
    end: datetime
    num_trades: int = Field(..., ge=0)
    win_rate: float = Field(..., ge=0.0, le=1.0)
    net_profit: float
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    max_drawdown: float = Field(0.0, ge=0.0)
    sharpe: Optional[float] = None
    profit_factor: Optional[float] = Field(None, ge=0.0)
    includes_friction: bool = True

    @model_validator(mode="after")
    def _check_window(self) -> "BacktestResult":
        if self.end < self.start:
            raise ValueError("end must be >= start")
        return self

    @field_validator("includes_friction")
    @classmethod
    def _must_include_friction(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "frictionless backtests are not allowed; include spread/slippage/"
                "commission (see ARCHITECTURE.md)"
            )
        return value
