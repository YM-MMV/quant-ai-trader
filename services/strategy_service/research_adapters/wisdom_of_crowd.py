"""Wisdom of Crowd Project research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Wisdom of Crowd Project:
aggregate many independent forecasts/votes into a consensus that is often better
than any single one. Given a set of per-source scores for one or more symbols we
average them into a consensus ranking and report agreement (dispersion).

**Applicability: research_only.** Consensus is a ranking/feature derived from
external forecasts; it is not, by itself, an executable per-symbol signal.
"""
from __future__ import annotations

from statistics import fmean, pstdev
from typing import Any

from services.strategy_service.research_adapters import _common as c
from services.strategy_service.research_adapters.base import (
    MT5Applicability,
    OutputType,
    ResearchAdapter,
    ResearchAdapterMetadata,
    ResearchOutput,
)


class WisdomOfCrowdAdapter(ResearchAdapter):
    """Consensus aggregation of multiple forecasts into a ranking (research only)."""

    VERSION = "1.0.0"

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="wisdom_of_crowd",
            version=self.VERSION,
            source_strategy="Wisdom of Crowd Project",
            category="quantamental",
            description="Consensus aggregation of multiple forecasts (ranking).",
            output_type=OutputType.RANKING,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=[
                "multiple independent forecasts/votes per symbol "
                "(analysts, models, sentiment)"
            ],
            supported_asset_classes=c.ASSET_CLASSES,
            reason=(
                "Research only: a consensus ranking/feature derived from external "
                "forecasts, not an executable per-symbol signal on its own."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        # forecasts: {symbol: [score, score, ...]}  (scores are directional, e.g. -1..1)
        forecasts = inputs.get("forecasts")
        if not isinstance(forecasts, dict) or not forecasts:
            return self.requirements_report("need a mapping of symbol -> list of scores")

        consensus: dict[str, float] = {}
        agreement: dict[str, float] = {}
        for symbol, scores in forecasts.items():
            vals = [float(s) for s in scores] if scores else []
            if len(vals) < 2:
                continue
            consensus[symbol] = round(fmean(vals), 6)
            # Lower dispersion ⇒ higher agreement.
            consensus_std = pstdev(vals)
            agreement[symbol] = round(1.0 / (1.0 + consensus_std), 4)

        if not consensus:
            return self.requirements_report("need >= 2 forecasts for at least one symbol")
        ranked = dict(sorted(consensus.items(), key=lambda kv: kv[1], reverse=True))
        top = next(iter(ranked))
        return self.make_output(
            summary=(
                f"Consensus across {len(ranked)} symbol(s); strongest view: {top} "
                f"({ranked[top]:+.2f}). Ranking/research only."
            ),
            data={"consensus": ranked, "agreement": agreement},
            risk_notes=[
                "consensus quality depends on independent, unbiased sources",
                "ranking/feature only — not an executable signal",
            ],
        )


__all__ = ["WisdomOfCrowdAdapter"]
