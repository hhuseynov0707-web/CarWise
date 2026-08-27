"""Seller questions and inspection priorities (spec §29, §30).

Both outputs are generated from *detected evidence*, not from a stock checklist.
Spec §29 is explicit: "Questions must be generated from evidence." Spec §30 asks
for a prioritized plan rather than a generic list.

The design that satisfies both: a catalogue of inspection items each carrying a
baseline priority, plus **promotion rules** keyed to findings elsewhere in the
analysis. A paint-thickness check is a low-priority nicety on a car with no
concerning signals and the single highest priority on one with a disclosed
repaint and an unexplained discount. Same item, different plan, and the reason
for the difference travels with it.

No repair costs are quoted anywhere. We have no reliable local repair-cost
dataset, and spec §26 permits cost ranges only "when reliable data exists".

Pure computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import RiskSeverity, RiskType
from app.domain.market import SubjectVehicle
from app.engines.comparables.engine import ComparableSet
from app.engines.rating.engine import PricePosition
from app.engines.risk.engine import RiskAssessment
from app.engines.stats import median

#: Mileage past which wear-item and major-service checks are promoted.
HIGH_MILEAGE_KM = 150_000
VERY_HIGH_MILEAGE_KM = 220_000

#: Vehicle age past which age-related systems are promoted.
OLDER_VEHICLE_YEARS = 8


@dataclass(frozen=True, slots=True)
class SellerQuestion:
    """A question worth asking, and the finding that produced it."""

    question: str
    why: str
    """What makes this question worth the seller's time — always a specific
    observation from this analysis, never a generic rationale."""

    triggered_by: str
    priority: str
    """``"high"``, ``"medium"`` or ``"low"``."""


@dataclass(frozen=True, slots=True)
class InspectionItem:
    """One item on the prioritized inspection plan."""

    item: str
    priority: str
    reason: str
    triggered_by: str
    system: str
    """Broad area: ``structure``, ``powertrain``, ``electronics``, ``wear``,
    ``documents``, ``cosmetic``."""


@dataclass(frozen=True, slots=True)
class InspectionPlan:
    questions: tuple[SellerQuestion, ...]
    items: tuple[InspectionItem, ...]

    def by_priority(self, priority: str) -> tuple[InspectionItem, ...]:
        return tuple(i for i in self.items if i.priority == priority)

    @property
    def high(self) -> tuple[InspectionItem, ...]:
        return self.by_priority("high")

    @property
    def medium(self) -> tuple[InspectionItem, ...]:
        return self.by_priority("medium")

    @property
    def low(self) -> tuple[InspectionItem, ...]:
        return self.by_priority("low")


_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class InspectionEngine:
    """Builds the question list and inspection plan from analysis findings."""

    def build(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        position: PricePosition,
        risk: RiskAssessment,
        as_of: datetime,
    ) -> InspectionPlan:
        questions = self._questions(subject, comparables, position, risk, as_of)
        items = self._items(subject, comparables, position, risk, as_of)
        return InspectionPlan(questions=questions, items=items)

    # --- seller questions --------------------------------------------------

    def _questions(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        position: PricePosition,
        risk: RiskAssessment,
        as_of: datetime,
    ) -> tuple[SellerQuestion, ...]:
        out: list[SellerQuestion] = []

        gap = position.gap_analysis
        if gap and gap.is_discount and gap.has_material_unexplained:
            out.append(
                SellerQuestion(
                    question=(
                        "This car is priced noticeably below comparable listings even after "
                        "accounting for its mileage and model year. What is the reason for "
                        "the price?"
                    ),
                    why=(
                        f"About {abs(gap.unexplained_azn):,.0f} AZN of the discount is not "
                        f"explained by the measurable differences we could identify."
                    ),
                    triggered_by="price gap analysis",
                    priority="high",
                )
            )

        for signal in risk.by_severity():
            question = _QUESTION_FOR_RISK.get(signal.risk_type)
            if question is None:
                continue
            out.append(
                SellerQuestion(
                    question=question,
                    why=signal.evidence[0],
                    triggered_by=signal.title,
                    priority="high" if signal.rank >= 3 else "medium",
                )
            )

        if not subject.service_records_provided:
            out.append(
                SellerQuestion(
                    question="Can you provide the maintenance records and service invoices?",
                    why="No service history was supplied with this analysis.",
                    triggered_by="missing service history",
                    priority="high",
                )
            )

        if subject.owner_count is None:
            out.append(
                SellerQuestion(
                    question="How many previous owners has the car had, and how long have you owned it?",
                    why="Ownership history was not stated and affects how much of the car's life is documented.",
                    triggered_by="missing ownership history",
                    priority="medium",
                )
            )

        if not subject.vin:
            out.append(
                SellerQuestion(
                    question="Could you share the VIN so the factory specification can be confirmed?",
                    why=(
                        "Without a VIN the exact factory configuration, and therefore the "
                        "comparable set, cannot be confirmed."
                    ),
                    triggered_by="missing VIN",
                    priority="high",
                )
            )

        if subject.has_repaint_disclosure or (
            subject.description and "repaint" in subject.description.lower()
        ):
            out.append(
                SellerQuestion(
                    question="Which panels were repainted, and what was the reason for each?",
                    why="A repaint disclosure was detected; the reason distinguishes cosmetic work from accident repair.",
                    triggered_by="repaint disclosure",
                    priority="high",
                )
            )

        days = subject.days_listed(as_of)
        if days is not None and days >= 45:
            out.append(
                SellerQuestion(
                    question=(
                        "The car has been listed for a while — has anyone inspected it and "
                        "then decided against buying? If so, what did they find?"
                    ),
                    why=f"The listing has been active for {days} days.",
                    triggered_by="listing duration",
                    priority="medium",
                )
            )

        if subject.mileage_km and subject.mileage_km >= HIGH_MILEAGE_KM:
            out.append(
                SellerQuestion(
                    question=(
                        "At this mileage, which major services have been done — timing belt or "
                        "chain, transmission fluid, clutch, suspension components?"
                    ),
                    why=(
                        f"At {subject.mileage_km:,} km several major maintenance intervals "
                        f"would normally have fallen due."
                    ),
                    triggered_by="high mileage",
                    priority="high",
                )
            )

        if subject.mileage_km and comparables.matches:
            comp_km = [
                float(m.listing.mileage_km)
                for m in comparables.matches
                if m.listing.mileage_km is not None
            ]
            if len(comp_km) >= 5 and subject.mileage_km < median(comp_km) * 0.6:
                out.append(
                    SellerQuestion(
                        question=(
                            "The mileage is well below comparable cars of this age — can you "
                            "show documentation confirming the odometer reading?"
                        ),
                        why=(
                            f"{subject.mileage_km:,} km against a comparable median of "
                            f"{median(comp_km):,.0f} km. Unusually low mileage is often "
                            f"genuine, and is straightforward to confirm from service records."
                        ),
                        triggered_by="mileage below comparable range",
                        priority="high",
                    )
                )

        out.append(
            SellerQuestion(
                question="Would you agree to an independent pre-purchase inspection at a workshop of my choosing?",
                why=(
                    "A seller's answer to this is itself informative, and no analysis based "
                    "on listing data can substitute for a physical inspection."
                ),
                triggered_by="standard due diligence",
                priority="high",
            )
        )

        return tuple(_dedupe_questions(out))

    # --- inspection plan ---------------------------------------------------

    def _items(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        position: PricePosition,
        risk: RiskAssessment,
        as_of: datetime,
    ) -> tuple[InspectionItem, ...]:
        items: dict[str, InspectionItem] = {}

        def add(
            key: str,
            item: str,
            priority: str,
            reason: str,
            triggered_by: str,
            system: str,
        ) -> None:
            existing = items.get(key)
            if existing and _PRIORITY_RANK[existing.priority] <= _PRIORITY_RANK[priority]:
                return
            items[key] = InspectionItem(item, priority, reason, triggered_by, system)

        # Baseline plan. Present for every vehicle, and promoted by evidence.
        add("diagnostic", "Full diagnostic scan for stored and pending fault codes", "high",
            "Stored codes reveal faults that are not apparent on a short test drive.",
            "standard due diligence", "electronics")
        add("cold_start", "Cold engine start, observed from the first turn of the key", "high",
            "Many engine and emissions faults are only audible or visible on a genuine cold start.",
            "standard due diligence", "powertrain")
        add("structure", "Structural and chassis inspection on a lift", "medium",
            "Structural repair is the single most consequential thing that can be hidden.",
            "standard due diligence", "structure")
        add("paint", "Paint thickness measurement across all panels", "medium",
            "Reveals repainted or filled panels that the description may not mention.",
            "standard due diligence", "structure")
        add("fluids", "Fluid condition and leak inspection", "medium",
            "Fluid condition is a fast proxy for how the car has been maintained.",
            "standard due diligence", "powertrain")
        add("transmission", "Transmission behaviour under load, hot and cold", "medium",
            "Transmission repair is among the most expensive outcomes on a used purchase.",
            "standard due diligence", "powertrain")
        add("suspension", "Suspension, bushings and steering play", "low",
            "Wear items that affect handling and are negotiable if due for replacement.",
            "standard due diligence", "wear")
        add("brakes", "Brake pad, disc and fluid condition", "low",
            "Consumables — worth pricing into any offer if near replacement.",
            "standard due diligence", "wear")
        add("tyres", "Tyre age, tread depth and even wear across all four", "low",
            "Uneven wear indicates alignment or suspension problems; tyre age matters as much as tread.",
            "standard due diligence", "wear")
        add("electrics", "Electrical systems, warning lights and all comfort functions", "low",
            "Electrical faults are cheap to find now and expensive to chase later.",
            "standard due diligence", "electronics")
        add("documents", "Registration, ownership documents and VIN plate consistency", "high",
            "Document and VIN irregularities are the one category of problem no mechanical inspection catches.",
            "standard due diligence", "documents")
        add("rust", "Underbody and sill corrosion inspection", "low",
            "Corrosion is structural once it takes hold, and is easy to see on a lift.",
            "standard due diligence", "structure")

        # --- promotions from evidence --------------------------------------

        for signal in risk.signals:
            if signal.risk_type is RiskType.DAMAGE_DISCLOSURE:
                add("structure", "Structural and chassis inspection on a lift", "high",
                    "Damage has been disclosed, so the extent and quality of the repair is "
                    "the decisive question.", signal.title, "structure")
                add("paint", "Paint thickness measurement across all panels", "high",
                    "Confirms which panels were affected and whether the repair was cosmetic "
                    "or structural.", signal.title, "structure")
                add("alignment", "Wheel alignment and geometry check", "high",
                    "Alignment that will not hold is a common consequence of structural damage.",
                    signal.title, "structure")

            if signal.risk_type is RiskType.MILEAGE_ANOMALY:
                add("major_service", "Verification that mileage-due major services were performed", "high",
                    "At this mileage, timing components, transmission fluid and suspension "
                    "wear items would normally be due.", signal.title, "powertrain")
                add("compression", "Engine compression or leak-down test", "medium",
                    "Directly measures engine wear rather than inferring it from mileage.",
                    signal.title, "powertrain")

            if signal.risk_type is RiskType.MILEAGE_SEQUENCE_ANOMALY:
                add("odometer", "Odometer verification against service records and ECU data", "high",
                    "The recorded mileage sequence contains an inconsistency that should be "
                    "resolved before purchase.", signal.title, "documents")

            if signal.risk_type is RiskType.INFORMATION_INCONSISTENCY:
                add("documents", "Registration, ownership documents and VIN plate consistency", "high",
                    "Sources disagree about this vehicle's specification; the VIN plate and "
                    "documents settle it.", signal.title, "documents")

            if signal.risk_type is RiskType.MARKET_PRICE_ANOMALY and signal.rank >= 2:
                add("structure", "Structural and chassis inspection on a lift", "high",
                    "The price sits materially below comparable cars; a structural inspection "
                    "is the most direct way to rule out the most expensive explanation.",
                    signal.title, "structure")
                add("diagnostic", "Full diagnostic scan for stored and pending fault codes", "high",
                    "Establishes whether a mechanical fault explains the price.",
                    signal.title, "electronics")

        if subject.mileage_km and subject.mileage_km >= HIGH_MILEAGE_KM:
            add("major_service", "Verification that mileage-due major services were performed", "high",
                f"At {subject.mileage_km:,} km several major maintenance intervals would "
                f"normally have fallen due.", "high mileage", "powertrain")
            add("transmission", "Transmission behaviour under load, hot and cold", "high",
                "Transmission wear accumulates with distance and is expensive to remedy.",
                "high mileage", "powertrain")
        if subject.mileage_km and subject.mileage_km >= VERY_HIGH_MILEAGE_KM:
            add("compression", "Engine compression or leak-down test", "high",
                f"At {subject.mileage_km:,} km, measuring engine condition directly is worth "
                f"the cost of the test.", "very high mileage", "powertrain")

        age = _age_years(subject, as_of)
        if age is not None and age >= OLDER_VEHICLE_YEARS:
            add("rust", "Underbody and sill corrosion inspection", "medium",
                f"At {age} years old, corrosion is a realistic possibility and is structural "
                f"once established.", "vehicle age", "structure")
            add("suspension", "Suspension, bushings and steering play", "medium",
                f"Rubber components degrade with age as much as with mileage.",
                "vehicle age", "wear")

        if not subject.service_records_provided:
            add("fluids", "Fluid condition and leak inspection", "high",
                "With no service history available, fluid condition is the best available "
                "evidence of how the car has been maintained.", "missing service history",
                "powertrain")

        ordered = sorted(
            items.values(),
            key=lambda i: (_PRIORITY_RANK[i.priority], i.system, i.item),
        )
        return tuple(ordered)


#: Questions generated directly from a detected risk type.
_QUESTION_FOR_RISK: dict[RiskType, str] = {
    RiskType.DAMAGE_DISCLOSURE: (
        "Which areas of the car were damaged, who carried out the repair, and can you "
        "show the repair documentation?"
    ),
    RiskType.MILEAGE_ANOMALY: (
        "At this mileage, which wear items and major services have already been replaced?"
    ),
    RiskType.MILEAGE_SEQUENCE_ANOMALY: (
        "The recorded mileage history does not run consistently — can you explain the "
        "sequence and show documentation?"
    ),
    RiskType.INFORMATION_INCONSISTENCY: (
        "Some details in the listing do not match the vehicle's decoded specification — "
        "could we confirm the exact trim and engine against the VIN plate?"
    ),
    RiskType.LISTING_BEHAVIOUR: (
        "What has prevented the car from selling so far?"
    ),
    RiskType.HISTORY_INCOMPLETE: (
        "What documentation can you provide covering the car's history and servicing?"
    ),
}


def _dedupe_questions(questions: list[SellerQuestion]) -> list[SellerQuestion]:
    """Keep the highest-priority instance of each distinct question."""
    best: dict[str, SellerQuestion] = {}
    for question in questions:
        existing = best.get(question.question)
        if existing and _PRIORITY_RANK[existing.priority] <= _PRIORITY_RANK[question.priority]:
            continue
        best[question.question] = question
    return sorted(best.values(), key=lambda q: _PRIORITY_RANK[q.priority])


def _age_years(subject: SubjectVehicle, as_of: datetime) -> int | None:
    year = subject.configuration.model_year
    if year is None:
        return None
    return max(0, as_of.year - year)
