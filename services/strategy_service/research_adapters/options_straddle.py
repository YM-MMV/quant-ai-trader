"""Options Straddle research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Options Straddle: a
long straddle (buy a call + a put at the same strike) profits from a large move
in either direction. Given a strike and the two premiums we report the cost and
the upper/lower break-evens.

**Applicability: not_applicable.** Our MT5 universe is spot forex/gold/crypto —
there is no options chain to buy a straddle on. This is a pricing/education
report, never an executable signal.
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


class OptionsStraddleAdapter(ResearchAdapter):
    """Long-straddle cost / break-even analysis (report only)."""

    VERSION = "1.0.0"

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="options_straddle",
            version=self.VERSION,
            source_strategy="Options Straddle",
            category="options",
            description="Long straddle cost and break-even analysis.",
            output_type=OutputType.REPORT,
            applicability=MT5Applicability.NOT_APPLICABLE,
            required_datasets=[
                "option chain: strike, call premium, put premium",
                "expiry and implied volatility (for richer analysis)",
            ],
            supported_asset_classes=["options"],
            reason=(
                "Not applicable: spot MT5 forex/gold/crypto has no options chain "
                "to construct a straddle; analysis only."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        strike = inputs.get("strike")
        call = inputs.get("call_premium")
        put = inputs.get("put_premium")
        if strike is None or call is None or put is None:
            return self.requirements_report("need strike, call_premium, put_premium")

        strike, call, put = float(strike), float(call), float(put)
        cost = call + put
        data = {
            "strike": strike,
            "total_premium": round(cost, 6),
            "upper_breakeven": round(strike + cost, 6),
            "lower_breakeven": round(strike - cost, 6),
            "max_loss": round(cost, 6),  # debit paid, if held to expiry at strike
        }
        return self.make_output(
            summary=(
                f"Long straddle @ {strike:g}: cost {cost:g}, break-even "
                f"{data['lower_breakeven']:g} / {data['upper_breakeven']:g}. "
                "Profits on a large move either way."
            ),
            data=data,
            risk_notes=[
                "no options on spot MT5 — analysis only, not executable",
                "max loss is the debit paid; time decay works against the holder",
            ],
        )


__all__ = ["OptionsStraddleAdapter"]
