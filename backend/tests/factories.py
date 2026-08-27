"""Deterministic fixtures for engine tests.

These build *synthetic* markets with a known ground truth — a chosen base price,
a chosen mileage slope, a chosen year slope, and controlled noise — so tests can
assert that the valuation engine recovers parameters it was never told.

That is the point of this module. Testing a valuation engine against real
scraped prices only tells you the engine is self-consistent; testing it against
a market whose true generating process you control tells you whether it is
*correct*. Nothing here is presented anywhere in the product as market data.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.domain.enums import (
    BodyStyle,
    Drivetrain,
    FuelType,
    ListingStatus,
    SellerType,
    Transmission,
)
from app.domain.identity import VehicleConfiguration
from app.domain.market import MarketListing, PricePoint, SubjectVehicle
from app.domain.money import Money

REFERENCE_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def make_config(
    *,
    make: str = "BMW",
    model: str = "5 SERIES",
    year: int = 2019,
    trim: str | None = "530I",
    displacement: float = 2.0,
    fuel: FuelType = FuelType.PETROL,
    transmission: Transmission = Transmission.AUTOMATIC,
    drivetrain: Drivetrain = Drivetrain.RWD,
    body: BodyStyle = BodyStyle.SEDAN,
    generation: str | None = "G30",
) -> VehicleConfiguration:
    return VehicleConfiguration(
        make=make,
        model=model,
        model_year=year,
        generation=generation,
        trim=trim,
        displacement_l=displacement,
        fuel=fuel,
        transmission=transmission,
        drivetrain=drivetrain,
        body=body,
    )


def make_listing(
    listing_id: str,
    *,
    price: float,
    mileage_km: int | None = 100_000,
    config: VehicleConfiguration | None = None,
    city: str = "Bakı",
    seller_type: SellerType = SellerType.PRIVATE,
    status: ListingStatus = ListingStatus.ACTIVE,
    days_ago_first_seen: int = 20,
    days_ago_last_seen: int = 0,
    has_damage_disclosure: bool | None = None,
    price_history: tuple[PricePoint, ...] = (),
    source_url: str | None = None,
    now: datetime = REFERENCE_NOW,
) -> MarketListing:
    return MarketListing(
        listing_id=listing_id,
        source="test",
        configuration=config or make_config(),
        price=Money.azn(round(price, 2)),
        first_seen_at=now - timedelta(days=days_ago_first_seen),
        last_seen_at=now - timedelta(days=days_ago_last_seen),
        source_url=source_url,
        mileage_km=mileage_km,
        city=city,
        seller_type=seller_type,
        status=status,
        price_history=price_history,
        has_damage_disclosure=has_damage_disclosure,
    )


def make_subject(
    *,
    asking_price: float | None = 43_000,
    mileage_km: int | None = 100_000,
    config: VehicleConfiguration | None = None,
    city: str = "Bakı",
    **kwargs: object,
) -> SubjectVehicle:
    return SubjectVehicle(
        configuration=config or make_config(),
        asking_price=Money.azn(asking_price) if asking_price is not None else None,
        mileage_km=mileage_km,
        city=city,
        **kwargs,  # type: ignore[arg-type]
    )


def synthetic_market(
    *,
    count: int = 60,
    base_price: float = 45_000.0,
    base_mileage: int = 100_000,
    base_year: int = 2019,
    mileage_slope: float = -0.09,  # AZN lost per km; -0.09 == 90 AZN / 1,000 km
    year_slope: float = 2_200.0,  # AZN gained per newer model year
    noise_pct: float = 0.05,
    mileage_spread: int = 70_000,
    year_spread: int = 2,
    city: str = "Bakı",
    seed: int = 20260827,
    now: datetime = REFERENCE_NOW,
) -> tuple[list[MarketListing], dict[str, float]]:
    """Generate a market obeying a known linear price model.

    Returns the listings plus the ground-truth parameters, so a test can assert
    the engine recovered them.

    The generating process is::

        price = base
              + mileage_slope * (mileage - base_mileage)
              + year_slope    * (year - base_year)
              + multiplicative noise

    Noise is multiplicative because real price dispersion scales with price
    level: a 5% spread on a 12,000 AZN car is 600 AZN, on a 90,000 AZN car it
    is 4,500 AZN.
    """
    rng = random.Random(seed)
    listings: list[MarketListing] = []

    for i in range(count):
        mileage = base_mileage + rng.randint(-mileage_spread // 2, mileage_spread // 2)
        mileage = max(1_000, mileage)
        year = base_year + rng.randint(-year_spread, year_spread)

        true_price = (
            base_price
            + mileage_slope * (mileage - base_mileage)
            + year_slope * (year - base_year)
        )
        price = true_price * (1.0 + rng.gauss(0.0, noise_pct))
        price = max(1_000.0, price)

        first_seen = rng.randint(5, 90)
        # last_seen can never precede first_seen; the domain enforces this.
        last_seen = rng.randint(0, min(14, first_seen))
        listings.append(
            make_listing(
                f"L{i:04d}",
                price=price,
                mileage_km=mileage,
                config=make_config(year=year),
                city=city,
                days_ago_first_seen=first_seen,
                days_ago_last_seen=last_seen,
                now=now,
            )
        )

    truth = {
        "base_price": base_price,
        "base_mileage": float(base_mileage),
        "base_year": float(base_year),
        "mileage_slope": mileage_slope,
        "year_slope": year_slope,
    }
    return listings, truth


def expected_price(truth: dict[str, float], mileage_km: int, year: int) -> float:
    """Ground-truth price for a vehicle under a synthetic market's model."""
    return (
        truth["base_price"]
        + truth["mileage_slope"] * (mileage_km - truth["base_mileage"])
        + truth["year_slope"] * (year - truth["base_year"])
    )
