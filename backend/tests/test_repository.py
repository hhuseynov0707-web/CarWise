"""Rebuilding domain objects from database rows.

The listing row carries the source's own strings in ``raw_payload`` and a
foreign key to a parsed, normalised configuration. Reading the first where the
second is meant produced a 500 on every analysis that found comparables — and
only once real listings existed, because an empty market never reached the
conversion at all.
"""

from __future__ import annotations

from decimal import Decimal

from app.db.models import Listing, VehicleConfigurationRow
from app.db.repository import to_domain_configuration
from app.domain.enums import BodyStyle, FuelType


def _config_row(**overrides: object) -> VehicleConfigurationRow:
    fields: dict = {
        "config_id": "cfg_test",
        "model_key": "BMW|530",
        "generation_key": "BMW|530|G30",
        "powertrain_key": "BMW|530|G30|2.0|PETROL",
        "canonical_string": "2019 BMW 530",
        "make": "BMW",
        "model": "530",
        "model_year": 2019,
        "displacement_l": Decimal("2.0"),
        "fuel": "PETROL",
        "transmission": "AUTOMATIC",
        "drivetrain": "RWD",
        "body": "SEDAN",
        "import_status": "UNKNOWN",
    }
    fields.update(overrides)
    return VehicleConfigurationRow(**fields)


def _listing(
    configuration: VehicleConfigurationRow | None, model_key: str = "BMW|530"
) -> Listing:
    listing = Listing(
        model_key=model_key,
        # What the source page said, kept verbatim so a parser fix can be
        # replayed later. Every value here is a string, including the year.
        raw_payload={
            "make": "Bmw",
            "model": "530",
            "model_year": "2019",
            "mileage_km": "120 000 km",
            "body": "Sedan, 4 qapı",
            "fuel": "Benzin",
        },
    )
    listing.configuration = configuration
    return listing


class TestConfigurationRebuild:
    def test_reads_the_resolved_configuration_not_the_source_strings(self) -> None:
        config = to_domain_configuration(_listing(_config_row()))

        assert config.model_year == 2019, "must be the integer, not the source's '2019'"
        assert isinstance(config.model_year, int)
        assert config.make == "BMW"
        assert config.fuel is FuelType.PETROL
        assert config.body is BodyStyle.SEDAN
        assert config.displacement_l == 2.0
        assert isinstance(config.displacement_l, float)

    def test_a_source_year_string_does_not_reach_the_domain(self) -> None:
        """The regression itself.

        VehicleConfiguration range-checks the model year, so a string reaching
        it raises TypeError rather than returning a wrong answer. Building the
        configuration at all is the assertion.
        """
        listing = _listing(_config_row())
        listing.raw_payload["model_year"] = "not a year"

        assert to_domain_configuration(listing).model_year == 2019

    def test_an_unresolved_listing_falls_back_to_the_identity_ladder(self) -> None:
        """config_id is nullable, and inventing the rest would be worse."""
        config = to_domain_configuration(_listing(None, model_key="BMW|530"))

        assert config.make == "BMW"
        assert config.model == "530"
        assert config.model_year is None
        assert config.fuel is FuelType.UNKNOWN
