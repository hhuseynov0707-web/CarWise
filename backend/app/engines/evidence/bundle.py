"""Evidence aggregation (spec §31).

Assembles every computed finding into one structured bundle. Two consumers:

1. The API and report renderer, which present it directly. This path works with
   the language model switched off entirely — a hard requirement from audit §5.
2. The reasoning layer, which receives the bundle as its **entire** universe of
   facts and may not go beyond it.

The second consumer is why :func:`numeric_registry` exists. Every number in the
bundle is collected into a set, and the LLM response validator checks each
number the model emits against it. A model that writes "the median is 46,200"
when no such figure appears anywhere in the evidence has its response rejected.
That is a code-level control, not a prompt instruction — prompts are not a
security boundary (audit §5).

Pure computation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import DealRating, PriceBasis, ValuationOutcome
from app.domain.market import SubjectVehicle
from app.domain.provenance import AttributeConflict
from app.engines.comparables.engine import ComparableSet
from app.engines.confidence.engine import ConfidenceAssessment
from app.engines.inspection.engine import InspectionPlan
from app.engines.negotiation.engine import NegotiationStrategy
from app.engines.rating.engine import PricePosition
from app.engines.risk.engine import RiskAssessment
from app.engines.valuation.engine import Valuation

#: How many comparables to include individually in the bundle. The full set
#: drives the statistics; sending hundreds of rows to a language model wastes
#: context without improving the reasoning.
#:
#: Ten rather than twelve because the payload has to fit a per-minute token
#: budget, and rows are the part of it that grows with the market. The model
#: is not permitted to compute anything from them — every figure it may state
#: is already in ``market_statistics`` — so these are there to let it describe
#: the sample, and the most similar ten describe it as well as twelve do.
COMPARABLE_SAMPLE_LIMIT = 10


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Everything the platform computed about one vehicle.

    This is the canonical result object. The API serializes it, the PDF renders
    it, and the reasoning layer explains it. None of those consumers may add
    findings of their own.
    """

    analysis_id: str
    generated_at: datetime
    subject: SubjectVehicle
    comparables: ComparableSet
    valuation: Valuation
    position: PricePosition
    risk: RiskAssessment
    confidence: ConfidenceAssessment
    negotiation: NegotiationStrategy
    inspection: InspectionPlan
    conflicts: tuple[AttributeConflict, ...] = ()
    explanations: tuple[str, ...] = ()
    """Hedged candidate explanations for an unexplained price gap (spec §19)."""

    limitations: tuple[str, ...] = ()
    improvements: tuple[str, ...] = ()
    """Actions the user could take to strengthen the analysis."""

    @property
    def has_valuation(self) -> bool:
        return self.valuation.outcome is ValuationOutcome.OK

    @property
    def headline(self) -> str:
        return self.subject.configuration.describe()


def build_limitations(
    valuation: Valuation,
    confidence: ConfidenceAssessment,
    comparables: ComparableSet,
    subject: SubjectVehicle,
) -> tuple[str, ...]:
    """State plainly what this analysis cannot tell the user (spec §34, §59).

    Written from the actual state of the analysis rather than from a boilerplate
    disclaimer, so the limitations section changes when the evidence does.
    """
    out: list[str] = [
        "This analysis is based on listing data, publicly available information and "
        "details supplied by the user. It cannot establish the mechanical condition, "
        "accident history, legal status or future reliability of the vehicle.",
    ]

    if valuation.basis is PriceBasis.ASKING:
        out.append(
            "Values are derived from asking prices, not confirmed sale prices. Vehicles "
            "in this market frequently sell for less than their listed price, so the "
            "true transaction value is likely to sit below this range."
        )

    if not subject.vin:
        out.append(
            "No VIN was supplied, so the factory specification could not be independently "
            "confirmed and the vehicle was matched on its stated attributes alone."
        )

    out.append(
        "No independent vehicle history record was available for this vehicle. Accident, "
        "title, odometer and theft history remain unverified."
    )

    if comparables.widened:
        out.append(
            f"The comparable set had to be widened to {comparables.key_level_used.label} "
            f"to reach a usable sample, so the comparison is less exact than an "
            f"identically-configured match would be."
        )

    for adjustment in valuation.unavailable_adjustments():
        if adjustment.name in ("seasonality", "market_demand"):
            out.append(adjustment.explanation)

    if not confidence.calibrated:
        out.append(
            "The confidence figure reflects the strength of the underlying evidence — "
            "sample size, similarity, freshness and completeness. It has not yet been "
            "calibrated against verified outcomes and should not be read as a probability "
            "that the estimate is correct."
        )

    if subject.mileage_km is None:
        out.append("No odometer reading was provided, which is the largest single price factor.")

    return tuple(out)


