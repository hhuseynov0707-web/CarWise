"""PostgreSQL implementation of the market data ports.

Maps ORM rows to domain objects. The engines never see a row, a column name, or
a SQLAlchemy type — which is what allows the whole analytical core to be tested
against a plain list (see ``InMemoryMarketRepository``) and what makes the
storage layer replaceable per spec §72.

One query decision worth flagging: :meth:`candidate_listings` filters on
``model_key``, the *loosest* rung of the identity ladder, not on the exact
configuration. Narrowing here would prevent the comparable engine from widening,
and widening is its job, not the database's. The database's job is to return a
generous candidate pool cheaply, which the indexes in ``models.py`` make
possible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, ListingObservation, TransactionObservationRow
from app.domain.enums import (
    BodyStyle,
    ConditionGrade,
    Currency,
    Drivetrain,
    FuelType,
    ImportStatus,
    ListingStatus,
    SellerType,
    SourceType,
    Transmission,
)
from app.domain.identity import VehicleConfiguration
from app.domain.market import (
    MarketListing,
    MileagePoint,
    PricePoint,
    TransactionObservation,
)
from app.domain.money import Money

#: Hard ceiling on rows returned to one analysis. A pathological query (a very
#: common model over a long window) must not be able to pull the whole table
#: into memory.
MAX_CANDIDATES = 5000


class SqlMarketRepository:
    """Market data backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def candidate_listings(
        self,
        configuration: VehicleConfiguration,
        as_of: datetime,
        window_days: int,
        limit: int = MAX_CANDIDATES,
    ) -> Sequence[MarketListing]:
        """Listings of the same make and model within the observation window.

        Removed listings are deliberately included. Excluding them would bias
        the sample toward cars that failed to sell, pushing every median upward
        (audit §7.5).
        """
        if not configuration.is_resolvable:
            return []

        cutoff = as_of - timedelta(days=window_days)
        statement = (
            select(Listing)
            .where(Listing.model_key == configuration.model_key)
            .where(Listing.last_seen_at >= cutoff)
            .where(Listing.price_azn > 0)
            .order_by(Listing.last_seen_at.desc())
            .limit(min(limit, MAX_CANDIDATES))
        )
        rows = (await self._session.scalars(statement)).unique().all()
        return [to_domain_listing(row) for row in rows]

    async def transaction_observations(
        self,
        configuration: VehicleConfiguration,
        as_of: datetime,
        window_days: int,
    ) -> Sequence[TransactionObservation]:
        """Reported settled sales for this configuration.

        A wider window than listings: transaction records are scarce enough that
        discarding an eight-month-old one costs more than the staleness does.
        """
        if not configuration.is_resolvable:
            return []

        cutoff = as_of - timedelta(days=max(window_days, 365))
        statement = (
            select(TransactionObservationRow)
            .where(TransactionObservationRow.model_key == configuration.model_key)
            .where(TransactionObservationRow.transaction_date >= cutoff)
            .order_by(TransactionObservationRow.transaction_date.desc())
            .limit(500)
        )
        rows = (await self._session.scalars(statement)).all()
        return [to_domain_transaction(row) for row in rows]

    async def listing_by_url(self, url: str) -> MarketListing | None:
        statement = select(Listing).where(Listing.source_url == url).limit(1)
        row = (await self._session.scalars(statement)).first()
        return to_domain_listing(row) if row else None

    async def listing_by_external_id(
        self, source_id: int, external_id: str
    ) -> MarketListing | None:
        statement = (
            select(Listing)
            .where(Listing.source_id == source_id)
            .where(Listing.external_id == external_id)
            .limit(1)
        )
        row = (await self._session.scalars(statement)).first()
        return to_domain_listing(row) if row else None


# --- Mapping ---------------------------------------------------------------


def _enum(value: str | None, enum_cls, default):  # type: ignore[no-untyped-def]
    """Coerce a stored string to an enum, defaulting rather than raising.

    A value that was valid when written but has since been renamed must not
    crash an analysis. Falling back to the default surfaces as reduced
    specificity, which the confidence engine already accounts for.
    """
    if not value:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


def to_domain_configuration(row: Listing) -> VehicleConfiguration:
    """Rebuild a configuration from the row the listing was resolved to.

    Reads the linked configuration rather than ``raw_payload``. The payload
    holds what the source page said — ``"2026"``, ``"0 km"``,
    ``"Offroader / SUV, 5 qapı"`` — and is kept so a parser fix can be replayed
    against historical rows. Those are source strings, not values this layer
    can hand to the domain: a model year arrives as text, and the range check
    in ``VehicleConfiguration.__post_init__`` raises a TypeError on it. Empty
    databases hid that; the first analysis with real comparables did not.

    The configuration table already holds these facts parsed and normalised,
    which is what resolving one per listing is for.
    """
    config = row.configuration
    if config is None:
        # config_id is nullable, so this is reachable in principle. The
        # identity ladder on the listing is all we can honestly assert here.
        make, _, model = (row.model_key or "").partition("|")
        return VehicleConfiguration(make=make or None, model=model or None)

    return VehicleConfiguration(
        make=config.make,
        model=config.model,
        model_year=config.model_year,
        generation=config.generation,
        trim=config.trim,
        engine_code=config.engine_code,
        displacement_l=(
            float(config.displacement_l) if config.displacement_l is not None else None
        ),
        fuel=_enum(config.fuel, FuelType, FuelType.UNKNOWN),
        transmission=_enum(config.transmission, Transmission, Transmission.UNKNOWN),
        drivetrain=_enum(config.drivetrain, Drivetrain, Drivetrain.UNKNOWN),
        body=_enum(config.body, BodyStyle, BodyStyle.UNKNOWN),
        horsepower=config.horsepower,
        import_status=_enum(config.import_status, ImportStatus, ImportStatus.UNKNOWN),
    )


