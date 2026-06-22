"""Monte Carlo Project research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Monte Carlo Project:
simulate many future price paths to characterise the distribution of outcomes.
We fit a geometric-Brownian-motion model to the historical log returns and run a
**seeded** (deterministic) simulation to produce risk context: Value-at-Risk,
expected terminal price, and the probability of a loss over the horizon.

**Applicability: research_only.** This is a risk/scenario tool, not a trade
direction — its output is ``risk_context`` for sizing and stress analysis.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from services.strategy_service.research_adapters import _common as c
from services.strategy_service.research_adapters.base import (
    MT5Applicability,
    OutputType,
    ResearchAdapter,
    ResearchAdapterMetadata,
    ResearchOutput,
)


class MonteCarloProjectAdapter(ResearchAdapter):
    """Seeded GBM Monte-Carlo simulation → VaR / scenario risk context."""

    VERSION = "1.0.0"

    def __init__(
        self,
        min_obs: int = 30,
        n_paths: int = 10_000,
        horizon: int = 20,
        seed: int = 7,
    ) -> None:
        self.min_obs = min_obs
        self.n_paths = n_paths
        self.horizon = horizon
        self.seed = seed

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="monte_carlo_project",
            version=self.VERSION,
            source_strategy="Monte Carlo Project",
            category="simulation",
            description="Seeded GBM Monte-Carlo path simulation (VaR / scenarios).",
            output_type=OutputType.RISK_CONTEXT,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=["historical OHLC close series (for returns)"],
            supported_asset_classes=c.ASSET_CLASSES,
            reason=(
                "Research only: a scenario/risk tool (VaR, terminal distribution), "
                "not a trade direction."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        close = c.to_close_series(c.pick(inputs, "candles", "prices"))
        if close is None or len(close) < self.min_obs:
            return self.requirements_report(
                f"need >= {self.min_obs} closes to fit a return model"
            )
        rets = c.log_returns(close)
        mu, sigma = float(rets.mean()), float(rets.std(ddof=1))
        s0 = float(close.iloc[-1])

        rng = np.random.default_rng(self.seed)  # deterministic
        # Sum of `horizon` iid normal log-returns per path.
        shocks = rng.normal(mu, sigma, size=(self.n_paths, self.horizon)).sum(axis=1)
        terminal = s0 * np.exp(shocks)
        pnl = terminal - s0

        data = {
            "spot": round(s0, 6),
            "horizon": self.horizon,
            "n_paths": self.n_paths,
            "expected_terminal": round(float(terminal.mean()), 6),
            "var_95": round(float(-np.percentile(pnl, 5)), 6),
            "var_99": round(float(-np.percentile(pnl, 1)), 6),
            "prob_loss": round(float((pnl < 0).mean()), 4),
            "seed": self.seed,
        }
        return self.make_output(
            summary=(
                f"Monte-Carlo ({self.n_paths} paths, {self.horizon} bars): "
                f"95% VaR {data['var_95']:g}, P(loss) {data['prob_loss']:.0%}."
            ),
            data=data,
            risk_notes=[
                "GBM assumes iid normal returns — tails are underestimated",
                "scenario/risk context only; not a trade signal",
            ],
        )


__all__ = ["MonteCarloProjectAdapter"]
