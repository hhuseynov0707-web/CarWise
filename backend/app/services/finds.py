"""Listings priced below their own configuration's market.

What this is and is not. It reports a gap between one asking price and the
statistics for identically-configured cars, together with what that gap rests
on. It does not say the car is a good buy, and it cannot: the commonest reason
a car is cheaper than its peers is that something about it is worse, and
nothing here has looked at the car.

So every row carries the evidence next to the number — how many listings the
comparison is against, how much that market agrees with itself, and how the
mileage compares, because mileage is the ordinary explanation for a low price
and the one a reader should rule out first.

Snapshots do the work rather than a valuation per listing. A snapshot is
already the per-configuration aggregate this needs, computed over a window
rather than over whatever happens to be live, which is what keeps the quick
sellers from biasing it upward (audit §7.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, MarketSnapshot, VehicleConfigurationRow

#: The comparison window. The widest one carries the most listings, and a
#: used-car market does not move fast enough for six months to be stale.
WINDOW_DAYS = 180

#: Region ``ALL`` rather than a city: a car is not a different car in Gəncə,
#: and slicing by region here would thin every sample for no gain.
REGION = "ALL"

#: Below this the snapshot describes too few cars to call anything an outlier.
#: Snapshots already refuse to exist under five; this is stricter because a
#: claim about one listing is being made from them.
MIN_SAMPLE_SIZE = 8

#: How many to return. A screen of everything is a screen nobody reads.
DEFAULT_LIMIT = 15

#: Past this the gap stops being a bargain and starts being a warning.
#:
#: A car does not sell for two fifths of its market because the seller is
#: generous. At that distance the likely explanations are a wreck, a listing
#: whose price is really a finance deposit, or an error — and none of those
#: belong on a screen of things worth looking at. Genuine underpricing in this
#: market lives well inside this.
MAX_BELOW_MEDIAN_PCT = 40.0

#: Wording that means the number in the price field may not be the price of
#: the car. A deposit advertised as the price makes an ordinary car look like
#: the find of the year, and it is the single loudest false positive here.
DEPOSIT_PHRASES = ("ilkin ödəniş", "ilkin odenis", "первоначальный взнос")


@dataclass(frozen=True, slots=True)
class Find:
    """One listing, and everything needed to judge it."""

    listing_id: int
    source_url: str | None
    make: str | None
    model: str | None
    model_year: int | None
    city: str | None
    mileage_km: int | None
    price_azn: Decimal

    #: The configuration's market over the window.
    median_azn: Decimal
    sample_size: int
    dispersion: float | None
    median_mileage_km: int | None

    @property
    def below_median_pct(self) -> float:
        return float((self.median_azn - self.price_azn) / self.median_azn * 100)

    @property
    def mileage_vs_median_pct(self) -> float | None:
        """How much further this car has run than the configuration's median.

        Positive means more mileage, which is the first thing that would
        explain the price and the first thing a reader should check.
        """
        if not self.mileage_km or not self.median_mileage_km:
            return None
        return (self.mileage_km - self.median_mileage_km) / self.median_mileage_km * 100


@dataclass
class FindsService:
    session: AsyncSession

    async def today(self, limit: int = DEFAULT_LIMIT) -> list[Find]:
        latest = await self._latest_snapshot_date()
        if latest is None:
            # No snapshots have been built. An empty list is the honest answer;
            # computing something on the spot would be a different, weaker
            # claim wearing the same label.
            return []

        statement = self._statement(latest, limit)
        rows = (await self.session.execute(statement)).all()
        return [
            Find(
                listing_id=row.id,
                source_url=row.source_url,
                make=row.make,
                model=row.model,
                model_year=row.model_year,
                city=row.city,
                mileage_km=row.mileage_km,
                price_azn=row.price_azn,
                median_azn=row.median_azn,
                sample_size=row.sample_size,
                dispersion=row.dispersion,
                median_mileage_km=row.median_mileage_km,
            )
            for row in rows
        ]

    async def _latest_snapshot_date(self):
        return (
            await self.session.scalars(
                select(func.max(MarketSnapshot.snapshot_date)).where(
                    and_(
                        MarketSnapshot.region == REGION,
                        MarketSnapshot.window_days == WINDOW_DAYS,
                    )
                )
            )
        ).first()

    def _statement(self, snapshot_date, limit: int) -> Select:
        gap = (MarketSnapshot.median_azn - Listing.price_azn) / MarketSnapshot.median_azn

        return (
            select(
                Listing.id,
                Listing.source_url,
                Listing.city,
                Listing.mileage_km,
                Listing.price_azn,
                VehicleConfigurationRow.make,
                VehicleConfigurationRow.model,
                VehicleConfigurationRow.model_year,
                MarketSnapshot.median_azn,
                MarketSnapshot.sample_size,
                MarketSnapshot.dispersion,
                MarketSnapshot.median_mileage_km,
            )
            .join(MarketSnapshot, MarketSnapshot.config_id == Listing.config_id)
            .join(
                VehicleConfigurationRow,
                VehicleConfigurationRow.config_id == Listing.config_id,
            )
            .where(MarketSnapshot.snapshot_date == snapshot_date)
            .where(MarketSnapshot.region == REGION)
            .where(MarketSnapshot.window_days == WINDOW_DAYS)
            .where(MarketSnapshot.sample_size >= MIN_SAMPLE_SIZE)
            .where(MarketSnapshot.median_azn.is_not(None))
            .where(MarketSnapshot.p25_azn.is_not(None))
            .where(Listing.status == "ACTIVE")
            .where(Listing.price_azn > 0)
            # Only cars the seller states are undamaged. A wrecked car being
            # cheap is not a finding — it is the price working correctly — and
            # listing one here is the difference between a screen that helps
            # and a screen that wastes somebody's afternoon.
            .where(Listing.has_damage_disclosure.is_(False))
            # And nothing whose price might be a finance deposit.
            .where(_no_deposit_wording())
            # Below the first quartile rather than below the median: half of
            # any market is below its median, and calling that a find would
            # make the label meaningless.
            .where(Listing.price_azn < MarketSnapshot.p25_azn)
            .where(gap <= MAX_BELOW_MEDIAN_PCT / 100)
            .order_by(desc(gap))
            .limit(limit)
        )


def _no_deposit_wording():
    """True for listings whose description does not advertise a deposit."""
    from sqlalchemy import and_, not_, or_

    return or_(
        Listing.description.is_(None),
        and_(*[not_(Listing.description.ilike(f"%{phrase}%")) for phrase in DEPOSIT_PHRASES]),
    )
