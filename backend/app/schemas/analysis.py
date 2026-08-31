"""API request and response models (spec §33, §54, §55).

The response shape is deliberately **evidence-first**. Every headline number
carries the reasoning that produced it in the same object, so the frontend
cannot render a badge without also having the "why" available. Spec §55 and §69
require that explanation; putting it in a separate endpoint would make omitting
it the path of least resistance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.normalization import known_cities, known_makes

VIN_LENGTH = 17
#: I, O and Q are excluded from VINs precisely because they are confusable with
#: 1 and 0, so their presence means the input is wrong, not merely unusual.
VIN_ALLOWED = set("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")


class ManualVehicleInput(BaseModel):
    """Mode B — manual entry (spec §3)."""

    model_config = ConfigDict(extra="forbid")

    make: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=96)
    model_year: int | None = Field(default=None, ge=1900, le=2100)
    generation: str | None = Field(default=None, max_length=64)
    trim: str | None = Field(default=None, max_length=96)
    engine_code: str | None = Field(default=None, max_length=48)
    displacement: float | None = Field(default=None, gt=0, le=20_000)
    horsepower: int | None = Field(default=None, ge=1, le=2000)
    fuel: str | None = Field(default=None, max_length=32)
    transmission: str | None = Field(default=None, max_length=32)
    drivetrain: str | None = Field(default=None, max_length=32)
    body: str | None = Field(default=None, max_length=32)

    mileage_km: int | None = Field(default=None, ge=0, le=2_000_000)
    asking_price: float | None = Field(default=None, ge=0, le=100_000_000)
    currency: Literal["AZN", "USD", "EUR"] = "AZN"
    city: str | None = Field(default=None, max_length=64)
    seller_type: str | None = Field(default=None, max_length=32)
    condition: str | None = Field(default=None, max_length=32)

    owner_count: int | None = Field(default=None, ge=0, le=50)
    has_damage_disclosure: bool | None = None
    has_repaint_disclosure: bool | None = None
    service_records_provided: bool = False
    description: str | None = Field(default=None, max_length=8000)
    listing_url: str | None = Field(default=None, max_length=1024)
    vin: str | None = Field(default=None, max_length=32)

    @field_validator("vin")
    @classmethod
    def _validate_vin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned:
            return None
        if len(cleaned) != VIN_LENGTH:
            raise ValueError(f"a VIN is {VIN_LENGTH} characters; got {len(cleaned)}")
        invalid = set(cleaned) - VIN_ALLOWED
        if invalid:
            raise ValueError(
                f"VIN contains characters that cannot appear in a VIN: {''.join(sorted(invalid))}"
            )
        return cleaned

    @model_validator(mode="after")
    def _mileage_plausibility(self) -> ManualVehicleInput:
        """Catch the obvious data-entry error of mileage in metres.

        Rejected at the edge rather than absorbed, because a 900,000 km reading
        would otherwise silently dominate the mileage slope for the whole
        comparable set.
        """
        if self.mileage_km is not None and self.mileage_km > 1_500_000:
            raise ValueError("mileage above 1,500,000 km is implausible; check the units")
        return self


class VinAnalysisRequest(BaseModel):
    """Mode A — VIN (spec §3)."""

    model_config = ConfigDict(extra="forbid")

    vin: str = Field(min_length=VIN_LENGTH, max_length=VIN_LENGTH)
    mileage_km: int | None = Field(default=None, ge=0, le=2_000_000)
    asking_price: float | None = Field(default=None, ge=0)
    currency: Literal["AZN", "USD", "EUR"] = "AZN"
    city: str | None = None
    language: Literal["az", "en", "ru"] = "az"

    @field_validator("vin")
    @classmethod
    def _validate(cls, value: str) -> str:
        cleaned = value.strip().upper()
        invalid = set(cleaned) - VIN_ALLOWED
        if invalid:
            raise ValueError(
                f"VIN contains characters that cannot appear in a VIN: {''.join(sorted(invalid))}"
            )
        return cleaned


class ManualAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle: ManualVehicleInput
    language: Literal["az", "en", "ru"] = "az"
    include_narrative: bool = True


class ListingAnalysisRequest(BaseModel):
    """Mode C — listing URL (spec §3)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=1024)
    language: Literal["az", "en", "ru"] = "az"
    include_narrative: bool = True

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        """Only http(s). Blocks file://, ftp:// and similar SSRF vectors."""
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("the URL must start with http:// or https://")
        return cleaned


# --- Responses -------------------------------------------------------------


class MoneyOut(BaseModel):
    amount: float
    currency: str = "AZN"
    formatted: str


class AdjustmentOut(BaseModel):
    factor: str
    label: str
    amount_azn: float
    status: str
    explanation: str
    applied: bool
    data_points: int = 0


class ValuationOut(BaseModel):
    outcome: str
    price_basis: str
    """ASKING or TRANSACTION. Spec §9 — a figure must always say which it is."""

    central_estimate: MoneyOut | None = None
    fair_market_low: MoneyOut | None = None
    fair_market_high: MoneyOut | None = None
    raw_market_median: MoneyOut | None = None
    range_width_pct: float | None = None
    comparable_count: int = 0
    effective_sample_size: float = 0
    dispersion: float = 0
    outliers_removed: int = 0
    adjustments: list[AdjustmentOut] = []
    insufficient_reason: str | None = None
    notes: list[str] = []


