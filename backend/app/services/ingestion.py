"""Market ingestion pipeline (spec §7, §8).

    discover -> fetch -> parse -> normalize -> resolve identity -> deduplicate
    -> store -> detect changes -> record health

The part worth reading carefully is change detection. Storing the *current*
state of a listing is easy and nearly worthless; storing the transitions is what
produces days-on-market, price-reduction behaviour and eventually the
asking-to-settled gap — the proprietary dataset spec §63 identifies as the
actual defensible asset. So an observation row is written on every meaningful
change and never on an unchanged re-crawl, which keeps the table small enough
to aggregate over.

Three refusals are built in, all from audit §4:

* ingestion does not run when disabled in configuration;
* ingestion does not run while the adapter's extraction rules are unverified,
  because an untested parser writes nulls rather than errors;
* ingestion aborts when per-field extraction rates collapse mid-run, because
  that is what a markup change looks like from the inside.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.market.base import (
    ExtractionHealth,
    MarketSourceAdapter,
    ParseResult,
    RawListing,
)
from app.adapters.market.http import FetchFailed
from app.db.models import (
    DataQualityObservation,
    IngestionRun,
    Listing,
    ListingObservation,
    MarketSource,
    VehicleConfigurationRow,
)
from app.domain.enums import Currency, ListingStatus
from app.domain.money import FxTable, Money
from app.domain.normalization import market_region

#: A run aborts if fewer than this share of records yield a usable listing.
#: Set generously: individual bad pages are normal, a collapse is not.
MIN_SUCCESS_RATE = 0.5

#: Records to process before the first health check. Below this the sample is
#: too small to distinguish a broken parser from a run of unusual pages.
HEALTH_CHECK_AFTER = 25

#: A listing not seen for this long is presumed removed from the source.
PRESUMED_REMOVED_DAYS = 3


class IngestionRefused(RuntimeError):
    """The pipeline declined to run. The message says why."""


@dataclass
class IngestionReport:
    """What one run did."""

    source: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "RUNNING"
    listings_seen: int = 0
    listings_created: int = 0
    listings_updated: int = 0
    listings_unchanged: int = 0
    listings_removed: int = 0
    price_changes: int = 0
    errors: int = 0
    quality_issues: int = 0
    health: ExtractionHealth = field(default_factory=ExtractionHealth)
    unmapped: dict[str, set[str]] = field(default_factory=dict)
    abort_reason: str | None = None

    def record_unmapped(self, values: dict[str, str]) -> None:
        for field_name, value in values.items():
            self.unmapped.setdefault(field_name, set()).add(value)

    def unmapped_summary(self) -> dict[str, list[str]]:
        return {name: sorted(values)[:50] for name, values in self.unmapped.items()}

    @property
    def degraded_fields(self) -> list[str]:
        return self.health.degraded_fields()


@dataclass
class IngestionService:
    """Runs one market source end to end."""

    session: AsyncSession
    fx: FxTable
    ingestion_enabled: bool = False
    require_verified_selectors: bool = True

    #: Commit every N listings, or 0 to leave the transaction to the caller.
    #:
    #: An incremental run is a few hundred listings and belongs in one
    #: transaction — that is the default. A backfill is tens of thousands over
    #: many hours, and holding *that* open means no progress is visible while
    #: it runs, a failure near the end discards all of it, and Postgres carries
    #: an idle-in-transaction connection for the duration. The backfill script
    #: sets this; nothing else does.
    commit_every: int = 0

    async def run(
        self,
        adapter: MarketSourceAdapter,
        as_of: datetime,
        since: datetime | None = None,
        max_listings: int = 500,
    ) -> IngestionReport:
        self._guard(adapter)

        source = await self._source_row(adapter)
        report = IngestionReport(source=adapter.slug, started_at=as_of)
        run = IngestionRun(source_id=source.id, started_at=as_of, status="RUNNING")
        self.session.add(run)
        await self.session.flush()

        seen_external_ids: set[str] = set()

        try:
            async for identifier in adapter.discover(since):
                if report.listings_seen >= max_listings:
                    break

                try:
                    result = await adapter.fetch(identifier)
                except FetchFailed as exc:
                    # One listing that would not come down is not a reason to
                    # discard a run. It counts as an error, and _maybe_abort
                    # still stops the run if failures stop being occasional —
                    # so a genuinely severed network ends it, a blip does not.
                    report.listings_seen += 1
                    report.errors += 1
                    report.health.record(ParseResult(listing=None, errors=(str(exc),)))
                    self._maybe_abort(report)
                    continue

                report.listings_seen += 1
                report.health.record(result)
                report.record_unmapped(result.unmapped_values)

                # Durable checkpoint for everything handled so far. Placed
                # before this listing's own work so it runs once per iteration
                # whichever branch below is taken.
                if self.commit_every and report.listings_seen % self.commit_every == 0:
                    await self.session.commit()

                if not result.ok or result.listing is None:
                    report.errors += 1
                    self._maybe_abort(report)
                    continue

                listing = result.listing
                seen_external_ids.add(listing.external_id)

                issues = validate_raw_listing(listing)
                if issues:
                    report.quality_issues += len(issues)
                    for issue, detail in issues:
                        self.session.add(
                            DataQualityObservation(
                                source_id=source.id,
                                ingestion_run_id=run.id,
                                issue=issue,
                                detail=detail,
                                severity="MODERATE",
                                observed_at=as_of,
                            )
                        )
                    # An implausible record is not stored: one 4,000,000 AZN
                    # typo distorts every statistic that touches it.
                    continue

                outcome = await self._upsert(source, listing, as_of)
                if outcome == "created":
                    report.listings_created += 1
                elif outcome == "price_changed":
                    report.listings_updated += 1
                    report.price_changes += 1
                elif outcome == "updated":
                    report.listings_updated += 1
                else:
                    report.listings_unchanged += 1

                self._maybe_abort(report)

            report.listings_removed = await self._mark_removed(
                source, seen_external_ids, as_of
            )
            report.status = "COMPLETED"

        except _Aborted as abort:
            report.status = "ABORTED"
            report.abort_reason = str(abort)
        finally:
            report.finished_at = as_of
            run.finished_at = as_of
            run.status = report.status
            run.listings_seen = report.listings_seen
            run.listings_created = report.listings_created
            run.listings_updated = report.listings_updated
            run.listings_removed = report.listings_removed
            run.price_changes = report.price_changes
            run.errors = report.errors
            run.field_extraction_rates = report.health.rates()
            run.unmapped_tokens = report.unmapped_summary()
            run.error_detail = report.abort_reason
            await self.session.flush()

        return report

    # --- guards ------------------------------------------------------------

    def _guard(self, adapter: MarketSourceAdapter) -> None:
        if not self.ingestion_enabled:
            raise IngestionRefused(
                "Ingestion is disabled. Enable it only after the source's terms of "
                "service have been reviewed by a person and that decision recorded "
                "(see docs/00-architecture-audit.md §4)."
            )

        verified = getattr(adapter, "selectors_verified", True)
        if self.require_verified_selectors and not verified:
            raise IngestionRefused(
                f"The extraction rules for {adapter.slug} are not marked verified. "
                f"Run `python -m app.adapters.market.verify_turbo <listing-url>`, "
                f"correct the rules against a real page, then set \"verified\": true. "
                f"An unverified parser writes nulls rather than errors, which is worse "
                f"than not running at all."
            )

    def _maybe_abort(self, report: IngestionReport) -> None:
        """Stop a run whose parser has clearly lost touch with the pages.

        Continuing would fill the market database with half-empty rows that look
        like real observations, and every statistic downstream would quietly
        degrade (audit §3).
        """
        if report.listings_seen < HEALTH_CHECK_AFTER:
            return

        success_rate = 1.0 - report.health.failure_rate
        if success_rate < MIN_SUCCESS_RATE:
            raise _Aborted(
                f"only {success_rate:.0%} of records parsed after "
                f"{report.listings_seen} pages; the extraction rules have probably "
                f"stopped matching the site"
            )

        degraded = report.health.degraded_fields()
        critical = [name for name in degraded if name in ("price", "make", "model")]
        if critical:
            raise _Aborted(
                f"extraction of {', '.join(critical)} has collapsed across "
                f"{report.listings_seen} pages; the page structure has probably changed"
            )

    # --- persistence -------------------------------------------------------

    async def _source_row(self, adapter: MarketSourceAdapter) -> MarketSource:
        row = (
            await self.session.scalars(
                select(MarketSource).where(MarketSource.slug == adapter.slug)
            )
        ).first()
        if row is None:
            row = MarketSource(
                slug=adapter.slug,
                display_name=adapter.display_name,
                adapter=adapter.__class__.__name__,
                enabled=True,
            )
            self.session.add(row)
            await self.session.flush()
        return row

    async def _upsert(
        self, source: MarketSource, raw: RawListing, as_of: datetime
    ) -> str:
        """Insert or update one listing, recording any transition.

        Returns ``created``, ``price_changed``, ``updated`` or ``unchanged``.
        """
        price_azn = self._to_azn(raw.price)
        if price_azn is None:
            # No exchange rate means we do not know this price. Excluding it is
            # correct; storing it at a guessed rate would poison the comparable
            # sets it lands in.
            return "unchanged"

        await self._ensure_configuration(raw)

        existing = (
            await self.session.scalars(
                select(Listing)
                .where(Listing.source_id == source.id)
                .where(Listing.external_id == raw.external_id)
            )
        ).first()

        fingerprint = content_fingerprint(raw, price_azn)

        if existing is None:
            listing = Listing(
                source_id=source.id,
                external_id=raw.external_id,
                source_url=raw.source_url,
                config_id=raw.configuration.config_id if raw.configuration.is_resolvable else None,
                model_key=raw.configuration.model_key if raw.configuration.is_resolvable else None,
                generation_key=(
                    raw.configuration.generation_key if raw.configuration.is_resolvable else None
                ),
                powertrain_key=(
                    raw.configuration.powertrain_key if raw.configuration.is_resolvable else None
                ),
                price_amount=raw.price.amount,
                price_currency=raw.price.currency.value,
                price_azn=price_azn.amount,
                mileage_km=raw.mileage_km,
                city=raw.city,
                region=market_region(raw.city),
                seller_type=raw.seller_type.value,
                has_damage_disclosure=raw.has_damage_disclosure,
                has_repaint_disclosure=raw.has_repaint_disclosure,
                owner_count=raw.owner_count,
                description=raw.description,
                status=ListingStatus.ACTIVE.value,
                # Prefer the source's own publication date: our first crawl is
                # not when the car went on sale, and days-on-market computed
                # from it would understate every listing that predates us.
                first_seen_at=raw.posted_at or as_of,
                last_seen_at=as_of,
                content_fingerprint=fingerprint,
                raw_payload=dict(raw.raw_fields),
            )
            self.session.add(listing)
            await self.session.flush()
            self.session.add(
                ListingObservation(
                    listing_id=listing.id,
                    observed_at=as_of,
                    price_azn=price_azn.amount,
                    mileage_km=raw.mileage_km,
                    status=ListingStatus.ACTIVE.value,
                    change_kind="FIRST_SEEN",
                )
            )
            return "created"

        existing.last_seen_at = as_of
        if existing.status != ListingStatus.ACTIVE.value:
            existing.status = ListingStatus.ACTIVE.value
            existing.removed_at = None

        if existing.content_fingerprint == fingerprint:
            return "unchanged"

        changes: list[str] = []
        if existing.price_azn != price_azn.amount:
            changes.append("PRICE_CHANGE")
            existing.price_amount = raw.price.amount
            existing.price_currency = raw.price.currency.value
            existing.price_azn = price_azn.amount
        if raw.mileage_km is not None and existing.mileage_km != raw.mileage_km:
            changes.append("MILEAGE_CHANGE")
            existing.mileage_km = raw.mileage_km
        if raw.description and existing.description != raw.description:
            changes.append("DESCRIPTION_CHANGE")
            existing.description = raw.description

        existing.content_fingerprint = fingerprint
        existing.raw_payload = dict(raw.raw_fields)

        for change in changes:
            self.session.add(
                ListingObservation(
                    listing_id=existing.id,
                    observed_at=as_of,
                    price_azn=existing.price_azn,
                    mileage_km=existing.mileage_km,
                    status=existing.status,
                    change_kind=change,
                )
            )

        return "price_changed" if "PRICE_CHANGE" in changes else "updated"

    async def _ensure_configuration(self, raw: RawListing) -> None:
        """Register the vehicle configuration if this is the first sighting."""
        config = raw.configuration
        if not config.is_resolvable:
            return

        exists = (
            await self.session.scalars(
                select(VehicleConfigurationRow.config_id).where(
                    VehicleConfigurationRow.config_id == config.config_id
                )
            )
        ).first()
        if exists:
            return

        self.session.add(
            VehicleConfigurationRow(
                config_id=config.config_id,
                model_key=config.model_key,
                generation_key=config.generation_key,
                powertrain_key=config.powertrain_key,
                canonical_string=config.canonical_string,
                make=config.make,
                model=config.model,
                model_year=config.model_year,
                generation=config.generation,
                trim=config.trim,
                engine_code=config.engine_code,
                displacement_l=config.displacement_l,
                fuel=config.fuel.value,
                transmission=config.transmission.value,
                drivetrain=config.drivetrain.value,
                body=config.body.value,
                horsepower=config.horsepower,
                import_status=config.import_status.value,
                specificity=config.specificity,
            )
        )

    async def _mark_removed(
        self, source: MarketSource, seen: set[str], as_of: datetime
    ) -> int:
        """Mark listings the source no longer shows.

        A disappearance is not a sale — the seller may have withdrawn it, or it
        may have expired. The status says ``REMOVED`` and nothing infers a
        transaction from it (spec §9).
        """
        cutoff = as_of - timedelta(days=PRESUMED_REMOVED_DAYS)
        stale = (
            await self.session.scalars(
                select(Listing)
                .where(Listing.source_id == source.id)
                .where(Listing.status == ListingStatus.ACTIVE.value)
                .where(Listing.last_seen_at < cutoff)
            )
        ).all()

        removed = 0
        for listing in stale:
            if listing.external_id in seen:
                continue
            listing.status = ListingStatus.REMOVED.value
            listing.removed_at = as_of
            self.session.add(
                ListingObservation(
                    listing_id=listing.id,
                    observed_at=as_of,
                    price_azn=listing.price_azn,
                    mileage_km=listing.mileage_km,
                    status=ListingStatus.REMOVED.value,
                    change_kind="STATUS_CHANGE",
                )
            )
            removed += 1
        return removed

    def _to_azn(self, price: Money) -> Money | None:
        if price.currency is Currency.AZN:
            return price
        return self.fx.try_to(price, Currency.AZN)


class _Aborted(RuntimeError):
    """Internal signal that a run stopped early."""


# --- data quality (spec §45) ----------------------------------------------

#: Plausibility bounds. Anything outside these is a data error, not a cheap car.
MIN_PLAUSIBLE_PRICE_AZN = 300
MAX_PLAUSIBLE_PRICE_AZN = 3_000_000
MAX_PLAUSIBLE_MILEAGE_KM = 1_500_000
MIN_PLAUSIBLE_YEAR = 1950


def validate_raw_listing(listing: RawListing) -> list[tuple[str, str]]:
    """Structural plausibility checks, returned as ``(issue, detail)`` pairs.

    Deliberately narrow. The job is to catch data errors — a mistyped price, a
    mileage in metres — not to second-guess the market. A genuinely cheap car is
    signal worth keeping and explaining (spec §19), so the bounds sit far outside
    anything a real listing would reach.
    """
    issues: list[tuple[str, str]] = []
    price = listing.price.as_float()

    if price < MIN_PLAUSIBLE_PRICE_AZN:
        issues.append(
            ("IMPLAUSIBLE_PRICE", f"price {price:,.0f} below the plausible floor")
        )
    if price > MAX_PLAUSIBLE_PRICE_AZN:
        issues.append(
            ("IMPLAUSIBLE_PRICE", f"price {price:,.0f} above the plausible ceiling")
        )
    if listing.mileage_km is not None and listing.mileage_km > MAX_PLAUSIBLE_MILEAGE_KM:
        issues.append(
            ("IMPLAUSIBLE_MILEAGE", f"mileage {listing.mileage_km:,} km — check the units")
        )

    year = listing.configuration.model_year
    if year is not None:
        if year < MIN_PLAUSIBLE_YEAR:
            issues.append(("IMPLAUSIBLE_YEAR", f"model year {year}"))
        elif year > listing.observed_at.year + 2:
            issues.append(
                ("IMPLAUSIBLE_YEAR", f"model year {year} is in the future")
            )

    if not listing.configuration.is_resolvable:
        issues.append(
            (
                "UNRESOLVABLE_VEHICLE",
                "make or model could not be recognised, so the listing cannot "
                "participate in market comparison",
            )
        )

    return issues


def content_fingerprint(listing: RawListing, price_azn: Money) -> str:
    """Hash of the fields whose change is worth recording.

    Lets an incremental crawl skip a detail page whose summary has not moved,
    and keeps the observation table free of rows that record nothing.
    """
    parts: Sequence[str] = (
        f"{price_azn.as_float():.2f}",
        str(listing.mileage_km),
        listing.configuration.config_id,
        str(listing.city),
        str(listing.status.value),
        hashlib.sha256((listing.description or "").encode("utf-8")).hexdigest()[:16],
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
