"""Deal rating and price-gap explanation (spec §17, §18, §19, §38).

Two related jobs.

**Rating** turns the numbers into a category a person can act on. The threshold
that decides the category is expressed in units of *the market's own
dispersion*, not in fixed percentages. In a tightly-agreed segment a 5% premium
is a real premium; in a scattered one it is noise. A fixed "±5% = fair" rule
would call both the same thing, and would be wrong in at least one of them.

**Gap explanation** answers spec §19 — "why is this car cheaper?" — with
arithmetic rather than speculation. Because the valuation engine fitted the
local mileage and model-year slopes, we can price each difference:

    higher mileage       explains about  -2,900 AZN
    one model year older explains about  -2,200 AZN
    disclosed repaint    explains about    -800 AZN
    ------------------------------------------------
    unexplained remainder                -3,400 AZN

The unexplained remainder is the honest, valuable output. It is the part a
buyer needs to go and investigate, and no amount of language-model prose can
substitute for it.

Pure computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import DealRating, RiskSeverity, ValuationOutcome
from app.domain.market import SubjectVehicle
from app.domain.money import Money
from app.engines.comparables.engine import ComparableSet
from app.engines.risk.engine import RiskAssessment
from app.engines.stats import median, percentile_rank
from app.engines.valuation.engine import Valuation

#: Rating thresholds, in units of half the fair-market range width:
#: ``z = (asking - central) / half_width``.
#:
#: Because the range spans roughly the central two-thirds of comparable
#: prices, ``z = 1`` means "asking more than about five in six comparable
#: cars". The FAIR band is deliberately wide: most listings genuinely are
#: fairly priced, and a rating system that calls a 2% premium "high priced"
#: trains users to ignore it.
Z_SUSPICIOUS = -2.0
Z_GREAT = -1.2
Z_GOOD = -0.5
Z_FAIR_HIGH = 0.6
Z_HIGH_PRICED = 1.3

#: Absolute percentage gates. A rating must clear BOTH its dispersion
#: threshold and its percentage floor. The dispersion term makes the rating
#: sensitive to how much the market agrees; the percentage floor stops an
#: unusually tight segment from turning a 2% premium into a red badge.
MIN_MEANINGFUL_PCT = 2.5
PCT_GREAT = -6.0
PCT_SUSPICIOUS = -18.0
PCT_HIGH = 2.5
PCT_OVERPRICED = 8.0

#: A risk score above this prevents the top rating. Spec §17 defines GREAT
#: VALUE as below-market pricing *without major risk signals*.
GREAT_VALUE_RISK_CEILING = 40

#: Share of the gap that must remain unexplained before it is called out.
MATERIAL_UNEXPLAINED_SHARE = 0.35


@dataclass(frozen=True, slots=True)
class PriceGapComponent:
    """One quantified contributor to the gap between asking and fair value."""

    factor: str
    label: str
    amount_azn: float
    """Signed, in the direction of its effect on the asking price."""

    evidence: str
    derived_from: str


@dataclass(frozen=True, slots=True)
class PriceGapAnalysis:
    """Exact decomposition of the gap between asking price and market median.

    The identity is arithmetic, not approximate::

        asking - raw_median  =  (central - raw_median)  +  (asking - central)
          total gap                 explained               unexplained

    ``explained`` is the sum of the valuation adjustments that were actually
    fitted from data — this vehicle's mileage, model year and disclosed
    condition relative to the comparable set. ``unexplained`` is what remains
    after all of that, and it is the number a buyer should care about: the part
    of the discount (or premium) that the measurable facts do not account for.

    Anchoring on the raw market median rather than the central estimate matters.
    The central estimate has *already* been adjusted for this car's mileage and
    age, so explaining the asking-vs-central gap with mileage would count the
    same effect twice.
    """

    total_gap_azn: float
    """Asking price minus the raw weighted median of comparable asking prices."""

    explained_azn: float
    """Central estimate minus the raw median — the fitted adjustments."""

    unexplained_azn: float
    """Asking price minus the central estimate."""

    components: tuple[PriceGapComponent, ...]
    reference_median_azn: float
    direction: str
    """``"below"`` or ``"above"`` the comparable median."""

    @property
    def explained_share(self) -> float:
        """Share of the total gap the measurable factors account for.

        Clamped to ``[0, 1]``. Values are naturally clamped at the top when the
        adjustments over-explain the gap, which happens whenever the asking
        price sits on the opposite side of the median from the adjustments.
        """
        if self.total_gap_azn == 0:
            return 1.0
        share = 1.0 - abs(self.unexplained_azn) / abs(self.total_gap_azn)
        return max(0.0, min(1.0, share))

    @property
    def has_material_unexplained(self) -> bool:
        return (
            abs(self.total_gap_azn) > 0
            and (1.0 - self.explained_share) >= MATERIAL_UNEXPLAINED_SHARE
        )

    @property
    def is_discount(self) -> bool:
        return self.unexplained_azn < 0



@dataclass(frozen=True, slots=True)
class PricePosition:
    """Where the asking price sits against the market (spec §16, §18)."""

    rating: DealRating
    asking_price: Money | None
    central_estimate: Money | None
    difference_azn: float | None
    difference_pct: float | None
    dispersion_z: float | None
    """Deviation in units of half the range width — the value the rating uses."""

    percentile: float | None
    """Percentile of the asking price among comparable asking prices."""

    within_range: bool | None
    rationale: tuple[str, ...]
    gap_analysis: PriceGapAnalysis | None = None

    @property
    def label(self) -> str:
        return {
            DealRating.GREAT_VALUE: "Great value",
            DealRating.GOOD_VALUE: "Good value",
            DealRating.FAIR_VALUE: "Fair value",
            DealRating.HIGH_PRICED: "High priced",
            DealRating.OVERPRICED: "Overpriced",
            DealRating.SUSPICIOUSLY_CHEAP: "Suspiciously cheap",
            DealRating.INSUFFICIENT_DATA: "Not enough data",
        }[self.rating]


@dataclass
class RatingEngine:
    """Positions an asking price against the computed market range."""

    def evaluate(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        risk: RiskAssessment,
        as_of: datetime,
    ) -> PricePosition:
        if valuation.outcome is not ValuationOutcome.OK or valuation.central_estimate is None:
            return PricePosition(
                rating=DealRating.INSUFFICIENT_DATA,
                asking_price=subject.asking_price,
                central_estimate=None,
                difference_azn=None,
                difference_pct=None,
                dispersion_z=None,
                percentile=None,
                within_range=None,
                rationale=(
                    valuation.insufficient_reason
                    or "There is not enough comparable market data to position this price.",
                ),
            )

        if subject.asking_price is None:
            return PricePosition(
                rating=DealRating.INSUFFICIENT_DATA,
                asking_price=None,
                central_estimate=valuation.central_estimate,
                difference_azn=None,
                difference_pct=None,
                dispersion_z=None,
                percentile=None,
                within_range=None,
                rationale=("No asking price was provided, so no market position can be given.",),
            )

        asking = subject.asking_price.as_float()
        central = valuation.central_estimate.as_float()
        low = valuation.fair_market_low.as_float() if valuation.fair_market_low else central
        high = valuation.fair_market_high.as_float() if valuation.fair_market_high else central

        difference = asking - central
        difference_pct = (difference / central * 100) if central else 0.0
        half_width = max((high - low) / 2.0, central * 0.01)
        z = difference / half_width

        percentile = (
            percentile_rank(list(comparables.prices), asking) if comparables.matches else None
        )
        gap = self._explain_gap(subject, comparables, valuation)
        rating = self._classify(z, difference_pct, risk, gap)

        return PricePosition(
            rating=rating,
            asking_price=subject.asking_price,
            central_estimate=valuation.central_estimate,
            difference_azn=round(difference),
            difference_pct=round(difference_pct, 1),
            dispersion_z=round(z, 2),
            percentile=round(percentile, 1) if percentile is not None else None,
            within_range=low <= asking <= high,
            rationale=self._rationale(
                subject, comparables, valuation, rating, difference_pct, percentile, risk
            ),
            gap_analysis=gap,
        )

    def _classify(
        self,
        z: float,
        difference_pct: float,
        risk: RiskAssessment,
        gap: PriceGapAnalysis | None,
    ) -> DealRating:
        """Map price position to a category.

        Every non-FAIR rating must clear two independent tests: a
        dispersion-relative one (``z``) and an absolute percentage one. Either
        alone misleads — dispersion alone over-reacts in tightly-agreed
        segments, percentage alone ignores whether the market agrees at all.

        SUSPICIOUSLY CHEAP carries a third condition, from spec §17: the
        deviation must be *unexplained*. A 200,000 km car asking 25% below the
        median is not suspicious; it is a high-mileage car, and the mileage
        adjustment already accounts for it. Only a discount the measurable
        facts cannot explain earns the label.
        """
        if abs(difference_pct) < MIN_MEANINGFUL_PCT:
            return DealRating.FAIR_VALUE

        if z <= Z_SUSPICIOUS and difference_pct <= PCT_SUSPICIOUS:
            unexplained = gap is not None and gap.has_material_unexplained
            if unexplained:
                return DealRating.SUSPICIOUSLY_CHEAP
            # Large but fully accounted for: still a strong price, and the
            # explanation belongs in the report rather than in a warning badge.
            return (
                DealRating.GREAT_VALUE
                if risk.score <= GREAT_VALUE_RISK_CEILING
                else DealRating.GOOD_VALUE
            )

        if z <= Z_GREAT and difference_pct <= PCT_GREAT:
            # Spec §17: the top rating requires the absence of major risk signals.
            if risk.score > GREAT_VALUE_RISK_CEILING:
                return DealRating.GOOD_VALUE
            return DealRating.GREAT_VALUE

        if z <= Z_GOOD and difference_pct <= -MIN_MEANINGFUL_PCT:
            return DealRating.GOOD_VALUE

        if z >= Z_HIGH_PRICED and difference_pct >= PCT_OVERPRICED:
            return DealRating.OVERPRICED

        if z >= Z_FAIR_HIGH and difference_pct >= PCT_HIGH:
            return DealRating.HIGH_PRICED

        return DealRating.FAIR_VALUE

    def _rationale(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        rating: DealRating,
        difference_pct: float,
        percentile: float | None,
        risk: RiskAssessment,
    ) -> tuple[str, ...]:
        """The "why?" behind the badge (spec §55). Never an unexplained label."""
        out: list[str] = []

        direction = "above" if difference_pct > 0 else "below"
        out.append(
            f"Asking price is {abs(difference_pct):.1f}% {direction} the estimated central "
            f"value of {valuation.central_estimate.format()}."
        )
        out.append(
            f"The estimate is drawn from {comparables.size} comparable listings "
            f"({comparables.key_level_used.label}), with a fair range of "
            f"{valuation.fair_market_low.format()}–{valuation.fair_market_high.format()}."
        )
        if percentile is not None:
            out.append(
                f"Roughly {percentile:.0f}% of comparable listings ask less than this one."
            )

        high_signals = [s for s in risk.signals if s.rank >= 2]
        if rating is DealRating.GREAT_VALUE:
            out.append("No major risk indicators were detected in the available data.")
        elif rating is DealRating.GOOD_VALUE and risk.score > GREAT_VALUE_RISK_CEILING:
            out.append(
                f"Pricing alone would suggest a stronger rating, but {len(high_signals)} "
                f"risk indicator{'s' if len(high_signals) != 1 else ''} held it back."
            )
        elif rating is DealRating.SUSPICIOUSLY_CHEAP:
            out.append(
                "A gap this large relative to the spread of comparable prices warrants "
                "explanation before proceeding — it is a prompt to investigate, not a "
                "conclusion about the vehicle."
            )
        return tuple(out)

    # --- gap explanation (spec §19) ----------------------------------------

    def _explain_gap(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
    ) -> PriceGapAnalysis | None:
        """Decompose asking-vs-market-median into explained and unexplained parts.

        The components are the valuation engine's own fitted adjustments, which
        were derived by ablation and therefore already sum to the difference
        between the raw median and the central estimate. Reusing them here
        rather than recomputing keeps the report internally consistent: the
        number in the gap table is the same number in the valuation table.

        Factors we could name but not price — unknown trim, unverified
        condition — are deliberately absent from the arithmetic. They belong in
        the unexplained remainder, which is exactly what makes that remainder
        worth investigating.
        """
        if (
            subject.asking_price is None
            or valuation.central_estimate is None
            or valuation.raw_market_median is None
        ):
            return None

        asking = subject.asking_price.as_float()
        central = valuation.central_estimate.as_float()
        raw_median = valuation.raw_market_median.as_float()

        total_gap = asking - raw_median
        explained = central - raw_median
        unexplained = asking - central

        if abs(total_gap) < 1.0 and abs(unexplained) < 1.0:
            return None

        components = tuple(
            PriceGapComponent(
                factor=adjustment.name,
                label=_ADJUSTMENT_LABELS.get(adjustment.name, adjustment.name.title()),
                amount_azn=adjustment.amount_azn,
                evidence=adjustment.explanation,
                derived_from=adjustment.method,
            )
            for adjustment in valuation.applied_adjustments()
        )

        return PriceGapAnalysis(
            total_gap_azn=round(total_gap),
            explained_azn=round(explained),
            unexplained_azn=round(unexplained),
            components=components,
            reference_median_azn=round(raw_median),
            direction="below" if total_gap < 0 else "above",
        )


#: Display names for valuation adjustment factors.
_ADJUSTMENT_LABELS = {
    "mileage": "Mileage vs comparable median",
    "age": "Model year vs comparable median",
    "geography": "Regional price level",
    "condition_disclosure": "Disclosed damage or repaint",
    "seasonality": "Seasonal effect",
    "market_demand": "Supply and demand",
}


def candidate_explanations(
    position: PricePosition,
    risk: RiskAssessment,
    subject: SubjectVehicle,
    as_of: datetime,
) -> tuple[str, ...]:
    """Hedged, evidence-linked possibilities for an unexplained discount.

    Spec §19 is unambiguous about the register: "possible explanation",
    "requires verification" — never "the car definitely has a hidden accident".
    Each string here is phrased as a question the buyer can go and answer.
    """
    if position.gap_analysis is None or not position.gap_analysis.is_discount:
        return ()
    if not position.gap_analysis.has_material_unexplained:
        return ()

    out: list[str] = [
        f"Approximately {abs(position.gap_analysis.unexplained_azn):,.0f} AZN of the "
        f"discount is not accounted for by mileage, model year or disclosed condition."
    ]

    if not subject.service_records_provided:
        out.append(
            "Possible explanation: incomplete maintenance history. Requires verification — "
            "ask for service invoices."
        )
    if subject.has_damage_disclosure is None:
        out.append(
            "Possible explanation: undisclosed accident repair. Requires verification — "
            "an independent body and paint inspection would establish this."
        )
    if subject.configuration.trim is None:
        out.append(
            "Possible explanation: a lower equipment level than the comparable listings. "
            "Requires verification — confirm the exact trim and options."
        )
    days = subject.days_listed(as_of)
    if days is not None and days > 45:
        out.append(
            "Possible explanation: a motivated seller after a long time on the market. "
            "This would be favourable to a buyer rather than a concern."
        )
    if any(s.severity in (RiskSeverity.HIGH, RiskSeverity.CRITICAL) for s in risk.signals):
        out.append(
            "Possible explanation: the risk indicators listed in this report. Each states "
            "how it can be verified."
        )

    out.append(
        "None of these possibilities has been confirmed. They are the questions worth "
        "answering before making a decision."
    )
    return tuple(out)