def build_evidence_bundle(result: AnalysisResult) -> dict[str, Any]:
    """Serialize the analysis into the structure the reasoning layer receives.

    Deliberately flat and explicit. Every claim the model is allowed to make
    should be traceable to a key in this dictionary.
    """
    subject = result.subject
    valuation = result.valuation
    position = result.position

    bundle: dict[str, Any] = {
        "analysis_id": result.analysis_id,
        "generated_at": result.generated_at.isoformat(),
        "currency": "AZN",
        "vehicle": _vehicle_section(subject),
        "market_statistics": _market_section(result.comparables, valuation),
        "comparables": _comparables_section(result.comparables),
        "valuation": _valuation_section(valuation),
        "price_position": _position_section(position),
        "risk_signals": _risk_section(result.risk),
        "positive_signals": [
            {"title": p.title, "evidence": list(p.evidence), "source": p.source}
            for p in result.risk.positives
        ],
        "confidence": _confidence_section(result.confidence),
        "negotiation": _negotiation_section(result.negotiation),
        "inspection_priorities": [
            {
                "item": i.item,
                "priority": i.priority,
                "system": i.system,
                "reason": i.reason,
                "triggered_by": i.triggered_by,
            }
            for i in result.inspection.items
        ],
        "seller_questions": [
            {
                "question": q.question,
                "why": q.why,
                "priority": q.priority,
                "triggered_by": q.triggered_by,
            }
            for q in result.inspection.questions
        ],
        "candidate_explanations": list(result.explanations),
        "data_conflicts": [
            {
                "field": c.field_name,
                "accepted_value": str(c.accepted.value),
                "accepted_source": c.accepted.provenance.source,
                "rejected_value": str(c.rejected.value),
                "rejected_source": c.rejected.provenance.source,
            }
            for c in result.conflicts
        ],
        "vehicle_history": {
            "available": False,
            "note": (
                "No independent vehicle history provider is integrated for this market yet. "
                "Accident, title, odometer and theft history are unverified."
            ),
        },
        "service_records": {
            "provided": subject.service_records_provided,
            "analysis": None,
        },
        "diagnostic_codes": [],
        "web_research": [],
        "model_specific_concerns": [],
        "limitations": list(result.limitations),
    }
    return bundle


def _vehicle_section(subject: SubjectVehicle) -> dict[str, Any]:
    config = subject.configuration
    return {
        "description": config.describe(),
        "make": config.make,
        "model": config.model,
        "model_year": config.model_year,
        "generation": config.generation,
        "trim": config.trim,
        "engine_displacement_l": config.displacement_l,
        "fuel": config.fuel.value,
        "transmission": config.transmission.value,
        "drivetrain": config.drivetrain.value,
        "body": config.body.value,
        "horsepower": config.horsepower,
        "configuration_id": config.config_id,
        "specificity": config.specificity,
        "unknown_attributes": list(config.unknown_fields),
        "mileage_km": subject.mileage_km,
        "city": subject.city,
        "region": subject.region,
        "seller_type": subject.seller_type.value,
        "asking_price_azn": (
            subject.asking_price.as_float() if subject.asking_price else None
        ),
        "vin_provided": bool(subject.vin),
        "service_records_provided": subject.service_records_provided,
        "owner_count": subject.owner_count,
        "damage_disclosed": subject.has_damage_disclosure,
        "repaint_disclosed": subject.has_repaint_disclosure,
    }


def _market_section(comparables: ComparableSet, valuation: Valuation) -> dict[str, Any]:
    quantiles = valuation.raw_quantiles
    return {
        "comparable_count": comparables.size,
        "effective_sample_size": round(comparables.effective_sample_size, 1),
        "match_level": comparables.key_level_used.label,
        "search_widened": comparables.widened,
        "mean_similarity": round(comparables.weighted_mean_similarity, 3),
        "candidates_considered": comparables.candidates_considered,
        "excluded_stale": comparables.excluded_stale,
        "asking_price_distribution": quantiles.as_dict() if quantiles else None,
        "adjusted_price_distribution": (
            valuation.quantiles.as_dict() if valuation.quantiles else None
        ),
        "dispersion": valuation.dispersion,
        "outliers_removed": valuation.outliers_removed,
    }


