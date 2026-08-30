"""Response shape for budget-matched recommendations."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class BudgetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_azn: Decimal
    high_azn: Decimal
    centre_azn: Decimal
    observations: int
    source: str
    """``history`` when inferred from what the person looked at, ``stated``
    when they said it. A client should not present the two identically."""


class RecommendationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: int
    source_url: str | None
    make: str | None
    model: str | None
    model_year: int | None
    city: str | None
    mileage_km: int | None
    price_azn: Decimal
    median_azn: Decimal | None
    vs_median_pct: float | None


class DiscoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: BudgetOut | None
    observations_needed: int
    recommendations: list[RecommendationOut]
    note: str
