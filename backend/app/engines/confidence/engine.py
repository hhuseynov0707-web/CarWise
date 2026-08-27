"""Confidence engine (spec §48).

Confidence must never be a decorative number. Every point of it decomposes into
named components with stated reasons, so the UI can answer "why 87%?" with
evidence rather than with a shrug (spec §69).

One honesty constraint, from audit §7.6: until the platform has held-out data to
measure interval coverage against, this score is **not** a calibrated
probability and must not be described as one. It measures *the strength of the
evidence the estimate rests on* — sample size, similarity, freshness,
completeness, agreement. The ``calibrated`` flag records that distinction so
that the day real calibration exists, the UI copy can change with it rather than
having quietly overclaimed all along.

Pure computation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import ValuationOutcome
from app.domain.market import SubjectVehicle
from app.engines.comparables.engine import ComparableSet
from app.engines.stats import clamp, median, saturating
from app.engines.valuation.engine import Valuation

#: Effective sample size at which the sample-size component scores 0.5.
SAMPLE_HALF_POINT = 18.0

#: Comparable age, in days, at which the freshness component scores 0.5.
FRESHNESS_HALF_LIFE_DAYS = 60.0

#: Dispersion (robust CV) at which the agreement component scores 0.5.
DISPERSION_HALF_POINT = 0.14

#: Confidence can never be reported above this. No comparable-based estimate of
#: a used car deserves to claim near-certainty; there is always unobserved
#: condition, history and negotiation variance behind any listing price.
MAX_CONFIDENCE = 0.95

#: Floor applied when the valuation succeeded at all.
MIN_REPORTED_CONFIDENCE = 0.05


@dataclass(frozen=True, slots=True)
class ConfidenceComponent:
    """One named contribution to the confidence score."""

    name: str
    label: str
    score: float
    weight: float
    explanation: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    @property
    def contribution_points(self) -> float:
        """Contribution expressed in percentage points of the final score."""
        return round(self.contribution * 100, 1)


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    """The confidence score plus its full derivation."""

    score: float
    components: tuple[ConfidenceComponent, ...]
    calibrated: bool = False
    """False until interval coverage has been validated on held-out data.

    While False the UI must describe this as evidence strength, not as a
    probability that the estimate is correct."""

    limiting_factors: tuple[str, ...] = ()
    """The components dragging the score down, worst first — this is what the
    UI turns into "add mileage to improve this analysis"."""

    @property
    def percent(self) -> int:
        return round(self.score * 100)

    @property
    def band(self) -> str:
        if self.score >= 0.80:
            return "HIGH"
        if self.score >= 0.60:
            return "MODERATE"
        if self.score >= 0.40:
            return "LIMITED"
        return "LOW"

    def strongest(self, n: int = 3) -> tuple[ConfidenceComponent, ...]:
        return tuple(sorted(self.components, key=lambda c: c.contribution, reverse=True)[:n])

    def weakest(self, n: int = 3) -> tuple[ConfidenceComponent, ...]:
        return tuple(sorted(self.components, key=lambda c: c.score)[:n])


@dataclass
class ConfidenceEngine:
    """Scores how much evidence a valuation actually rests on."""

    weights: dict[str, float] = None  # type: ignore[assignment]

    #: Component weights. Sample size and similarity dominate because they are
    #: what separate a real market estimate from an anecdote.
    DEFAULT_WEIGHTS = {
        "sample_size": 0.25,
        "similarity": 0.22,
        "completeness": 0.15,
        "freshness": 0.13,
        "agreement": 0.12,
        "geography": 0.08,
        "verification": 0.05,
    }

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = dict(self.DEFAULT_WEIGHTS)
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"confidence weights must sum to 1.0, got {total}")

    def assess(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        as_of: datetime,
    ) -> ConfidenceAssessment:
        if valuation.outcome is ValuationOutcome.INSUFFICIENT_DATA:
            return ConfidenceAssessment(
                score=0.0,
                components=(),
                limiting_factors=(
                    valuation.insufficient_reason
                    or "Not enough comparable market data to estimate a value.",
                ),
            )

        components = (
            self._sample_size(comparables),
            self._similarity(comparables),
            self._completeness(subject),
            self._freshness(comparables, as_of),
            self._agreement(valuation),
            self._geography(subject, comparables),
            self._verification(subject, as_of),
        )

        raw = sum(c.contribution for c in components)
        score = clamp(raw, MIN_REPORTED_CONFIDENCE, MAX_CONFIDENCE)

        limiting = tuple(
            c.explanation for c in sorted(components, key=lambda c: c.score) if c.score < 0.6
        )

        return ConfidenceAssessment(
            score=round(score, 4),
            components=components,
            calibrated=False,
            limiting_factors=limiting[:4],
        )

    # --- components --------------------------------------------------------

    def _sample_size(self, comparables: ComparableSet) -> ConfidenceComponent:
        n_eff = comparables.effective_sample_size
        score = saturating(n_eff, SAMPLE_HALF_POINT)
        return ConfidenceComponent(
            name="sample_size",
            label="Comparable sample size",
            score=round(score, 4),
            weight=self.weights["sample_size"],
            explanation=(
                f"{comparables.size} comparable listings, worth about {n_eff:.0f} "
                f"equally-weighted observations after similarity and freshness weighting."
            ),
        )

    def _similarity(self, comparables: ComparableSet) -> ConfidenceComponent:
        similarity = comparables.weighted_mean_similarity
        # Rescale: a set averaging 0.55 similarity is weak evidence even though
        # 0.55 sounds middling, so the floor of the useful band maps to 0.
        score = clamp((similarity - 0.5) / 0.45, 0.0, 1.0)
        tier = comparables.tier_used.name.replace("_", " ").title()
        return ConfidenceComponent(
            name="similarity",
            label="Comparable quality",
            score=round(score, 4),
            weight=self.weights["similarity"],
            explanation=(
                f"Weighted average configuration similarity of {similarity:.0%}, "
                f"drawn from {tier} matches ({comparables.key_level_used.label})."
            ),
        )

    def _completeness(self, subject: SubjectVehicle) -> ConfidenceComponent:
        config_score = subject.configuration.specificity
        extras = [
            subject.mileage_km is not None,
            subject.city is not None,
            subject.asking_price is not None,
        ]
        extra_score = sum(extras) / len(extras)
        score = 0.7 * config_score + 0.3 * extra_score

        missing = list(subject.configuration.unknown_fields)
        if subject.mileage_km is None:
            missing.append("mileage")
        if subject.city is None:
            missing.append("location")

        explanation = (
            f"Vehicle is {config_score:.0%} specified."
            if not missing
            else f"Vehicle is {config_score:.0%} specified; unknown: {', '.join(missing[:5])}."
        )
        return ConfidenceComponent(
            name="completeness",
            label="Vehicle detail provided",
            score=round(score, 4),
            weight=self.weights["completeness"],
            explanation=explanation,
        )

    def _freshness(self, comparables: ComparableSet, as_of: datetime) -> ConfidenceComponent:
        if not comparables.matches:
            return ConfidenceComponent(
                "freshness", "Data freshness", 0.0, self.weights["freshness"],
                "No comparable observations available.",
            )
        ages = [m.listing.age_days(as_of) for m in comparables.matches]
        median_age = median([float(a) for a in ages])
        score = 0.5 ** (median_age / FRESHNESS_HALF_LIFE_DAYS)
        return ConfidenceComponent(
            name="freshness",
            label="Data freshness",
            score=round(clamp(score, 0.0, 1.0), 4),
            weight=self.weights["freshness"],
            explanation=(
                f"Median comparable was last observed {median_age:.0f} days ago."
                if median_age > 0
                else "Comparable listings were observed today."
            ),
        )

    def _agreement(self, valuation: Valuation) -> ConfidenceComponent:
        dispersion = valuation.dispersion
        score = 1.0 - saturating(dispersion, DISPERSION_HALF_POINT)
        return ConfidenceComponent(
            name="agreement",
            label="Market agreement",
            score=round(clamp(score, 0.0, 1.0), 4),
            weight=self.weights["agreement"],
            explanation=(
                f"Comparable prices vary by about {dispersion:.0%} around the median "
                f"after adjusting for mileage and age — "
                f"{'tight agreement' if dispersion < 0.10 else 'wide disagreement'} "
                f"about what this configuration is worth."
            ),
        )

    def _geography(
        self, subject: SubjectVehicle, comparables: ComparableSet
    ) -> ConfidenceComponent:
        if subject.city is None:
            return ConfidenceComponent(
                "geography", "Geographic match", 0.4, self.weights["geography"],
                "No location was provided, so comparables were drawn nationally.",
            )
        if not comparables.matches:
            return ConfidenceComponent(
                "geography", "Geographic match", 0.0, self.weights["geography"],
                "No comparable observations available.",
            )
        local = sum(1 for m in comparables.matches if m.listing.region == subject.region)
        share = local / len(comparables.matches)
        return ConfidenceComponent(
            name="geography",
            label="Geographic match",
            score=round(share, 4),
            weight=self.weights["geography"],
            explanation=(
                f"{local} of {len(comparables.matches)} comparables ({share:.0%}) are in "
                f"the same market area ({subject.region})."
            ),
        )

    def _verification(self, subject: SubjectVehicle, as_of: datetime) -> ConfidenceComponent:
        share = subject.ledger.verified_share(as_of)
        if not subject.ledger.fields():
            return ConfidenceComponent(
                "verification", "Independently verified data", 0.0,
                self.weights["verification"],
                "No vehicle details have been independently verified.",
            )
        return ConfidenceComponent(
            name="verification",
            label="Independently verified data",
            score=round(share, 4),
            weight=self.weights["verification"],
            explanation=(
                f"{share:.0%} of the vehicle details used here come from independently "
                f"verifiable sources rather than the seller or the user."
            ),
        )


def describe_improvements(assessment: ConfidenceAssessment, subject: SubjectVehicle) -> list[str]:
    """Concrete actions the user could take to raise confidence.

    Turns a low score from a dead end into a next step, which is the difference
    between "we cannot help you" and "tell us the mileage and we can".
    """
    actions: list[str] = []
    scores = {c.name: c.score for c in assessment.components}

    if subject.mileage_km is None:
        actions.append("Add the odometer reading — mileage is the single largest price factor.")
    if subject.city is None:
        actions.append("Add the vehicle's location to compare against the local market.")
    for field_name in subject.configuration.unknown_fields:
        label = {
            "trim": "Specify the trim level",
            "generation": "Specify the model generation",
            "displacement_l": "Add engine displacement",
            "transmission": "Specify the transmission type",
            "drivetrain": "Specify the drivetrain",
            "engine_code": "Add the engine code if known",
            "model_year": "Add the model year",
            "fuel": "Specify the fuel type",
            "body": "Specify the body style",
        }.get(field_name)
        if label:
            actions.append(f"{label} to narrow the comparable set.")

    if scores.get("sample_size", 1.0) < 0.5:
        actions.append(
            "This configuration is thinly traded locally; consider re-running the "
            "analysis in a few weeks as more listings appear."
        )
    return actions[:5]
