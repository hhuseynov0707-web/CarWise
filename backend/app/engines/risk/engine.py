"""Risk engine (spec §20–21).

Produces a 0–100 score that means exactly one thing:

> the strength and number of risk **indicators** detected in the available data

It is explicitly *not* a probability that the vehicle is bad. A meticulously
maintained car with a thin paper trail scores higher than a neglected one with
complete records, and that is correct behaviour: the score measures what we
cannot see, not what is wrong.

Aggregation is **noisy-OR**, not a sum. Three moderate indicators should not
add to the same score as one severe one, and no number of trivial indicators
should reach 100. Noisy-OR saturates naturally and, because each signal's
marginal effect can be computed by removing it and recomputing, every point of
the score is attributable (spec §69).

Pure computation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import (
    EvidenceStrength,
    RiskSeverity,
    RiskType,
    ValuationOutcome,
)
from app.domain.market import SubjectVehicle
from app.domain.provenance import AttributeConflict
from app.engines.comparables.engine import ComparableSet
from app.engines.risk.signals import DisclosureReading, PositiveSignal, RiskSignal, read_disclosures
from app.engines.stats import median, percentile_rank
from app.engines.valuation.engine import Valuation

#: Marginal risk weight contributed by a single indicator at each severity.
#: Deliberately conservative: even a CRITICAL indicator alone lands at 55, in
#: the "high" band rather than the top, because one indicator is one indicator.
SEVERITY_WEIGHT: dict[RiskSeverity, float] = {
    RiskSeverity.INFO: 0.02,
    RiskSeverity.LOW: 0.08,
    RiskSeverity.MODERATE: 0.18,
    RiskSeverity.HIGH: 0.35,
    RiskSeverity.CRITICAL: 0.55,
}

#: Deviation below the fair-market range that starts to look like a signal
#: rather than a good deal, as a fraction of the low bound.
PRICE_ANOMALY_MILD = 0.08
PRICE_ANOMALY_STRONG = 0.15
PRICE_ANOMALY_SEVERE = 0.25

#: Mileage above the comparable median, as a ratio, before it is flagged.
MILEAGE_ANOMALY_RATIO = 1.4
MILEAGE_ANOMALY_SEVERE_RATIO = 1.8

#: Days on market beyond which a listing's persistence is itself information.
STALE_LISTING_DAYS = 60
VERY_STALE_LISTING_DAYS = 120


@dataclass(frozen=True, slots=True)
class RiskContribution:
    """One signal's measured share of the final score."""

    risk_type: RiskType
    title: str
    severity: RiskSeverity
    weight: float
    marginal_points: float
    """Points the score would drop if this single signal were removed."""


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """The risk score with its full decomposition."""

    score: int
    signals: tuple[RiskSignal, ...]
    positives: tuple[PositiveSignal, ...]
    contributions: tuple[RiskContribution, ...]

    @property
    def band(self) -> str:
        """Spec §20 bands."""
        if self.score <= 20:
            return "LOW"
        if self.score <= 40:
            return "MODERATE_LOW"
        if self.score <= 60:
            return "MODERATE"
        if self.score <= 80:
            return "HIGH"
        return "VERY_HIGH"

    @property
    def band_label(self) -> str:
        return {
            "LOW": "Low apparent risk indicators",
            "MODERATE_LOW": "Moderate-low risk indicators",
            "MODERATE": "Moderate risk indicators",
            "HIGH": "High risk indicators",
            "VERY_HIGH": "Very high risk indicators",
        }[self.band]

    def by_severity(self) -> tuple[RiskSignal, ...]:
        return tuple(sorted(self.signals, key=lambda s: (-s.rank, s.title)))

    def of_type(self, risk_type: RiskType) -> tuple[RiskSignal, ...]:
        return tuple(s for s in self.signals if s.risk_type is risk_type)

    @property
    def verification_actions(self) -> tuple[str, ...]:
        """Deduplicated verification steps, ordered by the severity that drove them."""
        seen: dict[str, None] = {}
        for signal in self.by_severity():
            seen.setdefault(signal.recommended_verification, None)
        return tuple(seen)


