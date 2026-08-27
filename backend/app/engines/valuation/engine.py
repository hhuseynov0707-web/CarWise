"""Fair market value engine (spec §12–14, §47).

The method is deliberately not a black box, and deliberately not a language
model. It is a **normalize-then-aggregate** estimator:

1. Fit correction slopes (mileage, age) *from the comparable set itself*.
2. Normalize every comparable to the subject's specification — restate each
   observed price as "what this car would be asking at the subject's mileage,
   model year and location".
3. Take a weighted median of the normalized prices as the central estimate.
4. Take weighted quantiles of the normalized prices as the range, widened for
   sample size.
5. Attribute the movement between the raw median and the final estimate to
   individual factors by **ablation** — recompute with one factor switched off
   and report the difference.

Step 1 is what keeps the engine honest. Depreciation curves for the Azerbaijani
market do not exist in any table we own, so importing a constant "X AZN per
1,000 km" would be inventing data. Fitting the slope from the comparables means
the number is always derived from observed local prices — and when the sample
cannot support a fit, the adjustment returns **exactly zero with a stated
reason** rather than a guess (audit §10.8).

Step 5 gives the baseline model genuine per-factor explainability, which is what
spec §47 and §69 require, without waiting for a gradient-boosted model.

Pure computation. No I/O, no clock beyond the ``as_of`` passed in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import AdjustmentReason, PriceBasis, ValuationOutcome
from app.domain.market import SubjectVehicle, TransactionObservation
from app.domain.money import Money
from app.engines.comparables.engine import ComparableMatch, ComparableSet
from app.engines.stats import (
    QuantileSet,
    detect_outliers,
    median,
    quantile_set,
    theil_sen_slope,
    weighted_median,
    weighted_quantile,
)

# --- Fitting thresholds ----------------------------------------------------

#: Minimum comparables carrying the predictor before a slope may be fitted.
MIN_SLOPE_POINTS = 8

#: Minimum spread of the predictor. A slope fitted across 5,000 km of variation
#: cannot be extrapolated to a 60,000 km difference without inventing precision.
MIN_MILEAGE_SPAN_KM = 25_000.0
MIN_YEAR_SPAN = 2.0

#: Minimum group sizes before a categorical effect (region, damage disclosure)
#: may be estimated by comparing subgroup medians.
MIN_GROUP_SIZE = 10
MIN_DISCLOSURE_GROUP_SIZE = 8

#: Per-factor cap, as a share of the base value. A single correction moving the
#: estimate by more than this is extrapolation, not measurement.
MAX_FACTOR_SHARE = 0.30

#: Overall guard rails on the final estimate relative to the raw market median.
MIN_ESTIMATE_SHARE = 0.45
MAX_ESTIMATE_SHARE = 2.00

#: Transactions needed before valuation switches from an asking basis to a
#: settled-price basis for this configuration (spec §9).
MIN_TRANSACTION_SAMPLE = 8

#: Quantiles used for the reported fair-market range. Roughly a central
#: two-thirds band: wide enough to be honest, narrow enough to be useful.
RANGE_LOW_Q = 0.17
RANGE_HIGH_Q = 0.83

#: Floor on range half-width, as a share of the central estimate. Prevents a
#: tight sample from implying precision the market does not actually have.
MIN_RANGE_HALF_WIDTH_SHARE = 0.025

#: Ceiling on range half-width. Beyond this the market genuinely disagrees; we
#: report the range but flag the dispersion rather than widening indefinitely.
MAX_RANGE_HALF_WIDTH_SHARE = 0.35


@dataclass(frozen=True, slots=True)
class ValuationAdjustment:
    """One factor's effect on the estimate, with its full derivation.

    ``reason`` is what makes this type worth having: an adjustment of 0 AZN
    because the sample could not support a fit is a completely different claim
    from an adjustment of 0 AZN because the subject matches the market median,
    and the report must be able to tell the user which one it is.
    """

    name: str
    amount_azn: float
    reason: AdjustmentReason
    explanation: str
    method: str = ""
    data_points: int = 0
    slope: float | None = None
    confidence: float = 0.0

    @property
    def applied(self) -> bool:
        return self.reason is AdjustmentReason.APPLIED and self.amount_azn != 0.0

    @property
    def direction(self) -> str:
        if self.amount_azn > 0:
            return "increases"
        if self.amount_azn < 0:
            return "decreases"
        return "neutral"


@dataclass(frozen=True, slots=True)
class Valuation:
    """The engine's answer, including the case where it declines to answer."""

    outcome: ValuationOutcome
    basis: PriceBasis
    central_estimate: Money | None = None
    fair_market_low: Money | None = None
    fair_market_high: Money | None = None
    raw_market_median: Money | None = None
    """Weighted median before any normalization — the starting point ablation
    attributions are measured against."""

    adjustments: tuple[ValuationAdjustment, ...] = ()
    quantiles: QuantileSet | None = None
    """Distribution of *normalized* comparable prices, for the percentile chart."""

    raw_quantiles: QuantileSet | None = None
    """Distribution of raw asking prices, for the market-context chart."""

    comparable_count: int = 0
    effective_sample_size: float = 0.0
    outliers_removed: int = 0
    dispersion: float = 0.0
    """Robust coefficient of variation of normalized prices. High values mean
    the market itself disagrees about this car."""

    insufficient_reason: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome is ValuationOutcome.OK

    @property
    def range_width_pct(self) -> float | None:
        if not self.ok or self.central_estimate is None:
            return None
        if self.fair_market_low is None or self.fair_market_high is None:
            return None
        span = self.fair_market_high.as_float() - self.fair_market_low.as_float()
        centre = self.central_estimate.as_float()
        return (span / centre * 100) if centre else None

    def applied_adjustments(self) -> tuple[ValuationAdjustment, ...]:
        return tuple(a for a in self.adjustments if a.applied)

    def unavailable_adjustments(self) -> tuple[ValuationAdjustment, ...]:
        """Factors we could not measure — surfaced as honest limitations."""
        return tuple(a for a in self.adjustments if not a.applied)


