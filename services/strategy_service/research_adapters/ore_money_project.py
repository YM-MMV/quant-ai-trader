"""Ore Money Project research adapter.

The metal/ore counterpart to the Oil Money Project (this project name appears in
the quant-trading knowledge base though it is not in the curated inventory
baseline — included here so it is not silently dropped). It studies how a
commodity currency tied to metals/ore (e.g. AUD ↔ iron ore) co-moves with the
underlying commodity, via a returns regression.

**Applicability: research_only.** A quantamental relationship study requiring an
external ore/metal dataset; it informs bias/context, not a trade direction.
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


class OreMoneyProjectAdapter(ResearchAdapter):
    """Commodity-currency vs metal/ore sensitivity study (research only)."""

    VERSION = "1.0.0"

    def __init__(self, min_obs: int = 30) -> None:
        self.min_obs = min_obs

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="ore_money_project",
            version=self.VERSION,
            source_strategy="Ore Money Project",
            category="quantamental",
            description="Commodity-currency vs metal/ore regression (sensitivity).",
            output_type=OutputType.REPORT,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=[
                "commodity-currency FX OHLC (e.g. AUDUSD)",
                "metal/ore price series (e.g. iron ore, copper)",
            ],
            supported_asset_classes=["forex", "metal", "commodity"],
            reason=(
                "Research only: a quantamental co-movement study needing an "
                "external ore/metal dataset; informs bias, not a trade signal."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        fx = c.to_close_series(c.pick(inputs, "fx_candles", "fx_prices"))
        ore = c.to_close_series(c.pick(inputs, "ore_prices", "ore_candles"))
        if fx is None or ore is None:
            return self.requirements_report("need FX close series and ore price series")
        fx, ore = c.align(fx, ore)
        if len(fx) < self.min_obs:
            return self.requirements_report(
                f"need >= {self.min_obs} aligned observations, have {len(fx)}"
            )

        fx_r = c.log_returns(fx).reset_index(drop=True)
        ore_r = c.log_returns(ore).reset_index(drop=True)
        fx_r, ore_r = c.align(fx_r, ore_r)
        beta, alpha = c.ols_beta(fx_r, ore_r)
        corr = float(np.corrcoef(fx_r, ore_r)[0, 1])
        data = {
            "ore_beta": round(beta, 6),
            "correlation": round(corr, 4),
            "observations": int(len(fx_r)),
        }
        return self.make_output(
            summary=(
                f"FX/ore sensitivity: beta={beta:.3f}, corr={corr:.2f}. "
                "Context for a commodity-currency bias; not a trade signal."
            ),
            data=data,
            risk_notes=["relationship is regime-dependent and can decouple"],
        )


__all__ = ["OreMoneyProjectAdapter"]