class GapComponentOut(BaseModel):
    factor: str
    label: str
    amount_azn: float
    evidence: str


class GapAnalysisOut(BaseModel):
    reference_median_azn: float
    total_gap_azn: float
    explained_azn: float
    unexplained_azn: float
    explained_share: float
    components: list[GapComponentOut] = []


class PricePositionOut(BaseModel):
    rating: str
    rating_label: str
    asking_price: MoneyOut | None = None
    difference_azn: float | None = None
    difference_pct: float | None = None
    percentile: float | None = None
    within_fair_range: bool | None = None
    rationale: list[str] = []
    """The "why?" behind the badge. Never rendered without it (spec §55)."""

    gap_analysis: GapAnalysisOut | None = None


class RiskSignalOut(BaseModel):
    type: str
    severity: str
    title: str
    evidence: list[str]
    interpretation: str
    recommended_verification: str
    source: str
    confidence: float
    evidence_strength: str


class RiskContributionOut(BaseModel):
    title: str
    severity: str
    marginal_points: float


class RiskOut(BaseModel):
    score: int
    band: str
    band_label: str
    signals: list[RiskSignalOut] = []
    positives: list[dict[str, Any]] = []
    contributions: list[RiskContributionOut] = []
    verification_actions: list[str] = []


class ConfidenceComponentOut(BaseModel):
    name: str
    label: str
    score: float
    weight: float
    contribution_points: float
    explanation: str


class ConfidenceOut(BaseModel):
    score_percent: int
    band: str
    calibrated: bool
    """False until interval coverage is validated on held-out data. The UI must
    not present an uncalibrated score as a probability (audit §7.6)."""

    components: list[ConfidenceComponentOut] = []
    limiting_factors: list[str] = []
    improvements: list[str] = []


class ComparableOut(BaseModel):
    listing_id: str
    price: MoneyOut
    mileage_km: int | None = None
    model_year: int | None = None
    trim: str | None = None
    city: str | None = None
    similarity: float
    tier: int
    differences: list[str] = []
    source_url: str | None = None


class DistributionOut(BaseModel):
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    sample_size: int


class MarketContextOut(BaseModel):
    comparable_count: int
    effective_sample_size: float
    match_level: str
    search_widened: bool
    mean_similarity: float
    asking_price_distribution: DistributionOut | None = None
    adjusted_price_distribution: DistributionOut | None = None


class NegotiationOut(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    posture: str = ""
    opening_offer: MoneyOut | None = None
    target_low: MoneyOut | None = None
    target_high: MoneyOut | None = None
    walk_away_above: MoneyOut | None = None
    observed_market_reduction_pct: float | None = None
    reduction_sample_size: int = 0
    leverage: list[dict[str, Any]] = []
    rationale: list[str] = []


class SellerQuestionOut(BaseModel):
    question: str
    why: str
    priority: str
    triggered_by: str


class InspectionItemOut(BaseModel):
    item: str
    priority: str
    system: str
    reason: str
    triggered_by: str


class VehicleOut(BaseModel):
    description: str
    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    generation: str | None = None
    trim: str | None = None
    fuel: str
    transmission: str
    drivetrain: str
    body: str
    engine_displacement_l: float | None = None
    horsepower: int | None = None
    configuration_id: str
    specificity: float
    unknown_attributes: list[str] = []
    mileage_km: int | None = None
    city: str | None = None
    region: str | None = None
    vin_provided: bool = False


class NarrativeOut(BaseModel):
    generated_by: str
    """``openai`` or ``fallback``. The client labels AI-written prose as such."""

    is_ai_generated: bool
    degraded_reason: str | None = None
    vehicle_summary: str = ""
    market_context: str = ""
    price_explanation: str = ""
    final_assessment: str = ""
    positive_signals: list[dict[str, str]] = []
    risk_signals: list[dict[str, str]] = []
    limitations: list[str] = []


#: Spec §59. Carried in every analysis response so no client can render a
#: report without it.
DISCLAIMER = (
    "This report is an evidence-based market and vehicle-information analysis. It does "
    "not guarantee the mechanical condition, accident history, legal status or future "
    "reliability of the vehicle. An independent inspection and appropriate "
    "vehicle-history verification are recommended before purchase."
)


class AnalysisResponse(BaseModel):
    """The full analysis (spec §34, §54)."""

    analysis_id: str
    generated_at: datetime
    vehicle: VehicleOut
    valuation: ValuationOut
    price_position: PricePositionOut
    risk: RiskOut
    confidence: ConfidenceOut
    market: MarketContextOut
    comparables: list[ComparableOut] = []
    negotiation: NegotiationOut
    seller_questions: list[SellerQuestionOut] = []
    inspection_priorities: list[InspectionItemOut] = []
    candidate_explanations: list[str] = []
    limitations: list[str] = []
    narrative: NarrativeOut | None = None
    disclaimer: str = DISCLAIMER



class ReferenceDataResponse(BaseModel):
    """Vocabulary the UI needs for dropdowns and client-side validation."""

    makes: list[str] = Field(default_factory=known_makes)
    cities: list[str] = Field(default_factory=known_cities)
    fuels: list[str] = []
    transmissions: list[str] = []
    drivetrains: list[str] = []
    bodies: list[str] = []


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
    reasoning: str
    ingestion: str
    version: str = "0.1.0"


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    request_id: str | None = None
