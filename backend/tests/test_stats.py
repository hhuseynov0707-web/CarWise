"""Statistical primitives — the layer everything else trusts."""

from __future__ import annotations

import pytest

from app.engines.stats import (
    coefficient_of_variation,
    detect_outliers,
    median,
    percentile_rank,
    quantile_set,
    robust_sigma,
    saturating,
    theil_sen_slope,
    weighted_median,
    weighted_quantile,
)


class TestMedian:
    def test_odd_and_even_lengths(self) -> None:
        assert median([3.0, 1.0, 2.0]) == 2.0
        assert median([4.0, 1.0, 3.0, 2.0]) == 2.5

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            median([])


class TestRobustness:
    def test_median_survives_extreme_contamination(self) -> None:
        """One mistyped listing must not move the centre.

        This is the property that makes scraped market data usable at all.
        """
        clean = [40_000.0, 41_000.0, 42_000.0, 43_000.0, 44_000.0]
        contaminated = [*clean, 4_000_000.0]
        assert median(contaminated) == pytest.approx(42_500.0)
        assert sum(contaminated) / len(contaminated) > 700_000  # the mean is destroyed

    def test_robust_sigma_falls_back_when_mad_collapses(self) -> None:
        """A majority-identical sample has zero MAD but is not zero-spread."""
        values = [10.0, 10.0, 10.0, 10.0, 10.0, 25.0]
        assert robust_sigma(values) > 0

    def test_robust_sigma_of_constant_sample_is_zero(self) -> None:
        assert robust_sigma([7.0] * 6) == 0.0


class TestOutliers:
    def test_flags_extreme_value(self) -> None:
        values = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 4000.0]
        result = detect_outliers(values)
        assert result.removed_indices == (8,)

    def test_small_samples_are_never_trimmed(self) -> None:
        """Below the minimum sample there is no basis to call anything an outlier."""
        values = [10.0, 11.0, 900.0]
        result = detect_outliers(values, min_sample=8)
        assert result.removed_count == 0
        assert len(result.kept_indices) == 3

    def test_clean_sample_loses_nothing(self) -> None:
        values = [float(v) for v in range(40_000, 40_010)]
        assert detect_outliers(values).removed_count == 0


class TestWeightedQuantile:
    def test_uniform_weights_match_plain_quantile(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        assert weighted_median(values, [1.0] * 4) == pytest.approx(2.5)

    def test_weight_pulls_the_median(self) -> None:
        heavy_low = weighted_median([10.0, 20.0, 30.0], [10.0, 1.0, 1.0])
        heavy_high = weighted_median([10.0, 20.0, 30.0], [1.0, 1.0, 10.0])
        assert heavy_low < 20.0 < heavy_high

    def test_zero_weights_degrade_gracefully(self) -> None:
        assert weighted_median([1.0, 2.0, 3.0], [0.0, 0.0, 0.0]) == pytest.approx(2.0)

    def test_bounds(self) -> None:
        values = [5.0, 10.0, 15.0]
        weights = [1.0, 1.0, 1.0]
        assert weighted_quantile(values, weights, 0.0) == 5.0
        assert weighted_quantile(values, weights, 1.0) == 15.0

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            weighted_quantile([1.0, 2.0], [1.0], 0.5)

    def test_quantiles_are_monotonic(self) -> None:
        values = [float(v) for v in range(100)]
        result = quantile_set(values)
        assert result.p10 <= result.p25 <= result.p50 <= result.p75 <= result.p90


class TestPercentileRank:
    def test_midpoint(self) -> None:
        assert percentile_rank([1.0, 2.0, 3.0, 4.0], 2.5) == 50.0

    def test_ties_count_as_half(self) -> None:
        assert percentile_rank([5.0, 5.0, 5.0], 5.0) == 50.0

    def test_extremes(self) -> None:
        assert percentile_rank([1.0, 2.0, 3.0], 0.0) == 0.0
        assert percentile_rank([1.0, 2.0, 3.0], 99.0) == 100.0


class TestTheilSen:
    def test_recovers_exact_slope(self) -> None:
        fit = theil_sen_slope([0.0, 1.0, 2.0, 3.0, 4.0], [10.0, 8.0, 6.0, 4.0, 2.0])
        assert fit is not None
        assert fit.slope == pytest.approx(-2.0)
        assert fit.intercept == pytest.approx(10.0)

    def test_ignores_a_gross_outlier(self) -> None:
        """Least squares would be dragged badly here; the median of slopes is not."""
        fit = theil_sen_slope([0.0, 1.0, 2.0, 3.0, 4.0], [10.0, 8.0, 6.0, 4.0, 900.0])
        assert fit is not None
        assert fit.slope == pytest.approx(-2.0)

    def test_returns_none_without_variation(self) -> None:
        """No spread in x means no slope — and the caller must report that,
        not substitute an assumed one."""
        assert theil_sen_slope([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None

    def test_returns_none_below_three_points(self) -> None:
        assert theil_sen_slope([1.0, 2.0], [1.0, 2.0]) is None

    def test_reports_predictor_span(self) -> None:
        fit = theil_sen_slope([0.0, 5.0, 10.0], [0.0, 5.0, 10.0])
        assert fit is not None
        assert fit.x_span == 10.0


class TestDispersion:
    def test_scale_free(self) -> None:
        """The same relative spread at different price levels scores the same."""
        cheap = [9_000.0, 10_000.0, 11_000.0]
        pricey = [90_000.0, 100_000.0, 110_000.0]
        assert coefficient_of_variation(cheap) == pytest.approx(
            coefficient_of_variation(pricey), rel=1e-6
        )

    def test_single_value_has_no_dispersion(self) -> None:
        assert coefficient_of_variation([42.0]) == 0.0


class TestSaturating:
    def test_reaches_half_at_half_point(self) -> None:
        assert saturating(20.0, 20.0) == pytest.approx(0.5)

    def test_monotone_and_bounded(self) -> None:
        previous = 0.0
        for value in range(0, 500, 10):
            current = saturating(float(value), 20.0)
            assert current >= previous
            assert 0.0 <= current < 1.0
            previous = current

    def test_zero_and_negative(self) -> None:
        assert saturating(0.0, 10.0) == 0.0
        assert saturating(-5.0, 10.0) == 0.0