@dataclass(frozen=True, slots=True)
class _Correction:
    """A fitted correction that can be applied per comparable."""

    name: str
    slope: float | None
    reference: float | None
    data_points: int
    reason: AdjustmentReason
    explanation: str
    method: str

    def value_for(self, subject_x: float | None, comp_x: float | None) -> float:
        if self.slope is None or subject_x is None or comp_x is None:
            return 0.0
        return self.slope * (subject_x - comp_x)


@dataclass
class ValuationEngine:
    """Baseline comparable-normalization estimator.

    This is the Phase-1 model of spec §13's progression (baseline → statistical
    → gradient boosting). It is also the benchmark any future ML model must beat
    on held-out data before it is allowed to replace this one.
    """

    min_sample: int = 5

    def estimate(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        as_of: datetime,
        transactions: Sequence[TransactionObservation] = (),
    ) -> Valuation:
        """Produce a fair-market range, or decline with a reason."""
        basis = (
            PriceBasis.TRANSACTION
            if len(transactions) >= MIN_TRANSACTION_SAMPLE
            else PriceBasis.ASKING
        )

        if comparables.size < self.min_sample:
            return Valuation(
                outcome=ValuationOutcome.INSUFFICIENT_DATA,
                basis=basis,
                comparable_count=comparables.size,
                insufficient_reason=(
                    f"Only {comparables.size} comparable listing"
                    f"{'s' if comparables.size != 1 else ''} could be found for this "
                    f"configuration; at least {self.min_sample} are required before a "
                    f"market range can be estimated."
                ),
            )

        matches = list(comparables.matches)
        raw_prices = [m.price_azn for m in matches]

        # Outliers are removed before anything is fitted: one mistyped listing
        # would otherwise distort both the slopes and the median.
        outlier_result = detect_outliers(raw_prices)
        kept = [matches[i] for i in outlier_result.kept_indices]
        if len(kept) < self.min_sample:
            kept = matches  # never let filtering starve the sample
            outliers_removed = 0
        else:
            outliers_removed = outlier_result.removed_count

        prices = [m.price_azn for m in kept]
        weights = [m.weight for m in kept]
        raw_median = weighted_median(prices, weights)

        corrections = self._fit_corrections(subject, kept, raw_median)

        normalized = self._normalize(subject, kept, corrections)
        central = weighted_median(normalized, weights)
        central = self._guard(central, raw_median)

        adjustments = self._attribute(
            subject, kept, weights, corrections, raw_median, central
        )
        adjustments += self._unmeasurable_factors(subject, kept)

        low, high = self._range(normalized, weights, central, comparables)

        return Valuation(
            outcome=ValuationOutcome.OK,
            basis=basis,
            central_estimate=Money.azn(round(central)),
            fair_market_low=Money.azn(round(low)),
            fair_market_high=Money.azn(round(high)),
            raw_market_median=Money.azn(round(raw_median)),
            adjustments=adjustments,
            quantiles=quantile_set(normalized, weights),
            raw_quantiles=quantile_set(prices, weights),
            comparable_count=len(kept),
            effective_sample_size=round(comparables.effective_sample_size, 2),
            outliers_removed=outliers_removed,
            dispersion=round(_dispersion(normalized), 4),
            notes=self._notes(comparables, basis, len(transactions)),
        )

    # --- fitting -----------------------------------------------------------

    def _fit_corrections(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
        base_value: float,
    ) -> dict[str, _Correction]:
        return {
            "mileage": self._fit_mileage(subject, matches, base_value),
            "age": self._fit_age(subject, matches, base_value),
        }

    def _fit_mileage(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
        base_value: float,
    ) -> _Correction:
        """Fit the local price/mileage slope from the comparable set."""
        if subject.mileage_km is None:
            return _Correction(
                "mileage", None, None, 0, AdjustmentReason.INPUT_UNKNOWN,
                "Mileage was not provided, so no mileage adjustment could be applied.",
                "none",
            )

        points = [
            (float(m.listing.mileage_km), m.price_azn)
            for m in matches
            if m.listing.mileage_km is not None
        ]
        if len(points) < MIN_SLOPE_POINTS:
            return _Correction(
                "mileage", None, None, len(points), AdjustmentReason.INSUFFICIENT_DATA,
                f"Only {len(points)} comparable listings stated mileage; at least "
                f"{MIN_SLOPE_POINTS} are needed to measure how mileage moves price in "
                f"this segment.",
                "theil-sen",
            )

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        fit = theil_sen_slope(xs, ys)

        if fit is None or fit.x_span < MIN_MILEAGE_SPAN_KM:
            span = fit.x_span if fit else 0.0
            return _Correction(
                "mileage", None, None, len(points), AdjustmentReason.INSUFFICIENT_DATA,
                f"Comparable listings span only {span:,.0f} km of mileage, which is too "
                f"narrow to measure a reliable mileage effect.",
                "theil-sen",
            )

        if fit.slope >= 0:
            # A non-negative slope means mileage is not separable from trim or
            # condition in this sample. Reporting "no measurable effect" is
            # honest; inventing a negative slope would not be.
            return _Correction(
                "mileage", None, None, len(points), AdjustmentReason.NOT_MATERIAL,
                "No reliable price/mileage relationship could be measured in this "
                "comparable set — other differences between the cars dominate.",
                "theil-sen",
            )

        return _Correction(
            "mileage",
            slope=fit.slope,
            reference=median(xs),
            data_points=len(points),
            reason=AdjustmentReason.APPLIED,
            explanation=(
                f"Measured from {len(points)} comparable listings: roughly "
                f"{abs(fit.slope) * 1000:,.0f} AZN per 1,000 km in this segment."
            ),
            method="theil-sen",
        )

    def _fit_age(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
        base_value: float,
    ) -> _Correction:
        """Fit the local price/model-year slope from the comparable set."""
        subject_year = subject.configuration.model_year
        if subject_year is None:
            return _Correction(
                "age", None, None, 0, AdjustmentReason.INPUT_UNKNOWN,
                "Model year was not provided, so no age adjustment could be applied.",
                "none",
            )

        points = [
            (float(m.listing.configuration.model_year), m.price_azn)
            for m in matches
            if m.listing.configuration.model_year is not None
        ]
        if len(points) < MIN_SLOPE_POINTS:
            return _Correction(
                "age", None, None, len(points), AdjustmentReason.INSUFFICIENT_DATA,
                f"Only {len(points)} comparable listings stated a model year; at least "
                f"{MIN_SLOPE_POINTS} are needed to measure the year-on-year effect.",
                "theil-sen",
            )

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        fit = theil_sen_slope(xs, ys)

        if fit is None or fit.x_span < MIN_YEAR_SPAN:
            return _Correction(
                "age", None, None, len(points), AdjustmentReason.INSUFFICIENT_DATA,
                "Comparable listings cover too narrow a range of model years to "
                "measure a year-on-year price effect.",
                "theil-sen",
            )

        if fit.slope <= 0:
            return _Correction(
                "age", None, None, len(points), AdjustmentReason.NOT_MATERIAL,
                "No reliable price/model-year relationship could be measured in this "
                "comparable set.",
                "theil-sen",
            )

        return _Correction(
            "age",
            slope=fit.slope,
            reference=median(xs),
            data_points=len(points),
            reason=AdjustmentReason.APPLIED,
            explanation=(
                f"Measured from {len(points)} comparable listings: roughly "
                f"{fit.slope:,.0f} AZN per model year in this segment."
            ),
            method="theil-sen",
        )

    # --- normalization -----------------------------------------------------

    def _normalize(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
        corrections: dict[str, _Correction],
        skip: str | None = None,
    ) -> list[float]:
        """Restate each comparable's price at the subject's specification.

        ``skip`` disables one correction, which is how ablation attribution
        measures that correction's contribution.
        """
        mileage = corrections["mileage"]
        age = corrections["age"]
        subject_year = (
            float(subject.configuration.model_year)
            if subject.configuration.model_year is not None
            else None
        )
        subject_km = float(subject.mileage_km) if subject.mileage_km is not None else None

        out: list[float] = []
        for match in matches:
            price = match.price_azn
            cap = price * MAX_FACTOR_SHARE

            if skip != "mileage":
                comp_km = (
                    float(match.listing.mileage_km)
                    if match.listing.mileage_km is not None
                    else None
                )
                price += _clamp_abs(mileage.value_for(subject_km, comp_km), cap)

            if skip != "age":
                comp_year = (
                    float(match.listing.configuration.model_year)
                    if match.listing.configuration.model_year is not None
                    else None
                )
                price += _clamp_abs(age.value_for(subject_year, comp_year), cap)

            out.append(max(1.0, price))
        return out

    # --- attribution -------------------------------------------------------

    def _attribute(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
        weights: Sequence[float],
        corrections: dict[str, _Correction],
        raw_median: float,
        central: float,
    ) -> tuple[ValuationAdjustment, ...]:
        """Attribute the move from raw median to estimate, factor by factor.

        Ablation rather than algebra: recompute the estimate with one correction
        disabled and take the difference. This stays correct even though the
        aggregation is a median, which does not decompose additively.
        """
        adjustments: list[ValuationAdjustment] = []

        for name in ("mileage", "age"):
            correction = corrections[name]
            if correction.reason is not AdjustmentReason.APPLIED:
                adjustments.append(
                    ValuationAdjustment(
                        name=name,
                        amount_azn=0.0,
                        reason=correction.reason,
                        explanation=correction.explanation,
                        method=correction.method,
                        data_points=correction.data_points,
                    )
                )
                continue

            without = weighted_median(
                self._normalize(subject, matches, corrections, skip=name), weights
            )
            contribution = central - self._guard(without, raw_median)

            adjustments.append(
                ValuationAdjustment(
                    name=name,
                    amount_azn=round(contribution),
                    reason=AdjustmentReason.APPLIED,
                    explanation=correction.explanation,
                    method=f"{correction.method} + ablation",
                    data_points=correction.data_points,
                    slope=correction.slope,
                    confidence=_slope_confidence(correction.data_points),
                )
            )

        return tuple(adjustments)

    def _unmeasurable_factors(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
    ) -> tuple[ValuationAdjustment, ...]:
        """Report the factors we deliberately did not estimate, and why.

        These appear in the report's limitations section. Naming what could not
        be measured is part of the product's trust proposition (spec §32): the
        absence of a condition adjustment is information the buyer should have.
        """
        out: list[ValuationAdjustment] = []

        out.append(self._geographic_factor(subject, matches))
        out.append(self._disclosure_factor(subject, matches))

        out.append(
            ValuationAdjustment(
                name="seasonality",
                amount_azn=0.0,
                reason=AdjustmentReason.INSUFFICIENT_HISTORY,
                explanation=(
                    "Seasonal price patterns require at least a full year of market "
                    "history, which this dataset does not yet cover."
                ),
                method="none",
            )
        )
        out.append(
            ValuationAdjustment(
                name="market_demand",
                amount_azn=0.0,
                reason=AdjustmentReason.INSUFFICIENT_HISTORY,
                explanation=(
                    "Supply and demand pressure is measured from changes in listing "
                    "inventory over time; not enough historical snapshots exist yet."
                ),
                method="none",
            )
        )
        return tuple(out)

    def _geographic_factor(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
    ) -> ValuationAdjustment:
        """Estimate a regional price level difference, if the data supports it."""
        if not subject.region or subject.region == "UNKNOWN":
            return ValuationAdjustment(
                "geography", 0.0, AdjustmentReason.INPUT_UNKNOWN,
                "No location was provided, so no regional adjustment could be applied.",
                "none",
            )

        local = [m.price_azn for m in matches if m.listing.region == subject.region]
        other = [m.price_azn for m in matches if m.listing.region != subject.region]

        if len(local) < MIN_GROUP_SIZE or len(other) < MIN_GROUP_SIZE:
            return ValuationAdjustment(
                "geography", 0.0, AdjustmentReason.INSUFFICIENT_DATA,
                f"Measuring a regional price difference needs at least "
                f"{MIN_GROUP_SIZE} comparable listings inside and outside "
                f"{subject.region}; this sample has {len(local)} and {len(other)}.",
                "subgroup median", len(local) + len(other),
            )

        difference = median(local) - median(other)
        return ValuationAdjustment(
            "geography",
            round(difference),
            AdjustmentReason.APPLIED,
            f"Comparable listings in {subject.region} sit about "
            f"{abs(difference):,.0f} AZN "
            f"{'above' if difference > 0 else 'below'} those elsewhere.",
            "subgroup median",
            len(local) + len(other),
            confidence=_slope_confidence(min(len(local), len(other))),
        )

    def _disclosure_factor(
        self,
        subject: SubjectVehicle,
        matches: Sequence[ComparableMatch],
    ) -> ValuationAdjustment:
        """Estimate the market discount attached to a disclosed defect."""
        if subject.has_damage_disclosure is not True:
            return ValuationAdjustment(
                "condition_disclosure", 0.0, AdjustmentReason.INPUT_UNKNOWN,
                "No damage or repaint disclosure was recorded for this vehicle. "
                "Absence of a disclosure is not evidence that none exists.",
                "none",
            )

        disclosed = [m.price_azn for m in matches if m.listing.has_damage_disclosure is True]
        clean = [m.price_azn for m in matches if m.listing.has_damage_disclosure is False]

        if len(disclosed) < MIN_DISCLOSURE_GROUP_SIZE or len(clean) < MIN_DISCLOSURE_GROUP_SIZE:
            return ValuationAdjustment(
                "condition_disclosure", 0.0, AdjustmentReason.INSUFFICIENT_DATA,
                f"Too few comparable listings disclose damage status "
                f"({len(disclosed)} disclosed, {len(clean)} explicitly clean) to "
                f"measure what the market discounts for it.",
                "subgroup median", len(disclosed) + len(clean),
            )

        difference = median(disclosed) - median(clean)
        return ValuationAdjustment(
            "condition_disclosure",
            round(difference),
            AdjustmentReason.APPLIED,
            f"Comparable listings disclosing damage ask about {abs(difference):,.0f} AZN "
            f"less than those explicitly stated as undamaged.",
            "subgroup median",
            len(disclosed) + len(clean),
            confidence=_slope_confidence(min(len(disclosed), len(clean))),
        )

    # --- range and guards --------------------------------------------------

    def _range(
        self,
        normalized: Sequence[float],
        weights: Sequence[float],
        central: float,
        comparables: ComparableSet,
    ) -> tuple[float, float]:
        """Fair-market range from the spread of normalized comparable prices.

        Distribution-free: quantiles of the normalized prices rather than a
        symmetric interval around the centre, because used-car prices are
        right-skewed and a symmetric band would misstate both ends.

        The band is then widened by ``sqrt(1 + 1/n_eff)`` — the standard
        prediction-interval inflation — so a thin sample produces a visibly
        wider range instead of false precision.
        """
        low = weighted_quantile(normalized, weights, RANGE_LOW_Q)
        high = weighted_quantile(normalized, weights, RANGE_HIGH_Q)

        n_eff = max(1.0, comparables.effective_sample_size)
        inflation = (1.0 + 1.0 / n_eff) ** 0.5

        low = central - (central - low) * inflation
        high = central + (high - central) * inflation

        min_half = central * MIN_RANGE_HALF_WIDTH_SHARE
        max_half = central * MAX_RANGE_HALF_WIDTH_SHARE

        low = min(low, central - min_half)
        high = max(high, central + min_half)
        low = max(low, central - max_half)
        high = min(high, central + max_half)

        return max(1.0, low), high

    def _guard(self, value: float, raw_median: float) -> float:
        """Keep the estimate within sane bounds of the observed market.

        A correction chain that moves the estimate to half or double the market
        median has stopped measuring and started extrapolating.
        """
        return min(
            max(value, raw_median * MIN_ESTIMATE_SHARE),
            raw_median * MAX_ESTIMATE_SHARE,
        )

    def _notes(
        self, comparables: ComparableSet, basis: PriceBasis, transaction_count: int
    ) -> tuple[str, ...]:
        notes: list[str] = []

        if basis is PriceBasis.ASKING:
            notes.append(
                "This range reflects asking prices, not confirmed sale prices. "
                "Vehicles in this market frequently sell below their listed price."
            )
        if transaction_count and basis is PriceBasis.ASKING:
            notes.append(
                f"{transaction_count} reported sale price"
                f"{'s were' if transaction_count != 1 else ' was'} available but that is "
                f"below the {MIN_TRANSACTION_SAMPLE} needed to price on settled sales; "
                f"they are shown as supporting evidence only."
            )
        if comparables.widened:
            notes.append(
                f"The comparable set was widened to {comparables.tier_used.name.replace('_', ' ').lower()} "
                f"({comparables.key_level_used.label}) to reach a usable sample size."
            )
        if comparables.size < 15:
            notes.append(
                f"Based on {comparables.size} comparable listings — a small sample, "
                f"reflected in the width of the range and in the confidence score."
            )
        return tuple(notes)


# --- helpers ---------------------------------------------------------------


def _clamp_abs(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return max(-cap, min(cap, value))


def _slope_confidence(data_points: int) -> float:
    """Crude but monotone confidence in a fitted effect, from its sample size.

    Reported alongside the adjustment so a correction fitted on 9 points is not
    presented with the same authority as one fitted on 90.
    """
    if data_points <= 0:
        return 0.0
    return round(min(0.95, data_points / (data_points + 20.0) * 1.6), 3)


def _dispersion(values: Sequence[float]) -> float:
    from app.engines.stats import coefficient_of_variation

    return coefficient_of_variation(list(values))


@dataclass(frozen=True, slots=True)
class ValuationConfig:
    """Injectable knobs, so policy lives in configuration rather than in code."""

    min_sample: int = 5
    range_low_q: float = RANGE_LOW_Q
    range_high_q: float = RANGE_HIGH_Q


def build_engine(config: ValuationConfig | None = None) -> ValuationEngine:
    cfg = config or ValuationConfig()
    return ValuationEngine(min_sample=cfg.min_sample)