def _comparables_section(comparables: ComparableSet) -> list[dict[str, Any]]:
    return [
        {
            "listing_id": m.listing.listing_id,
            "price_azn": m.price_azn,
            "mileage_km": m.listing.mileage_km,
            "model_year": m.listing.configuration.model_year,
            "trim": m.listing.configuration.trim,
            "city": m.listing.city,
            "similarity": m.config_similarity,
            "tier": m.tier.value,
            "differences": list(m.differences),
            "days_on_market": None,
            "source": m.listing.source,
        }
        for m in comparables.top(COMPARABLE_SAMPLE_LIMIT)
    ]


def _valuation_section(valuation: Valuation) -> dict[str, Any]:
    return {
        "outcome": valuation.outcome.value,
        "price_basis": valuation.basis.value,
        "central_estimate_azn": (
            valuation.central_estimate.as_float() if valuation.central_estimate else None
        ),
        "fair_market_low_azn": (
            valuation.fair_market_low.as_float() if valuation.fair_market_low else None
        ),
        "fair_market_high_azn": (
            valuation.fair_market_high.as_float() if valuation.fair_market_high else None
        ),
        "raw_market_median_azn": (
            valuation.raw_market_median.as_float() if valuation.raw_market_median else None
        ),
        "insufficient_reason": valuation.insufficient_reason,
        "adjustments": [
            {
                "factor": a.name,
                "amount_azn": a.amount_azn,
                "status": a.reason.value,
                "explanation": a.explanation,
                "method": a.method,
                "data_points": a.data_points,
            }
            for a in valuation.adjustments
        ],
        "notes": list(valuation.notes),
    }


def _position_section(position: PricePosition) -> dict[str, Any]:
    gap = position.gap_analysis
    return {
        "rating": position.rating.value,
        "rating_label": position.label,
        "asking_price_azn": position.asking_price.as_float() if position.asking_price else None,
        "difference_azn": position.difference_azn,
        "difference_percent": position.difference_pct,
        "percentile": position.percentile,
        "within_fair_range": position.within_range,
        "rationale": list(position.rationale),
        "gap_analysis": (
            {
                "reference_median_azn": gap.reference_median_azn,
                "total_gap_azn": gap.total_gap_azn,
                "explained_azn": gap.explained_azn,
                "unexplained_azn": gap.unexplained_azn,
                "explained_share": round(gap.explained_share, 3),
                "components": [
                    {
                        "factor": c.factor,
                        "label": c.label,
                        "amount_azn": c.amount_azn,
                        "evidence": c.evidence,
                    }
                    for c in gap.components
                ],
            }
            if gap
            else None
        ),
    }


def _risk_section(risk: RiskAssessment) -> list[dict[str, Any]]:
    return [
        {
            "type": s.risk_type.value,
            "severity": s.severity.value,
            "title": s.title,
            "evidence": list(s.evidence),
            "interpretation": s.interpretation,
            "recommended_verification": s.recommended_verification,
            "source": s.source,
            "confidence": s.confidence,
            "evidence_strength": s.strength.value,
        }
        for s in risk.by_severity()
    ]


def _confidence_section(confidence: ConfidenceAssessment) -> dict[str, Any]:
    return {
        "score_percent": confidence.percent,
        "band": confidence.band,
        "calibrated": confidence.calibrated,
        "components": [
            {
                "name": c.name,
                "label": c.label,
                "score": c.score,
                "weight": c.weight,
                "contribution_points": c.contribution_points,
                "explanation": c.explanation,
            }
            for c in confidence.components
        ],
        "limiting_factors": list(confidence.limiting_factors),
    }


