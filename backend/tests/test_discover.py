"""Budget estimation.

The budget is the only number in this product inferred about a person rather
than measured from the market, which makes it the easiest one to get quietly
wrong. These cover the quantile arithmetic underneath it.

The choice it protects — a median rather than a mean — was checked against the
running system: one 250,000 AZN car opened out of curiosity moved an estimate
built from three ordinary ones by 1,500 AZN. A mean would have moved it by
57,250.
"""

from __future__ import annotations

import pytest

from app.services.discover import MIN_OBSERVATIONS, _quantile


class TestQuantile:
    def test_the_median_of_an_odd_sample(self) -> None:
        assert _quantile([10.0, 20.0, 30.0], 0.5) == pytest.approx(20.0)

    def test_the_median_of_an_even_sample_interpolates(self) -> None:
        assert _quantile([10.0, 20.0, 30.0, 40.0], 0.5) == pytest.approx(25.0)

    def test_the_quartiles_of_a_flat_sample(self) -> None:
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert _quantile(values, 0.25) == pytest.approx(20.0)
        assert _quantile(values, 0.75) == pytest.approx(40.0)

    def test_one_value_is_its_own_quantile(self) -> None:
        assert _quantile([42.0], 0.25) == pytest.approx(42.0)

    def test_an_extreme_value_barely_moves_the_median(self) -> None:
        """The property the estimate depends on.

        Someone opening one car far outside their range should not have every
        recommendation afterwards quietly repriced around it.
        """
        ordinary = [18000.0, 21000.0, 24000.0]
        with_outlier = sorted([*ordinary, 250000.0])

        median_shift = _quantile(with_outlier, 0.5) - _quantile(ordinary, 0.5)
        mean_shift = sum(with_outlier) / len(with_outlier) - sum(ordinary) / len(ordinary)

        assert median_shift == pytest.approx(1500.0)
        assert mean_shift > median_shift * 30

    def test_an_empty_sample_is_refused_rather_than_guessed(self) -> None:
        with pytest.raises(ValueError):
            _quantile([], 0.5)


def test_the_threshold_is_more_than_a_pair() -> None:
    """Two prices are not a budget, and a number drawn from them would carry
    the same confident formatting as one drawn from twenty."""
    assert MIN_OBSERVATIONS >= 3
