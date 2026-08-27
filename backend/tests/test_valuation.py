"""Valuation engine.

The central test is :class:`TestGroundTruthRecovery`. A valuation engine tested
only against real scraped prices tells you it is self-consistent. Tested against
a synthetic market whose true generating process is known, it tells you whether
it is *correct* — whether it recovers a mileage slope it was never given.

The second theme is refusal. Audit §1 establishes that a thin market is the
default state of this product, not an edge case, so declining to answer must be
tested as carefully as answering.
"""

from __future__ import annotations

import pytest

from app.domain.enums import AdjustmentReason, PriceBasis, ValuationOutcome
from app.engines.comparables.engine import ComparableEngine, SelectionPolicy
from app.engines.valuation.engine import ValuationEngine
from tests.factories import (
    REFERENCE_NOW,
    expected_price,
    make_config,
    make_listing,
    make_subject,
    synthetic_market,
)


def _analyse(subject, listings, policy: SelectionPolicy | None = None):
    comparables = ComparableEngine(policy=policy or SelectionPolicy()).select(
        subject, listings, REFERENCE_NOW
    )
    valuation = ValuationEngine().estimate(subject, comparables, REFERENCE_NOW)
    return comparables, valuation


class TestGroundTruthRecovery:
    """The engine must recover parameters it was never told."""

    def test_recovers_the_mileage_slope(self) -> None:
        listings, truth = synthetic_market(count=80, mileage_slope=-0.09, noise_pct=0.04)
        subject = make_subject(mileage_km=120_000)
        _, valuation = _analyse(subject, listings)

        mileage = next(a for a in valuation.adjustments if a.name == "mileage")
        assert mileage.reason is AdjustmentReason.APPLIED
        assert mileage.slope is not None
        assert mileage.slope == pytest.approx(truth["mileage_slope"], rel=0.25)

    def test_recovers_the_model_year_slope(self) -> None:
        listings, truth = synthetic_market(count=80, year_slope=2_200.0, noise_pct=0.04)
        subject = make_subject(mileage_km=100_000)
        _, valuation = _analyse(subject, listings)

        age = next(a for a in valuation.adjustments if a.name == "age")
        assert age.reason is AdjustmentReason.APPLIED
        assert age.slope is not None
        assert age.slope == pytest.approx(truth["year_slope"], rel=0.30)

    @pytest.mark.parametrize("mileage", [60_000, 100_000, 140_000, 180_000])
    def test_central_estimate_tracks_truth_across_the_mileage_range(
        self, mileage: int
    ) -> None:
        listings, truth = synthetic_market(count=100, noise_pct=0.04, mileage_spread=120_000)
        subject = make_subject(mileage_km=mileage, config=make_config(year=2019))
        _, valuation = _analyse(subject, listings)

        assert valuation.ok
        truth_price = expected_price(truth, mileage, 2019)
        error = abs(valuation.central_estimate.as_float() - truth_price) / truth_price
        assert error < 0.10, f"{error:.1%} error at {mileage:,} km"

    def test_true_value_falls_inside_the_reported_range(self) -> None:
        listings, truth = synthetic_market(count=100, noise_pct=0.05, mileage_spread=120_000)
        inside = 0
        mileages = [70_000, 90_000, 110_000, 130_000, 150_000]
        for mileage in mileages:
            subject = make_subject(mileage_km=mileage, config=make_config(year=2019))
            _, valuation = _analyse(subject, listings)
            truth_price = expected_price(truth, mileage, 2019)
            if (
                valuation.fair_market_low.as_float()
                <= truth_price
                <= valuation.fair_market_high.as_float()
            ):
                inside += 1
        assert inside >= len(mileages) - 1, f"only {inside}/{len(mileages)} ranges covered truth"

    def test_higher_mileage_is_always_valued_lower(self) -> None:
        """Monotonicity. A violation here would be visible to any user."""
        listings, _ = synthetic_market(count=100, mileage_spread=120_000)
        previous = float("inf")
        for mileage in (60_000, 90_000, 120_000, 150_000, 180_000):
            _, valuation = _analyse(make_subject(mileage_km=mileage), listings)
            current = valuation.central_estimate.as_float()
            assert current < previous, f"value did not fall at {mileage:,} km"
            previous = current


class TestRefusal:
    """Declining to answer is a first-class outcome (audit §1)."""

    def test_empty_market_produces_insufficient_data(self) -> None:
        _, valuation = _analyse(make_subject(), [])
        assert valuation.outcome is ValuationOutcome.INSUFFICIENT_DATA
        assert valuation.central_estimate is None
        assert valuation.insufficient_reason

    def test_thin_market_produces_insufficient_data(self) -> None:
        listings, _ = synthetic_market(count=3)
        _, valuation = _analyse(make_subject(), listings)
        assert valuation.outcome is ValuationOutcome.INSUFFICIENT_DATA
        assert "3 comparable" in valuation.insufficient_reason

    def test_refusal_states_how_many_were_found(self) -> None:
        listings, _ = synthetic_market(count=2)
        _, valuation = _analyse(make_subject(), listings)
        assert "2" in valuation.insufficient_reason

    def test_no_number_leaks_on_refusal(self) -> None:
        _, valuation = _analyse(make_subject(), [])
        assert valuation.fair_market_low is None
        assert valuation.fair_market_high is None
        assert valuation.raw_market_median is None


