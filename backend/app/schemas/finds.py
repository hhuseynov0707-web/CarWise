"""Response shape for listings priced below their configuration's market."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class FindOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: int
    config_id: str | None
    source_url: str | None
    make: str | None
    model: str | None
    model_year: int | None
    city: str | None
    mileage_km: int | None
    price_azn: Decimal

    median_azn: Decimal
    below_median_pct: float
    sample_size: int
    dispersion: float | None
    median_mileage_km: int | None
    mileage_vs_median_pct: float | None


class FindsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_from_snapshot: bool
    window_days: int
    min_sample_size: int
    finds: list[FindOut]

    caveat: str
    """Travels with the data rather than living in the UI, so that any client
    rendering these numbers also has the sentence that qualifies them."""
