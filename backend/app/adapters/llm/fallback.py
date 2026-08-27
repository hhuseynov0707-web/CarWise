"""Deterministic narrative, built without a language model.

Audit §5 states the invariant this file implements:

> The system must produce a complete, correct, useful report with the LLM
> switched off.

That is not a courtesy fallback. It is the thing that proves the product's value
lives in the analytical engines rather than in the model — and it is what the
service degrades to whenever the provider is unavailable, unconfigured, or
produces output that fails validation twice.

The prose here is assembled from the evidence bundle by template. It is plainer
than a model would write, and it is never wrong.
"""

from __future__ import annotations

from typing import Any

from app.adapters.llm.schema import (
    Claim,
    ClaimKind,
    MarketAssessment,
    NegotiationOutput,
    VehicleReport,
)


def build_fallback_report(bundle: dict[str, Any]) -> VehicleReport:
    """Assemble a complete report from the evidence bundle alone."""
    vehicle = bundle.get("vehicle", {})
    valuation = bundle.get("valuation", {})
    position = bundle.get("price_position", {})
    market = bundle.get("market_statistics", {})
    confidence = bundle.get("confidence", {})
    negotiation = bundle.get("negotiation", {})

    return VehicleReport(
        vehicle_summary=_vehicle_summary(vehicle, market),
        market_assessment=MarketAssessment(
            asking_price=position.get("asking_price_azn"),
            fair_market_low=valuation.get("fair_market_low_azn"),
            fair_market_high=valuation.get("fair_market_high_azn"),
            central_estimate=valuation.get("central_estimate_azn"),
            price_difference_percent=position.get("difference_percent"),
            price_percentile=position.get("percentile"),
            rating=position.get("rating", "INSUFFICIENT_DATA"),
            confidence=int(confidence.get("score_percent", 0)),
        ),
        market_context=_market_context(market, valuation),
        price_explanation=_price_explanation(position, bundle),
        positive_signals=_positive_claims(bundle),
        risk_signals=_risk_claims(bundle),
        model_specific_concerns=[],
        seller_questions=[q["question"] for q in bundle.get("seller_questions", [])],
        inspection_priorities=[
            f"{item['item']} ({item['priority']} priority) — {item['reason']}"
            for item in bundle.get("inspection_priorities", [])
        ],
        negotiation_strategy=_negotiation(negotiation),
        final_assessment=_final_assessment(bundle),
        limitations=list(bundle.get("limitations", [])) or ["No limitations were recorded."],
    )


def _vehicle_summary(vehicle: dict[str, Any], market: dict[str, Any]) -> str:
    parts = [f"This analysis covers a {vehicle.get('description', 'vehicle')}."]

    if vehicle.get("mileage_km") is not None:
        parts.append(f"The odometer reads {vehicle['mileage_km']:,} km.")
    else:
        parts.append("No odometer reading was provided.")

    if vehicle.get("city"):
        parts.append(f"It is listed in {vehicle['city']}.")

    unknown = vehicle.get("unknown_attributes") or []
    if unknown:
        parts.append(
            f"The following details were not specified and would sharpen the analysis: "
            f"{', '.join(unknown)}."
        )

    count = market.get("comparable_count", 0)
    if count:
        parts.append(
            f"It was compared against {count} similar listings "
            f"({market.get('match_level', 'comparable vehicles')})."
        )
    return " ".join(parts)


def _market_context(market: dict[str, Any], valuation: dict[str, Any]) -> str:
    count = market.get("comparable_count", 0)
    if not count:
        return "No comparable listings were available for this configuration."

    parts = [
        f"{count} comparable listings were analysed, with an average configuration "
        f"similarity of {market.get('mean_similarity', 0):.0%}."
    ]

    distribution = market.get("asking_price_distribution")
    if distribution:
        parts.append(
            f"Asking prices among those listings run from about "
            f"{distribution['p10']:,.0f} AZN at the 10th percentile to "
            f"{distribution['p90']:,.0f} AZN at the 90th, with a median of "
            f"{distribution['p50']:,.0f} AZN."
        )

    dispersion = market.get("dispersion")
    if dispersion is not None:
        agreement = "closely" if dispersion < 0.10 else "loosely"
        parts.append(
            f"After adjusting for mileage and model year, prices cluster {agreement} — "
            f"varying by about {dispersion:.0%} around the median."
        )

    if market.get("search_widened"):
        parts.append(
            "The comparable search had to be widened beyond exactly-matching "
            "configurations to reach a usable sample size."
        )

    for note in valuation.get("notes", []):
        parts.append(note)

    return " ".join(parts)