def _negotiation_section(strategy: NegotiationStrategy) -> dict[str, Any]:
    return {
        "available": strategy.available,
        "unavailable_reason": strategy.unavailable_reason,
        "posture": strategy.posture,
        "opening_offer_azn": (
            strategy.opening_offer.as_float() if strategy.opening_offer else None
        ),
        "target_low_azn": (
            strategy.target_range_low.as_float() if strategy.target_range_low else None
        ),
        "target_high_azn": (
            strategy.target_range_high.as_float() if strategy.target_range_high else None
        ),
        "walk_away_above_azn": (
            strategy.walk_away_above.as_float() if strategy.walk_away_above else None
        ),
        "observed_market_reduction_percent": strategy.observed_market_reduction_pct,
        "reduction_sample_size": strategy.reduction_sample_size,
        "leverage": [
            {
                "title": p.title,
                "evidence": p.evidence,
                "strength": p.strength,
                "monetary_basis_azn": p.monetary_basis_azn,
            }
            for p in strategy.leverage
        ],
        "rationale": list(strategy.rationale),
    }


# --- anti-hallucination support -------------------------------------------


@dataclass
class NumericRegistry:
    """Every number the evidence bundle contains.

    The reasoning layer is permitted to *cite* these and to state simple derived
    quantities; it is not permitted to introduce new ones. Membership is checked
    with a relative tolerance so that a model rounding 43,107 to 43,100 is
    accepted while one inventing 51,000 is not.
    """

    values: frozenset[float] = field(default_factory=frozenset)
    tolerance: float = 0.02

    def contains(self, value: float) -> bool:
        if value in self.values:
            return True
        for known in self.values:
            scale = max(abs(known), abs(value), 1.0)
            if abs(known - value) / scale <= self.tolerance:
                return True
        return False

    def unknown_values(self, candidates: list[float]) -> list[float]:
        return [v for v in candidates if not self.contains(v)]


#: Small integers appear everywhere in ordinary prose ("three risk signals",
#: "the first thing to check") and flagging them would produce constant false
#: positives without catching any real fabrication. Fabrication that matters is
#: fabricated *prices* and *statistics*.
_TRIVIAL_MAX = 100.0


#: Numbers embedded in prose, with thousands separators or decimals.
_EMBEDDED_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:[ ,]\d{3})+|\d+(?:\.\d+)?)(?![\w])")


def numeric_registry(bundle: dict[str, Any], tolerance: float = 0.02) -> NumericRegistry:
    """Collect every numeric value appearing anywhere in the evidence bundle.

    Three things beyond the obvious numeric fields are collected, because each
    is a legitimate way for a narrative to restate evidence it was given:

    * **Numbers inside strings.** The bundle's own explanations contain figures
      ("roughly 99 AZN per 1,000 km"). A narrative quoting one of those is
      quoting the evidence, not inventing.
    * **Magnitudes.** The bundle stores a discount as ``-5806``; prose writes
      "5,806 AZN below". Same fact, sign carried by the words.
    * **Percentage/fraction twins.** ``0.94`` and ``94`` are one number.

    Being generous here is deliberate. A false positive rejects a correct
    narrative and degrades the report; a false negative lets through a figure
    that is at worst a restatement of supplied evidence. The check exists to
    catch fabricated *prices*, and those look nothing like anything in the
    bundle.
    """
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            found.add(float(node))
            return
        if isinstance(node, str):
            for match in _EMBEDDED_NUMBER.finditer(node):
                raw = match.group(1).replace(",", "").replace(" ", "")
                try:
                    found.add(float(raw))
                except ValueError:
                    continue
            return
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
            return
        if isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(bundle)

    derived: set[float] = set()
    for value in found:
        derived.add(abs(value))
        derived.add(round(value))
        derived.add(round(abs(value)))
        if 0.0 < abs(value) <= 1.0:
            derived.add(abs(value) * 100)
        if 1.0 < abs(value) <= 100.0:
            derived.add(abs(value) / 100)
    found |= derived

    return NumericRegistry(values=frozenset(found), tolerance=tolerance)


def is_trivial_number(value: float) -> bool:
    """Whether a number is too small and common to be worth policing."""
    return abs(value) <= _TRIVIAL_MAX and float(value).is_integer()


def rating_is_consistent(bundle: dict[str, Any], claimed_rating: str) -> bool:
    """Whether a model-reported rating matches the one the engines computed."""
    computed = bundle.get("price_position", {}).get("rating")
    if computed is None:
        return claimed_rating == DealRating.INSUFFICIENT_DATA.value
    return claimed_rating == computed
