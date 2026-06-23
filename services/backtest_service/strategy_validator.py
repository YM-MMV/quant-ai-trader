"""Strategy validation and approval gate.

Before a strategy may be used for *paper* trading it must clear a strict, fully
deterministic set of gates evaluated against its backtest metrics. This module
runs those gates and emits an auditable :class:`ValidationReport`; only when
every required gate passes does the strategy earn an :class:`ApprovalRecord`.

Design notes:

* **Pure & deterministic.** :meth:`StrategyValidator.validate` reads metrics and
  flags — no MT5, no network, no AI, no wall-clock (timestamps are injected).
* **Required vs. non-blocking gates.** Required gates (min trades, profit factor,
  drawdown, expectancy, largest-trade concentration, stop/TP presence,
  out-of-sample positivity, no look-ahead) block approval. Sensitivity gates
  (slippage/spread) block *only* when stressed results are supplied and fail;
  otherwise they are recorded as ``not_evaluated``. Parameter-sensitivity and
  walk-forward are explicit placeholders (recorded, never blocking yet).
* **Honest gaps.** A required gate whose evidence is missing (e.g. no
  out-of-sample metrics) *fails* rather than passing silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from services.backtest_service.costs import CostModel
from services.backtest_service.metrics import BacktestMetrics
from services.config_loader import CONFIG_DIR, load_yaml
from services.models import StrategyStatus

VALIDATOR_VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
class StrategyValidationConfig(BaseModel):
    """Thresholds for the approval gate (see config/strategy_validation.yaml)."""

    model_config = ConfigDict(extra="forbid")

    minimum_trades: int = Field(100, ge=0)
    minimum_profit_factor: float = Field(1.2, ge=0)
    maximum_drawdown_pct: float = Field(15.0, ge=0)
    maximum_largest_trade_contribution_pct: float = Field(25.0, ge=0, le=100)
    minimum_expectancy: float = 0.0
    # Opt-in: 0.0 records Sharpe without blocking; a positive value makes a
    # minimum annualised Sharpe a *required* gate (missing evidence then fails).
    minimum_sharpe: float = Field(0.0, ge=0)
    require_stop_loss: bool = True
    require_take_profit: bool = True
    require_out_of_sample_positive: bool = True

    minimum_stress_profit_factor: float = Field(1.0, ge=0)
    slippage_stress_points: float = Field(2.0, ge=0)
    spread_stress_multiplier: float = Field(1.5, ge=0)


def load_validation_config(config_dir=CONFIG_DIR) -> StrategyValidationConfig:
    """Load and validate ``config/strategy_validation.yaml``."""
    return StrategyValidationConfig.model_validate(
        load_yaml(config_dir / "strategy_validation.yaml")
    )


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
@dataclass
class ValidationInput:
    """Everything the gates need, decoupled from how it was produced.

    ``in_sample`` is mandatory; the rest are optional evidence. Build one
    directly (unit tests) or via :meth:`from_reports` from M10 backtest reports.
    """

    in_sample: BacktestMetrics
    out_of_sample: Optional[BacktestMetrics] = None
    all_trades_have_stop_loss: bool = True
    all_trades_have_take_profit: bool = True
    rejected_no_stop_signals: int = 0
    slippage_stress: Optional[BacktestMetrics] = None
    spread_stress: Optional[BacktestMetrics] = None
    look_ahead_flags: tuple[str, ...] = ()
    # Placeholders for later milestones (recorded, not yet computed).
    parameter_sensitivity: Optional[float] = None
    walk_forward: Optional[BacktestMetrics] = None

    @classmethod
    def from_reports(
        cls,
        in_report,
        out_of_sample_report=None,
        *,
        slippage_stress_report=None,
        spread_stress_report=None,
        look_ahead_flags: Sequence[str] = (),
    ) -> "ValidationInput":
        """Build an input from M10 ``BacktestReport`` objects (duck-typed)."""
        trades = list(getattr(in_report, "trades", []) or [])
        return cls(
            in_sample=in_report.metrics,
            out_of_sample=(out_of_sample_report.metrics if out_of_sample_report else None),
            all_trades_have_stop_loss=all(
                getattr(t, "stop_loss", None) not in (None, 0) for t in trades
            ),
            all_trades_have_take_profit=all(
                getattr(t, "take_profit", None) is not None for t in trades
            ),
            rejected_no_stop_signals=int(getattr(in_report, "rejected_no_stop", 0)),
            slippage_stress=(slippage_stress_report.metrics if slippage_stress_report else None),
            spread_stress=(spread_stress_report.metrics if spread_stress_report else None),
            look_ahead_flags=tuple(look_ahead_flags),
        )


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
class RuleStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: RuleStatus
    blocking: bool
    detail: str
    observed: Optional[float] = None
    threshold: Optional[float] = None


class ApprovalRecord(BaseModel):
    """Issued only for an approved strategy — the gate's auditable output."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    status: str = StrategyStatus.APPROVED.value
    validator_version: str = VALIDATOR_VERSION
    approved_at: Optional[datetime] = None
    config: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    approved: bool
    results: list[RuleResult]
    failed_rules: list[str]
    summary: str
    validator_version: str = VALIDATOR_VERSION
    created_at: Optional[datetime] = None

    def approval_record(self, approved_at: Optional[datetime] = None) -> ApprovalRecord:
        """Return the approval record. Raises if the strategy was not approved."""
        if not self.approved:
            raise ValueError(
                f"strategy {self.strategy_id!r} was rejected; "
                f"failed: {self.failed_rules}"
            )
        observed = {
            r.name: r.observed for r in self.results if r.observed is not None
        }
        return ApprovalRecord(
            strategy_id=self.strategy_id,
            approved_at=approved_at or self.created_at,
            metrics=observed,
            notes=[r.detail for r in self.results if r.status is RuleStatus.NOT_EVALUATED],
        )