def aggregate_score(signals: Sequence[RiskSignal]) -> float:
    """Noisy-OR combination of independent indicators, in ``[0, 1]``.

    ``1 - Π(1 - wᵢ·cᵢ)``. Each indicator can only ever reduce the remaining
    "clean" probability mass, so the result saturates toward but never reaches
    1, and adding weak indicators to a strong one barely moves it.
    """
    remaining = 1.0
    for signal in signals:
        weight = SEVERITY_WEIGHT[signal.severity] * signal.confidence
        remaining *= 1.0 - min(0.95, max(0.0, weight))
    return 1.0 - remaining


@dataclass
class RiskEngine:
    """Runs every detector and aggregates their output."""

    def assess(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        as_of: datetime,
        conflicts: Sequence[AttributeConflict] = (),
    ) -> RiskAssessment:
        disclosures = read_disclosures(subject.description)

        signals: list[RiskSignal] = []
        signals += self._price_anomaly(subject, comparables, valuation)
        signals += self._mileage_anomaly(subject, comparables)
        signals += self._inconsistency(conflicts)
        signals += self._history_gaps(subject)
        signals += self._listing_behaviour(subject, as_of)
        signals += self._disclosure_signals(subject, disclosures)
        signals += self._configuration_anomaly(comparables, valuation)
        signals += self._unverified_claims(disclosures)

        signals.sort(key=lambda s: (-s.rank, -s.confidence))
        score = aggregate_score(signals)

        return RiskAssessment(
            score=round(score * 100),
            signals=tuple(signals),
            positives=tuple(self._positives(subject, comparables, valuation, disclosures)),
            contributions=tuple(self._attribute(signals, score)),
        )

    def _attribute(
        self, signals: Sequence[RiskSignal], total: float
    ) -> list[RiskContribution]:
        """Marginal points each signal adds, by leave-one-out recomputation."""
        out: list[RiskContribution] = []
        for index, signal in enumerate(signals):
            without = aggregate_score([s for i, s in enumerate(signals) if i != index])
            out.append(
                RiskContribution(
                    risk_type=signal.risk_type,
                    title=signal.title,
                    severity=signal.severity,
                    weight=round(SEVERITY_WEIGHT[signal.severity] * signal.confidence, 4),
                    marginal_points=round((total - without) * 100, 1),
                )
            )
        out.sort(key=lambda c: c.marginal_points, reverse=True)
        return out

    # --- detectors ---------------------------------------------------------

    def _price_anomaly(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
    ) -> list[RiskSignal]:
        """Price far below comparable market (spec §21, §38).

        A cheap car is not a suspicious car. This detector fires only when the
        gap is large enough that *something* explains it, and the report's job
        is then to find that something (spec §19) rather than to imply fraud.
        """
        if valuation.outcome is not ValuationOutcome.OK:
            return []
        if subject.asking_price is None or valuation.fair_market_low is None:
            return []

        asking = subject.asking_price.as_float()
        low = valuation.fair_market_low.as_float()
        if asking >= low:
            return []

        deviation = (low - asking) / low
        if deviation < PRICE_ANOMALY_MILD:
            return []

        if deviation >= PRICE_ANOMALY_SEVERE:
            severity = RiskSeverity.HIGH
        elif deviation >= PRICE_ANOMALY_STRONG:
            severity = RiskSeverity.MODERATE
        else:
            severity = RiskSeverity.LOW

        percentile = (
            percentile_rank(list(comparables.prices), asking) if comparables.matches else 0.0
        )

        return [
            RiskSignal(
                risk_type=RiskType.MARKET_PRICE_ANOMALY,
                severity=severity,
                title="Asking price is materially below comparable listings",
                evidence=(
                    f"Asking {subject.asking_price.format()} against an estimated fair "
                    f"range of {valuation.fair_market_low.format()}–"
                    f"{valuation.fair_market_high.format()}.",
                    f"That is {deviation:.1%} below the bottom of the range.",
                    f"Only about {percentile:.0f}% of {comparables.size} comparable "
                    f"listings ask less.",
                ),
                interpretation=(
                    "A gap this size usually has a specific cause — higher mileage, a "
                    "lower trim, disclosed damage, an urgent seller, or something not "
                    "stated in the listing. It is not by itself evidence of a problem, "
                    "but it is the thing most worth understanding before proceeding."
                ),
                recommended_verification=(
                    "Ask the seller directly why the price sits below comparable cars, "
                    "and arrange an independent inspection before committing."
                ),
                source="market comparison",
                confidence=min(0.95, 0.5 + deviation),
                strength=EvidenceStrength.STRONG,
            )
        ]

    def _mileage_anomaly(
        self, subject: SubjectVehicle, comparables: ComparableSet
    ) -> list[RiskSignal]:
        if subject.mileage_km is None or not comparables.matches:
            return []
        comp_mileages = [
            float(m.listing.mileage_km)
            for m in comparables.matches
            if m.listing.mileage_km is not None
        ]
        if len(comp_mileages) < 5:
            return []

        comp_median = median(comp_mileages)
        if comp_median <= 0:
            return []
        ratio = subject.mileage_km / comp_median
        if ratio < MILEAGE_ANOMALY_RATIO:
            return []

        severity = (
            RiskSeverity.MODERATE if ratio >= MILEAGE_ANOMALY_SEVERE_RATIO else RiskSeverity.LOW
        )
        return [
            RiskSignal(
                risk_type=RiskType.MILEAGE_ANOMALY,
                severity=severity,
                title="Mileage is well above comparable vehicles",
                evidence=(
                    f"{subject.mileage_km:,} km against a comparable median of "
                    f"{comp_median:,.0f} km ({ratio:.0%} of typical).",
                    f"Based on {len(comp_mileages)} comparable listings that stated mileage.",
                ),
                interpretation=(
                    "Higher mileage brings forward wear-item replacement and can affect "
                    "resale later. It is already reflected in the valuation; the open "
                    "question is whether maintenance kept pace with the distance covered."
                ),
                recommended_verification=(
                    "Request full service records and confirm major maintenance intervals "
                    "for this mileage have actually been carried out."
                ),
                source="market comparison",
                confidence=0.9,
                strength=EvidenceStrength.STRONG,
            )
        ]

    def _inconsistency(self, conflicts: Sequence[AttributeConflict]) -> list[RiskSignal]:
        """Sources disagreeing about the same attribute (spec §21)."""
        if not conflicts:
            return []
        evidence = tuple(
            f"{c.field_name}: {c.accepted.value!r} per {c.accepted.provenance.source}, "
            f"but {c.rejected.value!r} per {c.rejected.provenance.source}."
            for c in conflicts[:5]
        )
        return [
            RiskSignal(
                risk_type=RiskType.INFORMATION_INCONSISTENCY,
                severity=RiskSeverity.MODERATE if len(conflicts) > 1 else RiskSeverity.LOW,
                title="Vehicle details do not agree across sources",
                evidence=evidence,
                interpretation=(
                    "Discrepancies of this kind are frequently clerical — a mistyped "
                    "listing field or an ambiguous trim name. They can also indicate "
                    "that the vehicle is not the configuration being advertised, which "
                    "changes what it is worth."
                ),
                recommended_verification=(
                    "Check the VIN plate against the documents and confirm the "
                    "specification with the seller."
                ),
                source="provenance ledger",
                confidence=0.85,
                strength=EvidenceStrength.STRONG,
            )
        ]

    def _history_gaps(self, subject: SubjectVehicle) -> list[RiskSignal]:
        """Absence of verifiable history (spec §24).

        Deliberately framed as *unknown*, never as *bad*. Most cars in this
        market have no accessible history record; that is a property of the
        market, not an accusation about the vehicle.
        """
        missing: list[str] = []
        if not subject.vin:
            missing.append("no VIN was provided, so factory specification could not be confirmed")
        if not subject.service_records_provided:
            missing.append("no service records were supplied")
        if subject.owner_count is None:
            missing.append("the number of previous owners is unknown")

        if not missing:
            return []

        severity = RiskSeverity.MODERATE if len(missing) >= 2 else RiskSeverity.LOW
        return [
            RiskSignal(
                risk_type=RiskType.HISTORY_INCOMPLETE,
                severity=severity,
                title="Vehicle history could not be independently verified",
                evidence=tuple(m[0].upper() + m[1:] + "." for m in missing),
                interpretation=(
                    "This is a statement about what we could not check, not about the "
                    "vehicle's actual condition. An unverifiable history is common in "
                    "this market, and it shifts more weight onto a physical inspection."
                ),
                recommended_verification=(
                    "Ask for the VIN, maintenance invoices, and the vehicle's "
                    "registration document showing ownership history."
                ),
                source="input completeness",
                confidence=1.0,
                strength=EvidenceStrength.STRONG,
            )
        ]

    def _listing_behaviour(self, subject: SubjectVehicle, as_of: datetime) -> list[RiskSignal]:
        """Long time on market and repeated price cuts (spec §8, §21)."""
        days = subject.days_listed(as_of)
        changes = subject.price_change_count
        if days is None and changes == 0:
            return []

        evidence: list[str] = []
        if days is not None:
            evidence.append(f"Listed for {days} days.")
        if changes:
            evidence.append(f"Asking price changed {changes} time{'s' if changes != 1 else ''}.")
            move = subject.total_price_change_pct
            if move is not None:
                evidence.append(f"Net movement from the original price: {move:+.1f}%.")

        stale = days is not None and days >= STALE_LISTING_DAYS
        very_stale = days is not None and days >= VERY_STALE_LISTING_DAYS
        if not stale and changes < 3:
            return []

        return [
            RiskSignal(
                risk_type=RiskType.LISTING_BEHAVIOUR,
                severity=RiskSeverity.LOW if not very_stale else RiskSeverity.MODERATE,
                title="Listing has been on the market unusually long",
                evidence=tuple(evidence),
                interpretation=(
                    "Persistent listings and repeated reductions usually mean the market "
                    "has not agreed with the price. That is negotiating leverage for a "
                    "buyer. Occasionally it reflects something buyers discovered at "
                    "inspection and walked away from."
                ),
                recommended_verification=(
                    "Ask how many people have viewed the car and whether any inspection "
                    "found something that ended a sale."
                ),
                source="listing history",
                confidence=0.8,
                strength=EvidenceStrength.MEDIUM,
            )
        ]

    def _disclosure_signals(
        self, subject: SubjectVehicle, disclosures: DisclosureReading
    ) -> list[RiskSignal]:
        """Damage and repaint that the seller has themselves stated."""
        out: list[RiskSignal] = []

        damage = subject.has_damage_disclosure
        if damage is None:
            damage = disclosures.damage
        repaint = subject.has_repaint_disclosure
        if repaint is None:
            repaint = disclosures.repaint

        if damage is True:
            out.append(
                RiskSignal(
                    risk_type=RiskType.DAMAGE_DISCLOSURE,
                    severity=RiskSeverity.MODERATE,
                    title="Accident damage has been disclosed",
                    evidence=("The listing or the seller states the vehicle has been damaged.",),
                    interpretation=(
                        "A disclosed repair is better information than an undisclosed one. "
                        "What matters is which structural areas were affected and the "
                        "quality of the repair, neither of which can be judged from a "
                        "listing."
                    ),
                    recommended_verification=(
                        "Have the chassis and structural members inspected, and measure "
                        "paint thickness across all panels."
                    ),
                    source="seller disclosure",
                    confidence=0.95,
                    strength=EvidenceStrength.STRONG,
                )
            )

        if repaint is True:
            out.append(
                RiskSignal(
                    risk_type=RiskType.DAMAGE_DISCLOSURE,
                    severity=RiskSeverity.LOW,
                    title="Repainted panels have been disclosed",
                    evidence=("The listing or the seller states one or more panels were repainted.",),
                    interpretation=(
                        "Repainting is common and often cosmetic — stone chips, parking "
                        "scuffs, faded panels. It can also follow accident repair. Which "
                        "panels were done, and why, is the distinguishing question."
                    ),
                    recommended_verification=(
                        "Ask which specific panels were repainted and why, then verify "
                        "with a paint-thickness gauge."
                    ),
                    source="seller disclosure",
                    confidence=0.9,
                    strength=EvidenceStrength.MEDIUM,
                )
            )

        if disclosures.needs_repair:
            out.append(
                RiskSignal(
                    risk_type=RiskType.DAMAGE_DISCLOSURE,
                    severity=RiskSeverity.HIGH,
                    title="Listing states the vehicle needs repair",
                    evidence=("The description indicates outstanding mechanical work.",),
                    interpretation=(
                        "The asking price may already account for this, but the cost of "
                        "the outstanding work is the decisive number and it is not stated."
                    ),
                    recommended_verification=(
                        "Get a written repair estimate from an independent workshop before "
                        "agreeing any price."
                    ),
                    source="seller disclosure",
                    confidence=0.85,
                    strength=EvidenceStrength.STRONG,
                )
            )

        return out

    def _configuration_anomaly(
        self, comparables: ComparableSet, valuation: Valuation
    ) -> list[RiskSignal]:
        """Thin or widened comparable evidence (spec §21).

        This is a risk to *the analysis*, not to the vehicle, and it is labelled
        as such. A buyer should know when our own numbers are weakly supported.
        """
        if comparables.size >= 15 and not comparables.widened:
            return []

        evidence = [f"Only {comparables.size} comparable listings were available."]
        if comparables.widened:
            evidence.append(
                f"The search had to be widened to {comparables.key_level_used.label} "
                f"to reach that number."
            )
        if valuation.ok and valuation.range_width_pct:
            evidence.append(
                f"The resulting fair-market range spans {valuation.range_width_pct:.0f}% "
                f"of the central estimate."
            )

        return [
            RiskSignal(
                risk_type=RiskType.CONFIGURATION_ANOMALY,
                severity=RiskSeverity.LOW,
                title="This configuration is thinly represented in the local market",
                evidence=tuple(evidence),
                interpretation=(
                    "Rare configurations are harder to price and can be harder to resell. "
                    "The valuation here is correspondingly less certain — which is "
                    "reflected in the confidence score rather than hidden."
                ),
                recommended_verification=(
                    "Consider how easily you could resell this specification locally, and "
                    "treat the estimated range as indicative rather than firm."
                ),
                source="comparable coverage",
                confidence=0.75,
                strength=EvidenceStrength.MEDIUM,
            )
        ]

    def _unverified_claims(self, disclosures: DisclosureReading) -> list[RiskSignal]:
        """Unqualified superlatives in the description (spec §21).

        The lowest-severity signal in the system, and intentionally so. This is
        not a bad-faith indicator; it exists because the product's core promise
        is separating claims from verified facts, and a superlative is a claim.
        """
        if not disclosures.unverified_superlatives:
            return []
        return [
            RiskSignal(
                risk_type=RiskType.UNVERIFIED_SELLER_CLAIM,
                severity=RiskSeverity.INFO,
                title="Condition claims in the listing are unverified",
                evidence=(
                    "The description makes strong condition claims "
                    '("ideal condition", "like new" or similar).',
                ),
                interpretation=(
                    "Such descriptions are normal in listings and are not a warning sign. "
                    "They simply carry no independent backing, and the report treats them "
                    "as claims rather than facts."
                ),
                recommended_verification=(
                    "Treat condition statements as a starting point for inspection rather "
                    "than a substitute for one."
                ),
                source="listing description",
                confidence=0.7,
                strength=EvidenceStrength.WEAK,
            )
        ]

    # --- positives ---------------------------------------------------------

    def _positives(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        disclosures: DisclosureReading,
    ) -> list[PositiveSignal]:
        out: list[PositiveSignal] = []

        if comparables.size >= 25 and not comparables.widened:
            out.append(
                PositiveSignal(
                    title="Well-supported market evidence",
                    evidence=(
                        f"{comparables.size} closely-matching comparable listings were "
                        f"found without widening the search.",
                    ),
                    source="comparable coverage",
                )
            )

        if subject.service_records_provided:
            out.append(
                PositiveSignal(
                    title="Service records available",
                    evidence=("Maintenance documentation was provided for review.",),
                    source="user upload",
                )
            )

        if disclosures.damage is False:
            out.append(
                PositiveSignal(
                    title="Seller explicitly states no accident damage",
                    evidence=(
                        "The listing states the vehicle has not been in an accident. "
                        "This remains a seller claim until independently verified.",
                    ),
                    source="seller disclosure",
                )
            )

        if subject.mileage_km is not None and comparables.matches:
            comp_mileages = [
                float(m.listing.mileage_km)
                for m in comparables.matches
                if m.listing.mileage_km is not None
            ]
            if len(comp_mileages) >= 5:
                comp_median = median(comp_mileages)
                if comp_median > 0 and subject.mileage_km <= comp_median * 0.85:
                    out.append(
                        PositiveSignal(
                            title="Mileage below comparable vehicles",
                            evidence=(
                                f"{subject.mileage_km:,} km against a comparable median of "
                                f"{comp_median:,.0f} km.",
                            ),
                            source="market comparison",
                        )
                    )

        if valuation.ok and valuation.dispersion < 0.08:
            out.append(
                PositiveSignal(
                    title="Market agrees closely on this configuration's value",
                    evidence=(
                        f"Comparable prices vary by only about {valuation.dispersion:.0%} "
                        f"around the median after adjustment.",
                    ),
                    source="market statistics",
                )
            )

        return out
