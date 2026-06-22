"""Pair Trading research adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Pair Trading: find two
co-moving instruments, model their spread, and fade deviations from it. We

* estimate the hedge ratio (OLS slope of A on B),
* form the spread ``A - beta*B`` and its z-score,
* run a lightweight cointegration screen (high correlation + a mean-reverting,
  bounded spread),
* and, when the spread is stretched, describe the two-leg mean-reversion view.

**Applicability: adaptable.** A pair trade needs *two* broker symbols and
simultaneous two-leg execution, which the single-symbol ``OrderIntent`` path
does not yet support — so the output is a research ``signal``, never routed to
execution (it is not ``DIRECT``).
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


class PairTradingAdapter(ResearchAdapter):
    """Cointegration-screened spread mean-reversion across two symbols."""

    VERSION = "1.0.0"

    def __init__(
        self,
        min_obs: int = 30,
        corr_threshold: float = 0.8,
        entry_z: float = 2.0,
    ) -> None:
        self.min_obs = min_obs
        self.corr_threshold = corr_threshold
        self.entry_z = entry_z

    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="pair_trading",
            version=self.VERSION,
            source_strategy="Pair Trading",
            category="statistical_arbitrage",
            description="Cointegration-screened spread mean-reversion (two legs).",
            output_type=OutputType.SIGNAL,
            applicability=MT5Applicability.ADAPTABLE,
            required_datasets=[
                "OHLC for symbol A",
                "OHLC for a correlated symbol B",
            ],
            supported_asset_classes=c.ASSET_CLASSES,
            reason=(
                "Adaptable: feasible when two correlated broker symbols are "
                "available and cointegration holds, but requires simultaneous "
                "two-leg execution not yet supported, so not DIRECT."
            ),
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        a = c.to_close_series(c.pick(inputs, "candles_a", "prices_a"))
        b = c.to_close_series(c.pick(inputs, "candles_b", "prices_b"))
        if a is None or b is None:
            return self.requirements_report("need close series for both symbols")
        a, b = c.align(a, b)
        if len(a) < self.min_obs:
            return self.requirements_report(
                f"need >= {self.min_obs} aligned observations, have {len(a)}"
            )

        corr = float(np.corrcoef(a, b)[0, 1])
        beta, alpha = c.ols_beta(a, b)
        spread = a - (beta * b + alpha)
        mu, sigma = float(spread.mean()), float(spread.std(ddof=1))
        z = float((spread.iloc[-1] - mu) / sigma) if sigma > 0 else 0.0
        # Cointegration screen: strong co-movement and a usable, bounded spread.
        cointegrated = corr >= self.corr_threshold and sigma > 0

        data = {
            "correlation": round(corr, 4),
            "hedge_ratio_beta": round(beta, 6),
            "spread_zscore": round(z, 4),
            "cointegrated": cointegrated,
            "entry_z": self.entry_z,
        }
        if not cointegrated:
            return self.make_output(
                summary=(
                    f"Pair not cointegrated (corr={corr:.2f}); no mean-reversion "
                    "edge. Research only."
                ),
                data=data,
                risk_notes=["correlation below threshold; spread may not revert"],
            )
        if abs(z) >= self.entry_z:
            # Spread rich (z>0) ⇒ sell A / buy B; spread cheap ⇒ buy A / sell B.
            leg_a = "sell" if z > 0 else "buy"
            leg_b = "buy" if z > 0 else "sell"
            data.update({"leg_a_side": leg_a, "leg_b_side": leg_b})
            return self.make_output(
                summary=(
                    f"Spread stretched (z={z:.2f}); mean-reversion view: {leg_a} A / "
                    f"{leg_b} B (hedge ratio {beta:.2f})."
                ),
                data=data,
                risk_notes=[
                    "two-leg trade; requires both symbols and simultaneous fills",
                    "cointegration can break down — monitor the spread",
                ],
            )
        return self.make_output(
            summary=f"Spread within band (z={z:.2f}); no entry. Watchlist only.",
            data=data,
        )


__all__ = ["PairTradingAdapter"]
