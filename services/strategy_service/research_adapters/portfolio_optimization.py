"""Portfolio Optimization Project research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Portfolio Optimization:
allocate capital across assets by their risk. Given the return series of two or
more assets we compute long-only **inverse-variance** weights (a robust,
deterministic risk-based allocation) and return them as a ranking.

**Applicability: research_only.** Per the milestone, this is a *capital
allocation* research module — a sizing/ranking aid — not a per-symbol trade
signal, so it never reaches execution.
"""
from __future__ import annotations

from typing import Any

from services.strategy_service.research_adapters import _common as c
from services.strategy_service.research_adapters.base import (
    MT5Applicability,
    OutputType,
    ResearchAdapter,
    ResearchAdapterMetadata,
    ResearchOutput,
)


class PortfolioOptimizationAdapter(ResearchAdapter):
    """Risk-based (inverse-variance) capital allocation ranking (research only)."""

    VERSION = "1.0.0"

    def __init__(self, min_obs: int = 20) -> None:
        self.min_obs = min_obs

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="portfolio_optimization",
            version=self.VERSION,
            source_strategy="Portfolio Optimization Project",
            category="portfolio",
            description="Inverse-variance capital allocation across assets.",
            output_type=OutputType.RANKING,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=["return/close series for >= 2 assets"],
            supported_asset_classes=c.ASSET_CLASSES,
            reason=(
                "Research only: a capital-allocation/sizing aid (weights), not a "
                "per-symbol trade signal; allocation feeds sizing, never execution."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        assets = inputs.get("assets")  # {symbol: candles/prices}
        if not isinstance(assets, dict) or len(assets) < 2:
            return self.requirements_report("need a mapping of >= 2 assets to series")

        variances: dict[str, float] = {}
        for symbol, raw in assets.items():
            s = c.to_close_series(raw)
            if s is None or len(s) < self.min_obs:
                continue
            var = float(c.log_returns(s).var(ddof=1))
            if var > 0:
                variances[symbol] = var
        if len(variances) < 2:
            return self.requirements_report(
                f"need >= 2 assets with >= {self.min_obs} observations and non-zero variance"
            )

        inv = {sym: 1.0 / var for sym, var in variances.items()}
        total = sum(inv.values())
        weights = {sym: round(v / total, 6) for sym, v in inv.items()}
        ranked = dict(sorted(weights.items(), key=lambda kv: kv[1], reverse=True))
        top = next(iter(ranked))
        return self.make_output(
            summary=(
                f"Inverse-variance allocation across {len(ranked)} assets; "
                f"largest weight: {top} ({ranked[top]:.0%}). Allocation research only."
            ),
            data={"weights": ranked, "method": "inverse_variance"},
            risk_notes=[
                "allocation/sizing aid only — not a trade signal",
                "diagonal (variance-only) approximation; ignores cross-correlations",
            ],
        )


__all__ = ["PortfolioOptimizationAdapter"]