# --------------------------------------------------------------------------- #
# Validator
# --------------------------------------------------------------------------- #
class StrategyValidator:
    """Evaluate the approval gates against a :class:`ValidationInput`."""

    def __init__(self, config: Optional[StrategyValidationConfig] = None) -> None:
        self.config = config or StrategyValidationConfig()

    def validate(
        self,
        data: ValidationInput,
        *,
        strategy_id: str,
        now: Optional[datetime] = None,
    ) -> ValidationReport:
        cfg = self.config
        ins = data.in_sample
        results: list[RuleResult] = []

        def add(name, ok, blocking, detail, *, observed=None, threshold=None,
                evaluated=True):
            status = (
                RuleStatus.NOT_EVALUATED if not evaluated
                else (RuleStatus.PASS if ok else RuleStatus.FAIL)
            )
            results.append(RuleResult(
                name=name, status=status, blocking=blocking, detail=detail,
                observed=observed, threshold=threshold,
            ))

        # 1) Minimum trades
        add("minimum_trades", ins.total_trades >= cfg.minimum_trades, True,
            f"{ins.total_trades} trades vs required {cfg.minimum_trades}",
            observed=ins.total_trades, threshold=cfg.minimum_trades)

        # 2) Out-of-sample positive
        if cfg.require_out_of_sample_positive:
            if data.out_of_sample is None:
                add("out_of_sample_positive", False, True,
                    "out-of-sample metrics not provided", evaluated=True)
            else:
                oos = data.out_of_sample
                add("out_of_sample_positive", oos.net_profit > 0, True,
                    f"out-of-sample net profit {oos.net_profit}",
                    observed=oos.net_profit, threshold=0.0)
        else:
            add("out_of_sample_positive", True, False,
                "out-of-sample positivity not required", evaluated=False)

        # 3) Max drawdown
        dd_pct = ins.max_drawdown_pct * 100.0
        add("maximum_drawdown", dd_pct <= cfg.maximum_drawdown_pct, True,
            f"drawdown {dd_pct:.2f}% vs limit {cfg.maximum_drawdown_pct}%",
            observed=round(dd_pct, 4), threshold=cfg.maximum_drawdown_pct)

        # 4) Profit factor (None ⇒ no losing trades ⇒ pass)
        pf = ins.profit_factor
        add("minimum_profit_factor", pf is None or pf >= cfg.minimum_profit_factor, True,
            f"profit factor {pf} vs required {cfg.minimum_profit_factor}",
            observed=pf, threshold=cfg.minimum_profit_factor)

        # 5) Expectancy
        add("minimum_expectancy", ins.expectancy >= cfg.minimum_expectancy, True,
            f"expectancy {ins.expectancy} vs required {cfg.minimum_expectancy}",
            observed=ins.expectancy, threshold=cfg.minimum_expectancy)

        # 5b) Minimum annualised Sharpe — opt-in (blocks only when configured > 0)
        sharpe = ins.sharpe_ratio
        if cfg.minimum_sharpe > 0:
            if sharpe is None:
                add("minimum_sharpe", False, True,
                    "annualised Sharpe required but unavailable (no period info)",
                    threshold=cfg.minimum_sharpe)
            else:
                add("minimum_sharpe", sharpe >= cfg.minimum_sharpe, True,
                    f"annualised Sharpe {sharpe:.3f} vs required {cfg.minimum_sharpe}",
                    observed=round(sharpe, 6), threshold=cfg.minimum_sharpe)
        else:
            add("minimum_sharpe", True, False,
                (f"annualised Sharpe {sharpe:.3f} (informational; not gated)"
                 if sharpe is not None else "annualised Sharpe not available"),
                observed=(round(sharpe, 6) if sharpe is not None else None),
                evaluated=sharpe is not None)

        # 6) Largest-trade contribution
        contrib_pct = ins.largest_winner_contribution * 100.0
        add("maximum_largest_trade_contribution",
            contrib_pct <= cfg.maximum_largest_trade_contribution_pct, True,
            f"largest trade {contrib_pct:.2f}% of profit vs limit "
            f"{cfg.maximum_largest_trade_contribution_pct}%",
            observed=round(contrib_pct, 4),
            threshold=cfg.maximum_largest_trade_contribution_pct)

        # 7) Stop loss present
        if cfg.require_stop_loss:
            ok = data.all_trades_have_stop_loss and data.rejected_no_stop_signals == 0
            add("no_missing_stop_loss", ok, True,
                "all trades carry a stop loss and no stop-less signals were emitted"
                if ok else
                f"{data.rejected_no_stop_signals} stop-less signal(s) / missing stops")
        else:
            add("no_missing_stop_loss", True, False, "stop loss not required",
                evaluated=False)

        # 8) Take profit present
        if cfg.require_take_profit:
            add("no_missing_take_profit", data.all_trades_have_take_profit, True,
                "all trades carry a take profit" if data.all_trades_have_take_profit
                else "one or more trades have no take profit")
        else:
            add("no_missing_take_profit", True, False, "take profit not required",
                evaluated=False)

        # 9) Slippage sensitivity
        self._sensitivity(add, "slippage_sensitivity", data.slippage_stress, cfg)
        # 10) Spread sensitivity
        self._sensitivity(add, "spread_sensitivity", data.spread_stress, cfg)

        # 11) Parameter-sensitivity placeholder
        add("parameter_sensitivity", True, False,
            "placeholder — parameter robustness not yet implemented", evaluated=False)
        # 12) Walk-forward placeholder
        add("walk_forward_validation", True, False,
            "placeholder — walk-forward not yet implemented", evaluated=False)

        # 13) No look-ahead flags
        no_lookahead = len(data.look_ahead_flags) == 0
        add("no_look_ahead", no_lookahead, True,
            "no look-ahead flags" if no_lookahead
            else f"look-ahead flags: {list(data.look_ahead_flags)}")

        failed = [r.name for r in results if r.blocking and r.status is RuleStatus.FAIL]
        approved = not failed
        summary = (
            f"APPROVED: passed all {len(results)} gates"
            if approved else
            f"REJECTED: {len(failed)} required gate(s) failed: {failed}"
        )
        return ValidationReport(
            strategy_id=strategy_id, approved=approved, results=results,
            failed_rules=failed, summary=summary, created_at=now,
        )

    def _sensitivity(self, add, name, stressed: Optional[BacktestMetrics], cfg) -> None:
        """A sensitivity gate: blocking iff stressed evidence was supplied."""
        if stressed is None:
            add(name, True, False, "no stressed results supplied", evaluated=False)
            return
        pf = stressed.profit_factor
        ok = stressed.net_profit > 0 and (
            pf is None or pf >= cfg.minimum_stress_profit_factor
        )
        add(name, ok, True,
            f"under stress: net profit {stressed.net_profit}, profit factor {pf}",
            observed=stressed.net_profit, threshold=0.0)


