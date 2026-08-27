"""Market source adapter interface (spec §6).

Every market source — a public marketplace, a dealer feed, a partner API, our
own confirmed transactions — implements this one interface and produces the same
:class:`RawListing` shape. Engines and services depend on the interface; only
the composition root ever names an implementation.

The type doing the real work here is :class:`ParseResult`. Adapters report
extraction success **per field**, not just success or failure overall, because
the way a scraper actually breaks is silent: the site changes its markup, every
request still returns 200, rows still get written, and mileage is quietly NULL
on 90% of them. Alerting on error counts misses that entirely. Alerting on
per-field extraction rates catches it the same day (audit §3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.enums import ListingStatus, SellerType
from app.domain.identity import VehicleConfiguration
from app.domain.money import Money


@dataclass(frozen=True, slots=True)
class RawListing:
    """A normalized listing produced by an adapter.

    Prices arrive in whatever currency the source quotes. Conversion to AZN
    happens in the ingestion pipeline using the FX rates in force for that
    batch, so every listing in a batch is on one scale.
    """

    external_id: str
    source_url: str
    configuration: VehicleConfiguration
    price: Money
    observed_at: datetime
    mileage_km: int | None = None
    city: str | None = None
    seller_type: SellerType = SellerType.UNKNOWN
    status: ListingStatus = ListingStatus.ACTIVE
    description: str | None = None
    has_damage_disclosure: bool | None = None
    has_repaint_disclosure: bool | None = None
    owner_count: int | None = None
    posted_at: datetime | None = None
    """The source's own publication date, when it states one. More accurate
    than our first-seen date for days-on-market, which otherwise starts from
    whenever we first crawled the listing."""

    image_count: int = 0
    raw_fields: dict[str, str] = field(default_factory=dict)
    """Source fields exactly as extracted, before normalization. Kept so a
    parser fix can be replayed against stored rows without re-crawling."""


@dataclass
class ParseResult:
    """One parse attempt, with per-field success recorded.

    ``listing`` is ``None`` when the record was unusable. ``missing_fields``
    is populated even on success — a listing that parsed but lost its mileage
    is a partial failure, and the aggregate of those is the health signal.
    """

    listing: RawListing | None
    missing_fields: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    unmapped_values: dict[str, str] = field(default_factory=dict)
    """Vocabulary the normalization tables did not recognize, by field. Feeds
    the unmapped-token metric so table gaps become visible and fixable."""

    @property
    def ok(self) -> bool:
        return self.listing is not None


@dataclass
class ExtractionHealth:
    """Aggregate per-field extraction rates across a run.

    This is the alarm that actually catches a markup change.
    """

    total: int = 0
    present: dict[str, int] = field(default_factory=dict)
    failures: int = 0

    #: Fields whose extraction rate is worth alerting on. A drop here means
    #: the parser has lost touch with the page, not that listings changed.
    TRACKED_FIELDS = (
        "make",
        "model",
        "model_year",
        "price",
        "mileage_km",
        "city",
        "fuel",
        "transmission",
    )

    def record(self, result: ParseResult) -> None:
        self.total += 1
        if not result.ok:
            self.failures += 1
            return
        missing = set(result.missing_fields)
        for name in self.TRACKED_FIELDS:
            if name not in missing:
                self.present[name] = self.present.get(name, 0) + 1

    def rates(self) -> dict[str, float]:
        if self.total == 0:
            return {}
        return {
            name: round(self.present.get(name, 0) / self.total, 4)
            for name in self.TRACKED_FIELDS
        }

    def degraded_fields(self, threshold: float = 0.6) -> list[str]:
        """Fields extracted from fewer than ``threshold`` of records.

        A non-empty list on a source that previously ran clean is the signal
        that the parser needs attention.
        """
        return [name for name, rate in self.rates().items() if rate < threshold]

    @property
    def failure_rate(self) -> float:
        return self.failures / self.total if self.total else 0.0


@runtime_checkable
class MarketSourceAdapter(Protocol):
    """Contract every market source implements."""

    slug: str
    display_name: str

    async def discover(self, since: datetime | None = None) -> AsyncIterator[str]:
        """Yield detail-page URLs (or identifiers) worth fetching.

        Implementations should be incremental: with ``since`` supplied, yield
        only what has plausibly changed. Full enumeration is for the initial
        backfill only.
        """
        ...

    async def fetch(self, identifier: str) -> ParseResult:
        """Fetch and parse one listing."""
        ...

    async def close(self) -> None:
        ...


class AdapterRegistry:
    """Resolves adapters by slug.

    Sources are looked up by name rather than imported, which is what keeps
    "which sources are enabled" a configuration question and lets a disabled
    or broken adapter fail without taking the application with it.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, MarketSourceAdapter] = {}

    def register(self, adapter: MarketSourceAdapter) -> None:
        self._adapters[adapter.slug] = adapter

    def get(self, slug: str) -> MarketSourceAdapter:
        adapter = self._adapters.get(slug)
        if adapter is None:
            raise KeyError(
                f"no market adapter registered for {slug!r}; "
                f"registered: {sorted(self._adapters)}"
            )
        return adapter

    def get_or_none(self, slug: str) -> MarketSourceAdapter | None:
        """Lookup for callers that treat an unknown source as a 404."""
        return self._adapters.get(slug)

    def enabled(self) -> Sequence[MarketSourceAdapter]:
        return tuple(self._adapters.values())

    def __contains__(self, slug: object) -> bool:
        return slug in self._adapters
