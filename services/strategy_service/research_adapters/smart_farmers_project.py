"""Smart Farmers Project research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Smart Farmers Project: a
quantamental study of agricultural commodities (weather/seasonality-driven price
behaviour). With one or more commodity price series we report a simple momentum
read (trailing return) per commodity as a starting point; the full study needs
weather/seasonal datasets.

**Applicability: research_only.** Agricultural commodities are outside our spot
forex/gold/crypto execution universe; this is a research/feature tool.
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


class SmartFarmersProjectAdapter(ResearchAdapter):
    """Agricultural-commodity momentum/seasonality study (research only)."""

    VERSION = "1.0.0"

    def __init__(self, min_obs: int = 20) -> None:
        self.min_obs = min_obs

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="smart_farmers_project",
            version=self.VERSION,
            source_strategy="Smart Farmers Project",
            category="quantamental",
            description="Agricultural-commodity momentum/seasonality research.",
            output_type=OutputType.REPORT,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=[
                "agricultural commodity price series (e.g. wheat, corn, soybean)",
                "weather / seasonal data (for the full study)",
            ],
            supported_asset_classes=["commodity"],
            reason=(
                "Research only: agricultural commodities are outside the spot "
                "MT5 forex/gold/crypto universe; a research/feature tool."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        prices = inputs.get("commodity_prices")
        # Accept either a single series or a {name: series} mapping.
        series_map: dict[str, Any] = {}
        if isinstance(prices, dict):
            series_map = prices
        elif prices is not None:
            series_map = {"commodity": prices}

        momentum: dict[str, float] = {}
        for name, raw in series_map.items():
            s = c.to_close_series(raw)
            if s is None or len(s) < self.min_obs:
                continue
            momentum[name] = round(float(s.iloc[-1] / s.iloc[0] - 1.0), 6)

        if not momentum:
            return self.requirements_report(
                f"need >= {self.min_obs} prices for at least one commodity"
            )
        ranked = dict(sorted(momentum.items(), key=lambda kv: kv[1], reverse=True))
        leader = next(iter(ranked))
        return self.make_output(
            summary=(
                f"Agricultural momentum read across {len(ranked)} series; "
                f"strongest: {leader} ({ranked[leader]:+.1%}). Research context only."
            ),
            data={"trailing_return": ranked},
            risk_notes=[
                "momentum only; full study needs weather/seasonal data",
                "not in the executable instrument universe",
            ],
        )


__all__ = ["SmartFarmersProjectAdapter"]