class TestAdjustmentHonesty:
    """An adjustment of zero must say *why* it is zero (audit §10.8)."""

    def test_missing_mileage_reports_input_unknown(self) -> None:
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(mileage_km=None), listings)
        mileage = next(a for a in valuation.adjustments if a.name == "mileage")
        assert mileage.reason is AdjustmentReason.INPUT_UNKNOWN
        assert mileage.amount_azn == 0.0
        assert "not provided" in mileage.explanation

    def test_narrow_mileage_spread_refuses_to_fit(self) -> None:
        """A slope fitted over 2,000 km cannot be extrapolated to 60,000."""
        listings, _ = synthetic_market(count=40, mileage_spread=2_000)
        _, valuation = _analyse(make_subject(mileage_km=100_000), listings)
        mileage = next(a for a in valuation.adjustments if a.name == "mileage")
        assert mileage.reason is AdjustmentReason.INSUFFICIENT_DATA
        assert mileage.amount_azn == 0.0

    def test_seasonality_is_never_guessed(self) -> None:
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(), listings)
        seasonality = next(a for a in valuation.adjustments if a.name == "seasonality")
        assert seasonality.reason is AdjustmentReason.INSUFFICIENT_HISTORY
        assert seasonality.amount_azn == 0.0

    def test_unavailable_adjustments_are_enumerable(self) -> None:
        """The report needs to list what could not be measured."""
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(mileage_km=None), listings)
        names = {a.name for a in valuation.unavailable_adjustments()}
        assert {"seasonality", "market_demand", "mileage"} <= names

    def test_every_adjustment_carries_an_explanation(self) -> None:
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(), listings)
        for adjustment in valuation.adjustments:
            assert adjustment.explanation, f"{adjustment.name} has no explanation"


class TestRangeConstruction:
    def test_thin_samples_produce_wider_ranges(self) -> None:
        """Uncertainty must be visible as width, not hidden behind a number."""
        wide_market, _ = synthetic_market(count=120, seed=1)
        thin_market, _ = synthetic_market(count=8, seed=1)

        _, wide = _analyse(make_subject(), wide_market)
        _, thin = _analyse(make_subject(), thin_market)

        assert thin.range_width_pct > wide.range_width_pct

    def test_range_brackets_the_central_estimate(self) -> None:
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(), listings)
        assert (
            valuation.fair_market_low.as_float()
            < valuation.central_estimate.as_float()
            < valuation.fair_market_high.as_float()
        )

    def test_range_has_a_minimum_width(self) -> None:
        """No false precision, even when comparables agree perfectly."""
        identical = [
            make_listing(f"L{i}", price=40_000, mileage_km=100_000 + i * 5_000)
            for i in range(30)
        ]
        _, valuation = _analyse(make_subject(mileage_km=100_000), identical)
        assert valuation.range_width_pct >= 4.0

    def test_outliers_do_not_move_the_estimate(self) -> None:
        listings, _ = synthetic_market(count=60)
        clean_subject = make_subject(mileage_km=100_000)
        _, clean = _analyse(clean_subject, listings)

        polluted = [*listings, make_listing("BAD", price=4_000_000, mileage_km=100_000)]
        _, dirty = _analyse(clean_subject, polluted)

        assert dirty.outliers_removed >= 1
        assert dirty.central_estimate.as_float() == pytest.approx(
            clean.central_estimate.as_float(), rel=0.03
        )


class TestPriceBasis:
    def test_defaults_to_asking_basis(self) -> None:
        """Spec §9: listing price is not transaction price, and the type says so."""
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(), listings)
        assert valuation.basis is PriceBasis.ASKING

    def test_asking_basis_is_disclosed_in_the_notes(self) -> None:
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(), listings)
        assert any("asking prices" in note for note in valuation.notes)


class TestExplainability:
    """Spec §47, §69: every number decomposes into named contributions."""

    def test_adjustments_move_the_estimate_off_the_raw_median(self) -> None:
        listings, _ = synthetic_market(count=80, mileage_spread=120_000)
        _, valuation = _analyse(make_subject(mileage_km=180_000), listings)
        assert valuation.raw_market_median.as_float() > valuation.central_estimate.as_float()

    def test_ablation_attribution_has_the_right_sign(self) -> None:
        listings, _ = synthetic_market(count=80, mileage_spread=120_000)
        _, high = _analyse(make_subject(mileage_km=180_000), listings)
        _, low = _analyse(make_subject(mileage_km=40_000), listings)

        high_mileage_adj = next(a for a in high.adjustments if a.name == "mileage")
        low_mileage_adj = next(a for a in low.adjustments if a.name == "mileage")

        assert high_mileage_adj.amount_azn < 0
        assert low_mileage_adj.amount_azn > 0

    def test_fitted_slopes_report_their_sample_size(self) -> None:
        listings, _ = synthetic_market(count=60)
        _, valuation = _analyse(make_subject(), listings)
        mileage = next(a for a in valuation.adjustments if a.name == "mileage")
        assert mileage.data_points > 0
        assert 0.0 < mileage.confidence <= 0.95