# --------------------------------------------------------------------------- #
# Optional end-to-end glue with the M10 backtester
# --------------------------------------------------------------------------- #
def stressed_cost_models(
    base: CostModel, cfg: StrategyValidationConfig
) -> tuple[CostModel, CostModel]:
    """Return (extra-slippage, wider-spread) cost models for the stress runs."""
    slippage = replace(base, slippage_points=base.slippage_points + cfg.slippage_stress_points)
    spread = replace(base, spread_fraction=base.spread_fraction * cfg.spread_stress_multiplier)
    return slippage, spread


def build_validation_input(
    candles_in,
    strategy,
    *,
    candles_out=None,
    backtest_config=None,
    validation_config: Optional[StrategyValidationConfig] = None,
    look_ahead_flags: Sequence[str] = (),
) -> ValidationInput:
    """Run in-sample, out-of-sample and stress backtests → a ``ValidationInput``.

    Uses the M10 :class:`SimpleBacktester`. Imported lazily so the validator's
    pure path has no dependency on the backtester engine.
    """
    from services.backtest_service.simple_backtester import (
        BacktestConfig,
        SimpleBacktester,
    )

    vcfg = validation_config or StrategyValidationConfig()
    bcfg = backtest_config or BacktestConfig()
    base_costs = bcfg.cost_model
    slip_costs, spread_costs = stressed_cost_models(base_costs, vcfg)

    in_report = SimpleBacktester(bcfg).run(candles_in, strategy)
    out_report = (
        SimpleBacktester(bcfg).run(candles_out, strategy) if candles_out is not None
        else None
    )
    slip_report = SimpleBacktester(replace(bcfg, cost_model=slip_costs)).run(
        candles_in, strategy)
    spread_report = SimpleBacktester(replace(bcfg, cost_model=spread_costs)).run(
        candles_in, strategy)

    return ValidationInput.from_reports(
        in_report, out_report,
        slippage_stress_report=slip_report,
        spread_stress_report=spread_report,
        look_ahead_flags=look_ahead_flags,
    )


__all__ = [
    "VALIDATOR_VERSION",
    "StrategyValidationConfig",
    "load_validation_config",
    "ValidationInput",
    "RuleStatus",
    "RuleResult",
    "ApprovalRecord",
    "ValidationReport",
    "StrategyValidator",
    "stressed_cost_models",
    "build_validation_input",
]
