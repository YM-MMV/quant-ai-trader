"""NullAdapter — the canonical empty adapter.

It validates inputs like any adapter but always abstains (``NONE``). It serves
two purposes:

* a reference/template for new adapters, and
* proof that *empty* adapters can be registered and run safely — useful as a
  placeholder while a strategy is still being ported.
"""
from __future__ import annotations

from typing import Any, Optional

from services.strategy_service.base import AdapterMetadata, AdapterSignal, StrategyAdapter


class NullAdapter(StrategyAdapter):
    """An adapter that always returns a ``NONE`` signal."""

    VERSION = "0.1.0"

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="null",
            version=self.VERSION,
            source_repo_url="",
            source_strategy="null",
            description="Placeholder adapter that always abstains.",
            category="placeholder",
            supported_symbols=None,   # any
            supported_timeframes=None,  # any
            min_candles=1,
        )

    def _compute_signal(
        self,
        candles: Any,
        features: Any,
        kronos_prediction: Optional[Any],
    ) -> AdapterSignal:
        return self.none_signal("null adapter always abstains")


__all__ = ["NullAdapter"]
