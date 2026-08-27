"""Translation from analysis results to API responses.

Kept separate from the routes so that the wire format can change without
touching request handling, and so that a route stays a handful of lines. This
module contains no computation — every value is copied from an engine result.
"""

from __future__ import annotations

from app.adapters.llm.service import NarrativeResult
from app.domain.money import Money
from app.engines.evidence.bundle import AnalysisResult
from app.engines.stats import QuantileSet
from app.schemas.analysis import (
    AdjustmentOut,
    AnalysisResponse,
    ComparableOut,
    ConfidenceComponentOut,
    ConfidenceOut,
    DistributionOut,
    GapAnalysisOut,
    GapComponentOut,
    InspectionItemOut,
    MarketContextOut,
    MoneyOut,
    NarrativeOut,
    NegotiationOut,
    PricePositionOut,
    RiskContributionOut,
    RiskOut,
    RiskSignalOut,
    SellerQuestionOut,
    ValuationOut,
    VehicleOut,
)

#: Display names for valuation factors, shared with the gap table so the same
#: factor never appears under two different labels in one report.
FACTOR_LABELS = {
    "mileage": "Mileage",
    "age": "Model year",
    "geography": "Location",
    "condition_disclosure": "Disclosed condition",
    "seasonality": "Seasonality",
    "market_demand": "Supply and demand",
}

#: How many comparables to return. Enough to show the market, few enough to keep
#: the payload small on mobile connections.
COMPARABLE_LIMIT = 20


def money_out(value: Money | None) -> MoneyOut | None:
    if value is None:
        return None
    return MoneyOut(
        amount=value.as_float(), currency=value.currency.value, formatted=value.format()
    )


def distribution_out(quantiles: QuantileSet | None) -> DistributionOut | None:
    if quantiles is None:
        return None
    return DistributionOut(**quantiles.as_dict())  # type: ignore[arg-type]


