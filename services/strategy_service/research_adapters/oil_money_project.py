"""Oil Money Project research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Oil Money Project: study
how a petro-currency (e.g. CAD, NOK, RUB) co-moves with crude oil. Given the FX
close series and an oil price series, we regress FX returns on oil returns and
report the sensitivity (beta), correlation, and fit.

**Applicability: research_only.** This is a quantamental relationship study and
needs an external oil dataset; it informs bias/context, it is not a trade signal.
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


class OilMoneyProjectAdapter(ResearchAdapter):
    """Petro-currency vs crude-oil sensitivity study (research only)."""

    VERSION = "1.0.0"

    def __init__(self, min_obs: int = 30) -> None:
        self.min_obs = min_obs

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="oil_money_project",
            version=self.VERSION,
            source_strategy="Oil Money Project",
            category="quantamental",
            description="Petro-currency vs crude-oil regression (sensitivity).",
            output_type=OutputType.REPORT,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=[
                "petro-currency FX OHLC (e.g. USDCAD, USDNOK)",
                "crude oil price series (e.g. WTI/Brent)",
            ],
            supported_asset_classes=["forex", "commodity"],
            reason=(
                "Research only: a quantamental co-movement study needing an "
                "external oil dataset; informs bias, not a trade signal."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        fx = c.to_close_series(c.pick(inputs, "fx_candles", "fx_prices"))
        oil = c.to_close_series(c.pick(inputs, "oil_prices", "oil_candles"))
        if fx is None or oil is None:
            return self.requirements_report("need FX close series and oil price series")
        fx, oil = c.align(fx, oil)
        if len(fx) < self.min_obs:
            return self.requirements_report(
                f"need >= {self.min_obs} aligned observations, have {len(fx)}"
            )

        fx_r = c.log_returns(fx).reset_index(drop=True)
        oil_r = c.log_returns(oil).reset_index(drop=True)
        fx_r, oil_r = c.align(fx_r, oil_r)
        beta, alpha = c.ols_beta(fx_r, oil_r)
        corr = float(np.corrcoef(fx_r, oil_r)[0, 1])
        data = {
            "oil_beta": round(beta, 6),
            "correlation": round(corr, 4),
            "observations": int(len(fx_r)),
        }
        return self.make_output(
            summary=(
                f"FX/oil sensitivity: beta={beta:.3f}, corr={corr:.2f}. "
                "Context for a petro-currency bias; not a trade signal."
            ),
            data=data,
            risk_notes=["relationship is regime-dependent and can decouple"],
        )


__all__ = ["OilMoneyProjectAdapter"]
