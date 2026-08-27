"""Market observations: listings, price history, and contributed transactions.

These are the types the analytical engines consume. Adapters are responsible for
producing them; engines never see a raw scraped payload, an ORM row, or a
source-specific field name.

The most important distinction encoded here is between a
:class:`MarketListing` — what somebody is *asking* — and a
:class:`TransactionObservation` — what somebody actually *paid*. Spec §9 and
audit §2. They are separate types precisely so that no code path can
accidentally average them together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import (
    ConditionGrade,
    Currency,
    ListingStatus,
    PriceBasis,
    SellerType,
    SourceType,
)
from app.domain.identity import VehicleConfiguration
from app.domain.money import Money
from app.domain.normalization import market_region
from app.domain.provenance import ProvenanceLedger


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One observed price for a listing at one moment."""

    observed_at: datetime
    price: Money

    def __post_init__(self) -> None:
        if self.price.currency is not Currency.AZN:
            raise ValueError(
                "price history must be normalized to AZN before reaching the domain layer"
            )


@dataclass(frozen=True, slots=True)
class MileagePoint:
    """One observed odometer reading for a listing at one moment."""

    observed_at: datetime
    mileage_km: int


@dataclass(frozen=True, slots=True)
class MarketListing:
    """A normalized vehicle listing observed on some market source.

    Prices are AZN-normalized at ingestion using the FX rates in force for that
    batch, so every listing in a comparable set is on one scale.
    """

    listing_id: str
    source: str
    configuration: VehicleConfiguration
    price: Money
    first_seen_at: datetime
    last_seen_at: datetime
    source_url: str | None = None
    mileage_km: int | None = None
    city: str | None = None
    seller_type: SellerType = SellerType.UNKNOWN
    status: ListingStatus = ListingStatus.ACTIVE
    condition: ConditionGrade = ConditionGrade.UNKNOWN
    price_history: tuple[PricePoint, ...] = ()
    mileage_history: tuple[MileagePoint, ...] = ()
    has_damage_disclosure: bool | None = None
    """``None`` means *not stated*, which is different from *stated as none*."""
    has_repaint_disclosure: bool | None = None
    description: str | None = None
    """Retained for analysis only; not republished (audit §4.7)."""

    def __post_init__(self) -> None:
        if self.price.currency is not Currency.AZN:
            raise ValueError(
                f"listing {self.listing_id} price is {self.price.currency.value}; "
                "ingestion must normalize to AZN before constructing a MarketListing"
            )
        if self.mileage_km is not None and self.mileage_km < 0:
            raise ValueError(f"listing {self.listing_id} has negative mileage")
        if self.last_seen_at < self.first_seen_at:
            raise ValueError(f"listing {self.listing_id} last_seen precedes first_seen")

    # --- listing behaviour (spec §8) ---------------------------------------

    @property
    def config_id(self) -> str:
        return self.configuration.config_id

    @property
    def region(self) -> str:
        return market_region(self.city)

    def days_on_market(self, as_of: datetime) -> int:
        """Days between first sighting and either removal or ``as_of``."""
        end = self.last_seen_at if self.status is not ListingStatus.ACTIVE else as_of
        return max(0, (end - self.first_seen_at).days)

    def age_days(self, as_of: datetime) -> int:
        """How stale this observation is, for freshness weighting."""
        return max(0, (as_of - self.last_seen_at).days)

    @property
    def original_price(self) -> Money:
        """First observed price, or the current one if there is no history."""
        return self.price_history[0].price if self.price_history else self.price

    @property
    def price_change_count(self) -> int:
        """Number of times the asking price actually moved."""
        if len(self.price_history) < 2:
            return 0
        changes = 0
        previous = self.price_history[0].price
        for point in self.price_history[1:]:
            if point.price.amount != previous.amount:
                changes += 1
                previous = point.price
        return changes

    @property
    def total_price_change_pct(self) -> float | None:
        """Signed percentage move from first to current price."""
        original = self.original_price
        if original.amount == 0:
            return None
        return self.price.pct_difference_from(original)

    @property
    def has_price_history(self) -> bool:
        return len(self.price_history) >= 2

    @property
    def is_active(self) -> bool:
        return self.status is ListingStatus.ACTIVE

    def price_azn(self) -> float:
        return self.price.as_float()


