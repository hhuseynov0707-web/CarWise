"""Market snapshot aggregation (spec §36, §44, §51 Workflow B).

Rolls listing observations up into per-configuration, per-region statistics on a
schedule. Those snapshots are what make market trends, depreciation curves and
the admin dashboard possible without recomputing over the whole listing table on
every request.

One decision here is load-bearing, and it is easy to get wrong in a way nobody
notices for months.

**Snapshots are computed over listings observed *within the window*, including
ones since removed — not over the currently-active set.** Cars priced well sell
quickly and leave the active set quickly; cars priced badly linger. A snapshot
of live listings is therefore a snapshot of the market's leftovers, and every
median it produces is biased upward. Including everything observed in the window
removes that bias (audit §7.5).

The cost is that a removed listing might have been withdrawn rather than sold,
so the window is not a clean sample of transactions either. It is a sample of
*what was on offer*, which is exactly what an asking-price statistic should be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, ListingObservation, MarketSnapshot
from app.domain.enums import ListingStatus, PriceBasis
from app.engines.stats import (
    coefficient_of_variation,
    detect_outliers,
    median,
    quantile_set,
)

#: Minimum observations before a snapshot is written. Below this the statistics
#: would be noise wearing the costume of a market rate.
MIN_SNAPSHOT_SAMPLE = 5

#: Standard windows. Short ones track movement; long ones stabilise thin
#: configurations that would otherwise never reach the minimum sample.
DEFAULT_WINDOWS = (30, 90, 180)


@dataclass(frozen=True, slots=True)
class SnapshotScope:
    """One configuration-and-region slice to aggregate."""

    config_id: str | None
    model_key: str | None
    region: str


@dataclass
class SnapshotReport:
    scopes_considered: int = 0
    snapshots_written: int = 0
    skipped_thin: int = 0


@dataclass
class SnapshotService:
    """Computes and stores market snapshots."""

    session: AsyncSession

    async def build(
        self,
        as_of: datetime,
        windows: tuple[int, ...] = DEFAULT_WINDOWS,
    ) -> SnapshotReport:
        report = SnapshotReport()

        for window_days in windows:
            cutoff = as_of - timedelta(days=window_days)
            scopes = await self._scopes(cutoff)

            for scope in scopes:
                report.scopes_considered += 1
                written = await self._snapshot(scope, cutoff, as_of, window_days)
                if written:
                    report.snapshots_written += 1
                else:
                    report.skipped_thin += 1

        await self.session.flush()
        return report

    async def _scopes(self, cutoff: datetime) -> list[SnapshotScope]:
        """Every configuration-and-region slice with activity in the window.

        Also emits a national slice (``region="ALL"``) per configuration, so a
        thinly-traded model still has a usable statistic even when no single
        city reaches the minimum sample.
        """
        rows = (
            await self.session.execute(
                select(Listing.config_id, Listing.model_key, Listing.region)
                .where(Listing.last_seen_at >= cutoff)
                .where(Listing.config_id.is_not(None))
                .distinct()
            )
        ).all()

        scopes: list[SnapshotScope] = []
        national: set[tuple[str | None, str | None]] = set()
        for config_id, model_key, region in rows:
            scopes.append(SnapshotScope(config_id, model_key, region or "UNKNOWN"))
            national.add((config_id, model_key))

        scopes.extend(
            SnapshotScope(config_id, model_key, "ALL") for config_id, model_key in national
        )
        return scopes

    async def _snapshot(
        self,
        scope: SnapshotScope,
        cutoff: datetime,
        as_of: datetime,
        window_days: int,
    ) -> bool:
        statement = (
            select(Listing)
            .where(Listing.config_id == scope.config_id)
            .where(Listing.last_seen_at >= cutoff)
            .where(Listing.price_azn > 0)
        )
        if scope.region != "ALL":
            statement = statement.where(Listing.region == scope.region)

        listings = (await self.session.scalars(statement)).unique().all()
        if len(listings) < MIN_SNAPSHOT_SAMPLE:
            return False

        prices = [float(listing.price_azn) for listing in listings]

        # Trim before computing anything: one mistyped listing destroys a mean
        # and badly damages an unweighted standard deviation.
        outliers = detect_outliers(prices)
        kept = [prices[index] for index in outliers.kept_indices]
        if len(kept) < MIN_SNAPSHOT_SAMPLE:
            kept = prices

        quantiles = quantile_set(kept)
        mileages = [
            float(listing.mileage_km) for listing in listings if listing.mileage_km is not None
        ]
        days_on_market = [
            float(max(0, (min(listing.last_seen_at, as_of) - listing.first_seen_at).days))
            for listing in listings
        ]

        reductions = await self._price_reductions(scope, cutoff)
        new_listings = sum(1 for listing in listings if listing.first_seen_at >= cutoff)
        removed = sum(
            1 for listing in listings if listing.status == ListingStatus.REMOVED.value
        )

        existing = (
            await self.session.scalars(
                select(MarketSnapshot)
                .where(MarketSnapshot.config_id == scope.config_id)
                .where(MarketSnapshot.region == scope.region)
                .where(MarketSnapshot.snapshot_date == as_of)
                .where(MarketSnapshot.window_days == window_days)
            )
        ).first()

        snapshot = existing or MarketSnapshot(
            config_id=scope.config_id,
            model_key=scope.model_key,
            region=scope.region,
            snapshot_date=as_of,
            window_days=window_days,
        )

        snapshot.sample_size = len(kept)
        # Always ASKING for now. It flips per configuration once enough
        # transaction observations exist (spec §9); nothing here assumes a
        # conversion factor between the two.
        snapshot.price_basis = PriceBasis.ASKING.value
        snapshot.median_azn = _decimal(quantiles.p50)
        snapshot.mean_azn = _decimal(sum(kept) / len(kept))
        snapshot.p10_azn = _decimal(quantiles.p10)
        snapshot.p25_azn = _decimal(quantiles.p25)
        snapshot.p75_azn = _decimal(quantiles.p75)
        snapshot.p90_azn = _decimal(quantiles.p90)
        snapshot.dispersion = round(coefficient_of_variation(kept), 4)
        snapshot.median_mileage_km = int(median(mileages)) if mileages else None
        snapshot.median_days_on_market = (
            round(median(days_on_market), 1) if days_on_market else None
        )
        snapshot.new_listings = new_listings
        snapshot.removed_listings = removed
        snapshot.price_reductions = len(reductions)
        snapshot.median_reduction_pct = (
            round(median(reductions), 2) if len(reductions) >= 3 else None
        )

        if existing is None:
            self.session.add(snapshot)
        return True

    async def _price_reductions(
        self, scope: SnapshotScope, cutoff: datetime
    ) -> list[float]:
        """Observed percentage price moves within the window.

        Measured from the observation table rather than assumed. This is the
        closest honest proxy for negotiating room until real transaction data
        exists — it records what sellers in this exact segment actually did.
        """
        statement = (
            select(ListingObservation.listing_id, ListingObservation.price_azn)
            .join(Listing, Listing.id == ListingObservation.listing_id)
            .where(Listing.config_id == scope.config_id)
            .where(ListingObservation.observed_at >= cutoff)
            .order_by(ListingObservation.listing_id, ListingObservation.observed_at)
        )
        if scope.region != "ALL":
            statement = statement.where(Listing.region == scope.region)

        rows = (await self.session.execute(statement)).all()

        first: dict[int, float] = {}
        last: dict[int, float] = {}
        for listing_id, price in rows:
            value = float(price)
            first.setdefault(listing_id, value)
            last[listing_id] = value

        moves: list[float] = []
        for listing_id, start in first.items():
            end = last[listing_id]
            if start > 0 and end != start:
                moves.append((end - start) / start * 100.0)
        return moves


def _decimal(value: float):  # type: ignore[no-untyped-def]
    from decimal import Decimal

    return Decimal(f"{value:.2f}")
