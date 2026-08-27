"""Robust statistical primitives for market computation.

Used-car price distributions are right-skewed and contaminated: a single
mistyped listing at 4,000,000 AZN destroys a mean and badly damages an
unweighted standard deviation (audit §7.2). Every routine here is therefore
either median-based or explicitly weighted, and outlier handling runs before
any statistic is reported.

Pure functions over plain floats. No I/O, no clock, no domain imports.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Scale factor making the median absolute deviation a consistent estimator of
# the standard deviation under normality.
_MAD_TO_SIGMA = 1.4826

# Modified z-score cutoff for outlier rejection. 3.5 is the conventional
# threshold; deliberately permissive, because genuinely cheap cars are signal,
# not noise — we want to keep them and explain them (spec §19), not discard them.
DEFAULT_OUTLIER_THRESHOLD = 3.5


def median(values: Sequence[float]) -> float:
    """Median of a non-empty sequence."""
    if not values:
        raise ValueError("median of empty sequence")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


def median_absolute_deviation(values: Sequence[float]) -> float:
    """Median of absolute deviations from the median.

    Breakdown point of 50%: it stays meaningful even if half the sample is
    corrupt, which is the property that matters for scraped market data.
    """
    if not values:
        raise ValueError("MAD of empty sequence")
    centre = median(values)
    return median([abs(v - centre) for v in values])


def robust_sigma(values: Sequence[float]) -> float:
    """MAD-based standard-deviation estimate, with a documented fallback.

    When more than half the sample shares one exact value the MAD collapses to
    zero, which would make every other point an infinite outlier. In that case
    we fall back to the mean absolute deviation.
    """
    if len(values) < 2:
        return 0.0
    mad = median_absolute_deviation(values)
    if mad > 0:
        return _MAD_TO_SIGMA * mad
    centre = median(values)
    mean_abs_dev = mean([abs(v - centre) for v in values])
    return 1.2533 * mean_abs_dev


def modified_z_scores(values: Sequence[float]) -> list[float]:
    """Per-point robust z-scores. All zeros when the sample has no spread."""
    sigma = robust_sigma(values)
    if sigma <= 0:
        return [0.0] * len(values)
    centre = median(values)
    return [(v - centre) / sigma for v in values]


@dataclass(frozen=True, slots=True)
class OutlierResult:
    """Which observations survived filtering, and why the rest did not."""

    kept_indices: tuple[int, ...]
    removed_indices: tuple[int, ...]
    threshold: float
    lower_bound: float | None
    upper_bound: float | None

    @property
    def removed_count(self) -> int:
        return len(self.removed_indices)


def detect_outliers(
    values: Sequence[float],
    threshold: float = DEFAULT_OUTLIER_THRESHOLD,
    min_sample: int = 8,
) -> OutlierResult:
    """Flag extreme values using modified z-scores.

    Below ``min_sample`` observations nothing is removed: with a handful of
    points there is not enough information to distinguish an outlier from the
    distribution, and trimming a small sample does more harm than the outlier
    does.
    """
    n = len(values)
    if n < min_sample:
        return OutlierResult(tuple(range(n)), (), threshold, None, None)

    sigma = robust_sigma(values)
    if sigma <= 0:
        return OutlierResult(tuple(range(n)), (), threshold, None, None)

    centre = median(values)
    lower = centre - threshold * sigma
    upper = centre + threshold * sigma

    kept: list[int] = []
    removed: list[int] = []
    for index, value in enumerate(values):
        (kept if lower <= value <= upper else removed).append(index)

    return OutlierResult(tuple(kept), tuple(removed), threshold, lower, upper)


def weighted_quantile(
    values: Sequence[float],
    weights: Sequence[float],
    q: float,
) -> float:
    """Weighted quantile with linear interpolation.

    Uses the standard convention of placing each observation at the midpoint of
    the cumulative-weight interval it occupies, so that with uniform weights the
    result matches a conventional interpolated quantile.

    ``q`` is a fraction in ``[0, 1]``.
    """
    if not values:
        raise ValueError("weighted_quantile of empty sequence")
    if len(values) != len(weights):
        raise ValueError("values and weights must be the same length")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")

    pairs = sorted(zip(values, weights, strict=True), key=lambda pair: pair[0])
    ordered_values = [v for v, _ in pairs]
    ordered_weights = [max(0.0, w) for _, w in pairs]

    total = sum(ordered_weights)
    if total <= 0:
        return _interpolated_quantile(ordered_values, q)

    cumulative = 0.0
    positions: list[float] = []
    for weight in ordered_weights:
        positions.append((cumulative + weight / 2.0) / total)
        cumulative += weight

    if q <= positions[0]:
        return ordered_values[0]
    if q >= positions[-1]:
        return ordered_values[-1]

    for i in range(len(positions) - 1):
        left, right = positions[i], positions[i + 1]
        if left <= q <= right:
            if right == left:
                return ordered_values[i]
            span = (q - left) / (right - left)
            return ordered_values[i] + span * (ordered_values[i + 1] - ordered_values[i])
    return ordered_values[-1]


def _interpolated_quantile(ordered: Sequence[float], q: float) -> float:
    """Unweighted interpolated quantile over an already-sorted sequence."""
    n = len(ordered)
    if n == 1:
        return ordered[0]
    position = q * (n - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    return weighted_quantile(values, weights, 0.5)


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    if not values:
        raise ValueError("weighted_mean of empty sequence")
    total_weight = sum(weights)
    if total_weight <= 0:
        return mean(values)
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total_weight


@dataclass(frozen=True, slots=True)
class QuantileSet:
    """The percentile summary shown to users (spec §16)."""

    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    sample_size: int

    @property
    def interquartile_range(self) -> float:
        return self.p75 - self.p25

    def as_dict(self) -> dict[str, float | int]:
        return {
            "p10": self.p10,
            "p25": self.p25,
            "p50": self.p50,
            "p75": self.p75,
            "p90": self.p90,
            "sample_size": self.sample_size,
        }


def quantile_set(values: Sequence[float], weights: Sequence[float] | None = None) -> QuantileSet:
    """Compute the standard percentile summary of a price distribution."""
    if not values:
        raise ValueError("quantile_set of empty sequence")
    w = list(weights) if weights is not None else [1.0] * len(values)
    return QuantileSet(
        p10=weighted_quantile(values, w, 0.10),
        p25=weighted_quantile(values, w, 0.25),
        p50=weighted_quantile(values, w, 0.50),
        p75=weighted_quantile(values, w, 0.75),
        p90=weighted_quantile(values, w, 0.90),
        sample_size=len(values),
    )


def percentile_rank(values: Sequence[float], target: float) -> float:
    """Where ``target`` sits within ``values``, as a percentage in ``[0, 100]``.

    Ties count as half, the standard definition of percentile rank, so a value
    equal to every observation ranks at 50 rather than at 0 or 100.
    """
    if not values:
        raise ValueError("percentile_rank against empty sequence")
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    return 100.0 * (below + 0.5 * equal) / len(values)


def coefficient_of_variation(values: Sequence[float]) -> float:
    """Robust dispersion relative to level: ``robust_sigma / |median|``.

    Scale-free, so a 2,000 AZN spread on a 12,000 AZN car and a 2,000 AZN
    spread on a 90,000 AZN car are correctly treated as very different levels
    of market agreement.
    """
    if len(values) < 2:
        return 0.0
    centre = median(values)
    if centre == 0:
        return 0.0
    return robust_sigma(values) / abs(centre)


@dataclass(frozen=True, slots=True)
class SlopeFit:
    """A fitted linear relationship, with enough context to judge its worth."""

    slope: float
    intercept: float
    sample_size: int
    x_span: float
    """Range of the predictor across the sample. A slope fitted over a narrow
    span extrapolates badly and should not be trusted far outside it."""

    def predict(self, x: float) -> float:
        return self.intercept + self.slope * x


def theil_sen_slope(xs: Sequence[float], ys: Sequence[float]) -> SlopeFit | None:
    """Robust linear fit: the median of all pairwise slopes.

    Chosen over least squares because a handful of contaminated listings would
    otherwise drag the fitted mileage/price relationship, and that relationship
    directly moves the headline valuation number.

    Returns ``None`` when there is no usable variation in ``x`` or fewer than
    three points — in which case the caller must report "insufficient data"
    rather than substituting an assumed slope (audit §10.8).
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must be the same length")
    n = len(xs)
    if n < 3:
        return None

    slopes: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[j] - xs[i]
            if dx == 0:
                continue
            slopes.append((ys[j] - ys[i]) / dx)

    if not slopes:
        return None

    slope = median(slopes)
    intercept = median([ys[i] - slope * xs[i] for i in range(n)])
    return SlopeFit(
        slope=slope,
        intercept=intercept,
        sample_size=n,
        x_span=max(xs) - min(xs),
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def saturating(value: float, half_point: float) -> float:
    """Map ``[0, inf)`` onto ``[0, 1)``, reaching 0.5 at ``half_point``.

    Used wherever "more is better but with diminishing returns" — notably
    comparable-count contributions to confidence, where the difference between
    5 and 25 comparables matters far more than between 200 and 220.
    """
    if value <= 0:
        return 0.0
    if half_point <= 0:
        raise ValueError("half_point must be positive")
    return value / (value + half_point)