def to_domain_listing(row: Listing) -> MarketListing:
    price_history = tuple(
        PricePoint(observed_at=_aware(o.observed_at), price=Money.azn(o.price_azn))
        for o in sorted(row.history, key=lambda o: o.observed_at)
    )
    mileage_history = tuple(
        MileagePoint(observed_at=_aware(o.observed_at), mileage_km=o.mileage_km)
        for o in sorted(row.history, key=lambda o: o.observed_at)
        if o.mileage_km is not None
    )

    return MarketListing(
        listing_id=str(row.id),
        source=str(row.source_id),
        configuration=to_domain_configuration(row),
        price=Money.azn(row.price_azn),
        first_seen_at=_aware(row.first_seen_at),
        last_seen_at=_aware(row.last_seen_at),
        source_url=row.source_url,
        mileage_km=row.mileage_km,
        city=row.city,
        seller_type=_enum(row.seller_type, SellerType, SellerType.UNKNOWN),
        status=_enum(row.status, ListingStatus, ListingStatus.UNKNOWN),
        condition=_enum(row.condition, ConditionGrade, ConditionGrade.UNKNOWN),
        price_history=price_history,
        mileage_history=mileage_history,
        has_damage_disclosure=row.has_damage_disclosure,
        has_repaint_disclosure=row.has_repaint_disclosure,
        description=row.description,
    )


def to_domain_transaction(row: TransactionObservationRow) -> TransactionObservation:
    return TransactionObservation(
        observation_id=str(row.id),
        configuration=VehicleConfiguration(),
        price=Money.azn(row.price_azn),
        transaction_date=_aware(row.transaction_date),
        source=row.source,
        source_type=_enum(row.source_type, SourceType, SourceType.USER),
        confidence=row.confidence,
        mileage_km=row.mileage_km,
        condition=_enum(row.condition, ConditionGrade, ConditionGrade.UNKNOWN),
        city=row.city,
        listing_id=str(row.listing_id) if row.listing_id else None,
        listed_price=Money.azn(row.listed_price_azn) if row.listed_price_azn else None,
    )


def _aware(value: datetime) -> datetime:
    """Guarantee a timezone-aware datetime.

    SQLite (used in tests) discards timezone information that PostgreSQL keeps.
    Mixing naive and aware datetimes raises deep inside the engines, so the
    boundary normalizes rather than leaving it to chance.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def listing_to_row_values(listing: MarketListing, source_id: int) -> dict[str, object]:
    """Column values for persisting a normalized listing.

    Used by the ingestion pipeline. Kept next to the read mapping so the two
    directions stay in step.
    """
    config = listing.configuration
    return {
        "source_id": source_id,
        "external_id": listing.listing_id,
        "source_url": listing.source_url,
        "config_id": config.config_id if config.is_resolvable else None,
        "model_key": config.model_key if config.is_resolvable else None,
        "generation_key": config.generation_key if config.is_resolvable else None,
        "powertrain_key": config.powertrain_key if config.is_resolvable else None,
        "price_amount": Decimal(str(listing.price.as_float())),
        "price_currency": Currency.AZN.value,
        "price_azn": Decimal(str(listing.price.as_float())),
        "mileage_km": listing.mileage_km,
        "city": listing.city,
        "region": listing.region,
        "seller_type": listing.seller_type.value,
        "condition": listing.condition.value,
        "has_damage_disclosure": listing.has_damage_disclosure,
        "has_repaint_disclosure": listing.has_repaint_disclosure,
        "description": listing.description,
        "status": listing.status.value,
        "first_seen_at": listing.first_seen_at,
        "last_seen_at": listing.last_seen_at,
        "raw_payload": {
            "make": config.make,
            "model": config.model,
            "model_year": config.model_year,
            "generation": config.generation,
            "trim": config.trim,
            "engine_code": config.engine_code,
            "displacement_l": config.displacement_l,
            "fuel": config.fuel.value,
            "transmission": config.transmission.value,
            "drivetrain": config.drivetrain.value,
            "body": config.body.value,
            "horsepower": config.horsepower,
            "import_status": config.import_status.value,
        },
    }


__all__ = [
    "SqlMarketRepository",
    "listing_to_row_values",
    "to_domain_configuration",
    "to_domain_listing",
    "to_domain_transaction",
    "ListingObservation",
]
