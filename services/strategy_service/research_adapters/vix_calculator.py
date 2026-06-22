"""VIX Calculator research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` VIX Calculator. The true
VIX is the implied volatility of a strip of index options. Without an options
chain we instead compute a **realized-volatility proxy** (annualised std of log
returns) from OHLC — a usable volatility *feature* for risk sizing — and are
explicit that it is a proxy, not the option-implied VIX.

**Applicability: research_only.** A volatility number is a risk/feature input,
not a trade direction; the genuine VIX additionally needs an index option chain.
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


class VIXCalculatorAdapter(ResearchAdapter):
    """Realized-volatility proxy as a VIX-like feature (research only)."""

    VERSION = "1.0.0"

    def __init__(self, min_obs: int = 20, periods_per_year: int = 252) -> None:
        self.min_obs = min_obs
        self.periods_per_year = periods_per_year

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="vix_calculator",
            version=self.VERSION,
            source_strategy="VIX Calculator",
            category="options",
            description="Realized-volatility proxy (VIX-like volatility feature).",
            output_type=OutputType.FEATURE,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=[
                "index option chain (for the true implied VIX)",
                "OHLC close series (for the realized-vol proxy)",
            ],
            supported_asset_classes=c.ASSET_CLASSES,
            reason=(
                "Research only: a volatility feature/risk input, not a trade "
                "direction; the true VIX needs an index option chain."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        close = c.to_close_series(c.pick(inputs, "candles", "prices"))
        if close is None or len(close) < self.min_obs:
            return self.requirements_report(
                f"need >= {self.min_obs} closes for a realized-vol proxy"
            )
        rets = c.log_returns(close)
        ann_vol = c.annualize_vol(rets, self.periods_per_year)
        data = {
            "realized_vol_annualized": round(ann_vol, 6),
            "vix_like_points": round(ann_vol * 100.0, 4),
            "observations": int(len(rets)),
            "is_proxy": True,
        }
        return self.make_output(
            summary=(
                f"Realized-volatility proxy ≈ {ann_vol * 100:.1f} 'VIX points' "
                f"(annualised). Proxy only — not option-implied VIX."
            ),
            data=data,
            risk_notes=["proxy from price action; not the option-implied VIX"],
        )


__all__ = ["VIXCalculatorAdapter"]