@dataclass(frozen=True, slots=True)
class TransactionObservation:
    """A reported *settled* price (spec §9).

    Deliberately a distinct type from :class:`MarketListing`. These are scarce
    and high-value: once enough exist for a configuration, valuation for that
    configuration can shift from an asking basis to a transaction basis.

    ``confidence`` reflects how much the reporter is trusted — a dealer feed
    under contract is not an anonymous web form.
    """

    observation_id: str
    configuration: VehicleConfiguration
    price: Money
    transaction_date: datetime
    source: str
    source_type: SourceType
    confidence: float
    mileage_km: int | None = None
    condition: ConditionGrade = ConditionGrade.UNKNOWN
    city: str | None = None
    listing_id: str | None = None
    """Set when we can tie a sale back to the listing it came from."""
    listed_price: Money | None = None
    """Asking price of the same vehicle, when known."""

    def __post_init__(self) -> None:
        if self.price.currency is not Currency.AZN:
            raise ValueError("transaction price must be AZN-normalized")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    @property
    def config_id(self) -> str:
        return self.configuration.config_id

    @property
    def region(self) -> str:
        return market_region(self.city)

    @property
    def discount_from_asking_pct(self) -> float | None:
        """How far below the asking price this vehicle actually settled.

        Accumulating these is what eventually lets the platform measure the
        asking-to-transaction gap per segment instead of assuming a constant —
        the single most valuable thing the proprietary dataset can learn.
        """
        if self.listed_price is None or self.listed_price.amount == 0:
            return None
        return self.price.pct_difference_from(self.listed_price)


@dataclass
class SubjectVehicle:
    """The vehicle the user is actually asking about.

    Distinct from :class:`MarketListing` because the subject is not
    necessarily a listing at all — it may be a VIN, a manual entry, or a car
    the user already owns and wants to price (spec §42, seller mode).

    Carries its own :class:`ProvenanceLedger` so the report can state, per
    attribute, whether a value was decoded, scraped, typed by the user, or
    inferred by a model.
    """

    configuration: VehicleConfiguration
    asking_price: Money | None = None
    mileage_km: int | None = None
    city: str | None = None
    seller_type: SellerType = SellerType.UNKNOWN
    condition: ConditionGrade = ConditionGrade.UNKNOWN
    vin: str | None = None
    listing_url: str | None = None
    listing_first_seen_at: datetime | None = None
    price_history: tuple[PricePoint, ...] = ()
    has_damage_disclosure: bool | None = None
    has_repaint_disclosure: bool | None = None
    service_records_provided: bool = False
    owner_count: int | None = None
    description: str | None = None
    ledger: ProvenanceLedger = field(default_factory=ProvenanceLedger)

    def __post_init__(self) -> None:
        if self.asking_price is not None and self.asking_price.currency is not Currency.AZN:
            raise ValueError("subject asking price must be AZN-normalized")

    @property
    def region(self) -> str:
        return market_region(self.city)

    @property
    def config_id(self) -> str:
        return self.configuration.config_id

    def days_listed(self, as_of: datetime) -> int | None:
        if self.listing_first_seen_at is None:
            return None
        return max(0, (as_of - self.listing_first_seen_at).days)

    @property
    def price_change_count(self) -> int:
        if len(self.price_history) < 2:
            return 0
        changes = 0
        previous = self.price_history[0].price
        for point in self.price_history[1:]:
            if point.price.amount != previous.amount:
                changes += 1
                previous = point.price
        return changes

    @property
    def total_price_change_pct(self) -> float | None:
        if not self.price_history or self.asking_price is None:
            return None
        original = self.price_history[0].price
        if original.amount == 0:
            return None
        return self.asking_price.pct_difference_from(original)


@dataclass(frozen=True, slots=True)
class MarketSample:
    """The evidence set a valuation is computed from.

    Bundles the observations together with *how* they were selected, so the
    report can explain the basis of its own numbers rather than presenting a
    figure with no visible derivation.
    """

    listings: tuple[MarketListing, ...]
    transactions: tuple[TransactionObservation, ...] = ()
    basis: PriceBasis = PriceBasis.ASKING
    window_days: int | None = None
    """Observation window used, in days. Statistics are computed over listings
    *seen within* this window rather than only currently-active ones, to fight
    the survivorship bias described in audit §7.5."""

    @property
    def size(self) -> int:
        return len(self.listings) + len(self.transactions)

    @property
    def is_empty(self) -> bool:
        return self.size == 0