def _price_explanation(position: dict[str, Any], bundle: dict[str, Any]) -> str:
    gap = position.get("gap_analysis")
    if not gap:
        return "No asking price was supplied, so no market position could be calculated."

    direction = "below" if gap["total_gap_azn"] < 0 else "above"
    parts = [
        f"The asking price sits {abs(gap['total_gap_azn']):,.0f} AZN {direction} the "
        f"median of comparable listings ({gap['reference_median_azn']:,.0f} AZN)."
    ]

    for component in gap.get("components", []):
        if component["amount_azn"] == 0:
            continue
        effect = "adds" if component["amount_azn"] > 0 else "removes"
        parts.append(
            f"{component['label']} {effect} about "
            f"{abs(component['amount_azn']):,.0f} AZN. {component['evidence']}"
        )

    unexplained = gap["unexplained_azn"]
    if abs(unexplained) >= 1:
        if unexplained < 0:
            parts.append(
                f"After those measurable differences, about {abs(unexplained):,.0f} AZN of "
                f"the lower price remains unaccounted for. That remainder is what warrants "
                f"attention — it may reflect condition, equipment, urgency to sell, or "
                f"something not stated in the listing."
            )
        else:
            parts.append(
                f"After those measurable differences, the car still asks about "
                f"{unexplained:,.0f} AZN more than the comparable evidence supports."
            )

    for explanation in bundle.get("candidate_explanations", []):
        parts.append(explanation)

    return " ".join(parts)


def _positive_claims(bundle: dict[str, Any]) -> list[Claim]:
    return [
        Claim(
            kind=ClaimKind.FACT,
            statement=f"{signal['title']}. {' '.join(signal['evidence'])}",
            basis=signal["source"],
        )
        for signal in bundle.get("positive_signals", [])
    ][:10]


def _risk_claims(bundle: dict[str, Any]) -> list[Claim]:
    """Render each risk signal as two claims: the evidence, then the reading.

    Splitting them is the point. The observation is a fact; what it might mean
    is an inference. Collapsing the two is how a report ends up implying more
    than it knows.
    """
    claims: list[Claim] = []
    for signal in bundle.get("risk_signals", []):
        claims.append(
            Claim(
                kind=ClaimKind.FACT,
                statement=f"{signal['title']}. {' '.join(signal['evidence'])}",
                basis=signal["source"],
            )
        )
        if len(claims) < 15:
            claims.append(
                Claim(
                    kind=ClaimKind.POSSIBILITY,
                    statement=f"{signal['interpretation']} {signal['recommended_verification']}",
                    basis=signal["source"],
                )
            )
    return claims[:15]


def _negotiation(negotiation: dict[str, Any]) -> NegotiationOutput | None:
    if not negotiation.get("available"):
        return None
    summary_parts = [negotiation.get("posture", "")]
    summary_parts.extend(negotiation.get("rationale", []))
    return NegotiationOutput(
        summary=" ".join(p for p in summary_parts if p)[:1200],
        opening_offer=negotiation.get("opening_offer_azn"),
        target_low=negotiation.get("target_low_azn"),
        target_high=negotiation.get("target_high_azn"),
        key_arguments=[
            f"{point['title']} — {point['evidence']}"
            for point in negotiation.get("leverage", [])
        ][:8],
    )


def _final_assessment(bundle: dict[str, Any]) -> str:
    """The closing summary. Presents evidence; makes no recommendation (§35)."""
    position = bundle.get("price_position", {})
    valuation = bundle.get("valuation", {})
    confidence = bundle.get("confidence", {})
    risks = bundle.get("risk_signals", [])

    if valuation.get("outcome") != "OK":
        return (
            f"{valuation.get('insufficient_reason', 'Insufficient market data.')} "
            f"Without a comparable sample, no market position can be given for this "
            f"vehicle. The vehicle details and any risk indicators identified are still "
            f"listed above, and an independent inspection remains the primary way to "
            f"establish condition."
        )

    parts: list[str] = []
    difference = position.get("difference_percent")
    if difference is not None:
        direction = "above" if difference > 0 else "below"
        parts.append(
            f"Based on {bundle.get('market_statistics', {}).get('comparable_count', 0)} "
            f"comparable listings, the asking price is approximately "
            f"{abs(difference):.1f}% {direction} the estimated central value of "
            f"{valuation.get('central_estimate_azn', 0):,.0f} AZN, within an estimated "
            f"fair range of {valuation.get('fair_market_low_azn', 0):,.0f}–"
            f"{valuation.get('fair_market_high_azn', 0):,.0f} AZN."
        )

    severe = [r for r in risks if r.get("severity") in ("HIGH", "CRITICAL")]
    moderate = [r for r in risks if r.get("severity") == "MODERATE"]

    if severe:
        parts.append(
            f"{len(severe)} higher-severity risk indicator"
            f"{'s were' if len(severe) != 1 else ' was'} detected: "
            f"{'; '.join(r['title'].lower() for r in severe)}. Each is listed above with "
            f"the evidence behind it and how it can be checked."
        )
    elif moderate:
        parts.append(
            f"{len(moderate)} moderate risk indicator"
            f"{'s were' if len(moderate) != 1 else ' was'} detected, none of them severe."
        )
    else:
        parts.append("No significant risk indicators were detected in the available data.")

    parts.append(
        f"Confidence in this analysis is {confidence.get('score_percent', 0)}% "
        f"({str(confidence.get('band', '')).replace('_', ' ').lower()}), reflecting the "
        f"size and quality of the comparable evidence rather than a verified probability."
    )

    parts.append(
        "This analysis is built from listing data and the details supplied. It cannot "
        "establish mechanical condition or accident history. An independent inspection "
        "and the questions listed above are the next step, and the decision remains yours."
    )

    return " ".join(parts)
