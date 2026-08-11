from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.app.domain.canonical import load_yaml
from backend.app.settings import REPOSITORY_ROOT


class _TokenPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Decimal
    output_reasoning: Decimal


class _EmbeddingPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    online_per_1000_input_tokens: Decimal
    batch_per_1000_input_tokens: Decimal


class _PricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    effective_date: date
    currency: str
    source: str
    standard_paygo_global_per_million_tokens: dict[str, _TokenPrice]
    batch_global_per_million_tokens: dict[str, _TokenPrice]
    embedding: dict[str, _EmbeddingPrice]


@dataclass(frozen=True)
class PricingEstimator:
    config: _PricingConfig

    @classmethod
    def from_path(cls, path: Path) -> PricingEstimator:
        return cls(_PricingConfig.model_validate(load_yaml(path)))

    def generation_cost(
        self,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        batch: bool = False,
    ) -> Decimal:
        prices = (
            self.config.batch_global_per_million_tokens
            if batch
            else self.config.standard_paygo_global_per_million_tokens
        )
        if model_id not in prices:
            raise ValueError(f"no configured generation price for {model_id}")
        price = prices[model_id]
        return (
            Decimal(input_tokens) * price.input
            + Decimal(output_tokens + reasoning_tokens) * price.output_reasoning
        ) / Decimal(1_000_000)

    def reserved_generation_cost(
        self, model_id: str, *, estimated_input_tokens: int, max_output_tokens: int
    ) -> Decimal:
        return self.generation_cost(
            model_id,
            input_tokens=estimated_input_tokens,
            output_tokens=max_output_tokens,
        )


def default_pricing_estimator() -> PricingEstimator:
    return PricingEstimator.from_path(REPOSITORY_ROOT / "config" / "pricing.yaml")
