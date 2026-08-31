"""What a find reports about itself.

The percentages are the whole product of this screen, and both of them are
easy to get backwards. A mileage figure with the wrong sign turns "this is
cheap because it has been driven harder" into "this is cheap and barely
used" — the opposite reading, from the same data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.finds import MIN_SAMPLE_SIZE, Find


def _find(price: str, median: str, mileage: int | None, median_mileage: int | None) -> Find:
    return Find(
        listing_id=1,
        config_id="cfg_test",
        source_url=None,
        make="BMW",
        model="530",
        model_year=2019,
        city="Bakı",
        mileage_km=mileage,
        price_azn=Decimal(price),
        median_azn=Decimal(median),
        sample_size=12,
        dispersion=0.1,
        median_mileage_km=median_mileage,
    )


class TestPriceGap:
    def test_the_gap_is_measured_against_the_median(self) -> None:
        find = _find("40000", "50000", None, None)
        assert find.below_median_pct == pytest.approx(20.0)

    def test_a_price_at_the_median_has_no_gap(self) -> None:
        assert _find("50000", "50000", None, None).below_median_pct == pytest.approx(0.0)


class TestMileageComparison:
    def test_more_mileage_than_the_median_reads_positive(self) -> None:
        """Positive means driven further, which is what would explain the price."""
        find = _find("40000", "50000", mileage=150_000, median_mileage=100_000)
        assert find.mileage_vs_median_pct == pytest.approx(50.0)

    def test_less_mileage_than_the_median_reads_negative(self) -> None:
        find = _find("40000", "50000", mileage=80_000, median_mileage=100_000)
        assert find.mileage_vs_median_pct == pytest.approx(-20.0)

    def test_an_unknown_mileage_is_not_reported_as_average(self) -> None:
        """None rather than 0. Zero would render as "level with the median",
        which is a claim, and the absent reading supports no claim at all."""
        assert _find("40000", "50000", None, 100_000).mileage_vs_median_pct is None
        assert _find("40000", "50000", 100_000, None).mileage_vs_median_pct is None

    def test_a_zero_odometer_is_treated_as_unknown(self) -> None:
        """A new car reads 0 km, and dividing that into a percentage against a
        used-market median says nothing worth printing."""
        assert _find("40000", "50000", 0, 100_000).mileage_vs_median_pct is None


class TestThresholds:
    def test_the_sample_floor_is_above_the_snapshot_floor(self) -> None:
        """Snapshots refuse to exist under five listings. Singling out one car
        from a snapshot is a stronger claim than describing the snapshot, so
        it asks for more."""
        assert MIN_SAMPLE_SIZE > 5