def to_response(
    result: AnalysisResult,
    narrative: NarrativeResult | None = None,
) -> AnalysisResponse:
    valuation = result.valuation
    position = result.position
    gap = position.gap_analysis

    return AnalysisResponse(
        analysis_id=result.analysis_id,
        generated_at=result.generated_at,
        vehicle=_vehicle(result),
        valuation=ValuationOut(
            outcome=valuation.outcome.value,
            price_basis=valuation.basis.value,
            central_estimate=money_out(valuation.central_estimate),
            fair_market_low=money_out(valuation.fair_market_low),
            fair_market_high=money_out(valuation.fair_market_high),
            raw_market_median=money_out(valuation.raw_market_median),
            range_width_pct=valuation.range_width_pct,
            comparable_count=valuation.comparable_count,
            effective_sample_size=valuation.effective_sample_size,
            dispersion=valuation.dispersion,
            outliers_removed=valuation.outliers_removed,
            adjustments=[
                AdjustmentOut(
                    factor=a.name,
                    label=FACTOR_LABELS.get(a.name, a.name.replace("_", " ").title()),
                    amount_azn=a.amount_azn,
                    status=a.reason.value,
                    explanation=a.explanation,
                    applied=a.applied,
                    data_points=a.data_points,
                )
                for a in valuation.adjustments
            ],
            insufficient_reason=valuation.insufficient_reason,
            notes=list(valuation.notes),
        ),
        price_position=PricePositionOut(
            rating=position.rating.value,
            rating_label=position.label,
            asking_price=money_out(position.asking_price),
            difference_azn=position.difference_azn,
            difference_pct=position.difference_pct,
            percentile=position.percentile,
            within_fair_range=position.within_range,
            rationale=list(position.rationale),
            gap_analysis=(
                GapAnalysisOut(
                    reference_median_azn=gap.reference_median_azn,
                    total_gap_azn=gap.total_gap_azn,
                    explained_azn=gap.explained_azn,
                    unexplained_azn=gap.unexplained_azn,
                    explained_share=round(gap.explained_share, 3),
                    components=[
                        GapComponentOut(
                            factor=c.factor,
                            label=c.label,
                            amount_azn=c.amount_azn,
                            evidence=c.evidence,
                        )
                        for c in gap.components
                    ],
                )
                if gap
                else None
            ),
        ),
        risk=RiskOut(
            score=result.risk.score,
            band=result.risk.band,
            band_label=result.risk.band_label,
            signals=[
                RiskSignalOut(
                    type=s.risk_type.value,
                    severity=s.severity.value,
                    title=s.title,
                    evidence=list(s.evidence),
                    interpretation=s.interpretation,
                    recommended_verification=s.recommended_verification,
                    source=s.source,
                    confidence=s.confidence,
                    evidence_strength=s.strength.value,
                )
                for s in result.risk.by_severity()
            ],
            positives=[
                {"title": p.title, "evidence": list(p.evidence), "source": p.source}
                for p in result.risk.positives
            ],
            contributions=[
                RiskContributionOut(
                    title=c.title,
                    severity=c.severity.value,
                    marginal_points=c.marginal_points,
                )
                for c in result.risk.contributions
            ],
            verification_actions=list(result.risk.verification_actions),
        ),
        confidence=ConfidenceOut(
            score_percent=result.confidence.percent,
            band=result.confidence.band,
            calibrated=result.confidence.calibrated,
            components=[
                ConfidenceComponentOut(
                    name=c.name,
                    label=c.label,
                    score=c.score,
                    weight=c.weight,
                    contribution_points=c.contribution_points,
                    explanation=c.explanation,
                )
                for c in result.confidence.components
            ],
            limiting_factors=list(result.confidence.limiting_factors),
            improvements=list(result.improvements),
        ),
        market=MarketContextOut(
            comparable_count=result.comparables.size,
            effective_sample_size=round(result.comparables.effective_sample_size, 1),
            match_level=result.comparables.key_level_used.label,
            search_widened=result.comparables.widened,
            mean_similarity=round(result.comparables.weighted_mean_similarity, 3),
            asking_price_distribution=distribution_out(valuation.raw_quantiles),
            adjusted_price_distribution=distribution_out(valuation.quantiles),
        ),
        comparables=[
            ComparableOut(
                listing_id=m.listing.listing_id,
                price=money_out(m.listing.price),  # type: ignore[arg-type]
                mileage_km=m.listing.mileage_km,
                model_year=m.listing.configuration.model_year,
                trim=m.listing.configuration.trim,
                city=m.listing.city,
                similarity=m.config_similarity,
                tier=int(m.tier),
                differences=list(m.differences),
                source_url=m.listing.source_url,
            )
            for m in result.comparables.top(COMPARABLE_LIMIT)
        ],
        negotiation=NegotiationOut(
            available=result.negotiation.available,
            unavailable_reason=result.negotiation.unavailable_reason,
            posture=result.negotiation.posture,
            opening_offer=money_out(result.negotiation.opening_offer),
            target_low=money_out(result.negotiation.target_range_low),
            target_high=money_out(result.negotiation.target_range_high),
            walk_away_above=money_out(result.negotiation.walk_away_above),
            observed_market_reduction_pct=result.negotiation.observed_market_reduction_pct,
            reduction_sample_size=result.negotiation.reduction_sample_size,
            leverage=[
                {
                    "title": p.title,
                    "evidence": p.evidence,
                    "strength": p.strength,
                    "monetary_basis_azn": p.monetary_basis_azn,
                }
                for p in result.negotiation.leverage
            ],
            rationale=list(result.negotiation.rationale),
        ),
        seller_questions=[
            SellerQuestionOut(
                question=q.question, why=q.why, priority=q.priority, triggered_by=q.triggered_by
            )
            for q in result.inspection.questions
        ],
        inspection_priorities=[
            InspectionItemOut(
                item=i.item,
                priority=i.priority,
                system=i.system,
                reason=i.reason,
                triggered_by=i.triggered_by,
            )
            for i in result.inspection.items
        ],
        candidate_explanations=list(result.explanations),
        limitations=list(result.limitations),
        narrative=_narrative(narrative),
    )


def _vehicle(result: AnalysisResult) -> VehicleOut:
    config = result.subject.configuration
    return VehicleOut(
        description=config.describe(),
        make=config.make,
        model=config.model,
        model_year=config.model_year,
        generation=config.generation,
        trim=config.trim,
        fuel=config.fuel.value,
        transmission=config.transmission.value,
        drivetrain=config.drivetrain.value,
        body=config.body.value,
        engine_displacement_l=config.displacement_l,
        horsepower=config.horsepower,
        configuration_id=config.config_id,
        specificity=config.specificity,
        unknown_attributes=list(config.unknown_fields),
        mileage_km=result.subject.mileage_km,
        city=result.subject.city,
        region=result.subject.region,
        vin_provided=bool(result.subject.vin),
    )


def _narrative(narrative: NarrativeResult | None) -> NarrativeOut | None:
    if narrative is None:
        return None
    report = narrative.report
    return NarrativeOut(
        generated_by=narrative.generated_by,
        is_ai_generated=narrative.is_ai_generated,
        degraded_reason=narrative.degraded_reason,
        vehicle_summary=report.vehicle_summary,
        market_context=report.market_context,
        price_explanation=report.price_explanation,
        final_assessment=report.final_assessment,
        positive_signals=[
            {"kind": c.kind.value, "statement": c.statement, "basis": c.basis}
            for c in report.positive_signals
        ],
        risk_signals=[
            {"kind": c.kind.value, "statement": c.statement, "basis": c.basis}
            for c in report.risk_signals
        ],
        limitations=list(report.limitations),
    )
