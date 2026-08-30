"""What this person appears to be shopping for, and what fits it.

The budget is inferred from what someone has actually looked at rather than
asked for, which is the useful version of the idea and also the one that can
go wrong quietly. Three choices keep it honest.

**The median, not the mean.** One expensive car opened out of curiosity would
drag a mean upward and quietly reprice every recommendation that follows. A
median barely notices it.

**Below three observations, it refuses.** Two prices are not a budget, and a
number produced from them would carry the same confident formatting as one
produced from twenty. The screen asks instead.

**It is always shown, and always overridable.** The estimate is a guess about
a person, which is a weaker thing than the market statistics elsewhere in this
product, and it should be visibly a guess rather than arrive as a fact.

Recommendations are then listings inside the band that are *not* priced above
their own configuration's market — the budget says what someone can spend, and
the snapshot says whether a given car is a sensible way to spend it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AnalysisRecord,
    Listing,
    MarketSnapshot,
    SavedVehicle,
    User,
    VehicleConfigurationRow,
)
from app.services.finds import REGION, WINDOW_DAYS

#: Fewer than this and the estimate is not made at all.
MIN_OBSERVATIONS = 3

#: How wide the band is when the observations are too few to give it a shape
#: of their own. Deliberately generous: a narrow band around a weak estimate
#: is false precision twice over.
FALLBACK_SPREAD = 0.20

#: How many recommendations to return.
DEFAULT_LIMIT = 12


@dataclass(frozen=True, slots=True)
class Budget:
    """An estimated spending range, and what it was estimated from."""

    low_azn: Decimal
    high_azn: Decimal
    centre_azn: Decimal
    observations: int
    source: str
    """``history``, ``stated`` — so a caller can say which it is rather than
    presenting an inference and a declaration identically."""


@dataclass(frozen=True, slots=True)
class Recommendation:
    listing_id: int
    source_url: str | None
    make: str | None
    model: str | None
    model_year: int | None
    city: str | None
    mileage_km: int | None
    price_azn: Decimal
    median_azn: Decimal | None

    @property
    def vs_median_pct(self) -> float | None:
        if self.median_azn is None or self.median_azn == 0:
            return None
        return float((self.median_azn - self.price_azn) / self.median_azn * 100)


@dataclass
class DiscoverService:
    session: AsyncSession

    # --- what we know about the person -------------------------------------

    async def observed_prices(self, user: User) -> list[Decimal]:
        """Prices this person has engaged with.

        Analyses are what they looked at; a saved target price is what they
        said out loud. Both are evidence about the same question, so both
        count.
        """
        analysed = (
            await self.session.scalars(
                select(AnalysisRecord.asking_price_azn)
                .where(AnalysisRecord.user_id == user.id)
                .where(AnalysisRecord.asking_price_azn.is_not(None))
                .order_by(desc(AnalysisRecord.created_at))
                .limit(50)
            )
        ).all()

        targets = (
            await self.session.scalars(
                select(SavedVehicle.target_price_azn)
                .where(SavedVehicle.user_id == user.id)
                .where(SavedVehicle.target_price_azn.is_not(None))
            )
        ).all()

        return [p for p in [*analysed, *targets] if p is not None and p > 0]

    async def estimate_budget(self, user: User) -> Budget | None:
        prices = await self.observed_prices(user)
        if len(prices) < MIN_OBSERVATIONS:
            return None

        values = sorted(float(p) for p in prices)
        centre = statistics.median(values)

        # The person's own spread when there is enough of it to have a shape;
        # a flat band around the median otherwise.
        if len(values) >= 4:
            low, high = _quantile(values, 0.25), _quantile(values, 0.75)
        else:
            low, high = centre * (1 - FALLBACK_SPREAD), centre * (1 + FALLBACK_SPREAD)

        # A band that collapsed to a point would recommend nothing.
        if high - low < centre * 0.1:
            low, high = centre * (1 - FALLBACK_SPREAD), centre * (1 + FALLBACK_SPREAD)

        return Budget(
            low_azn=Decimal(str(round(low, 2))),
            high_azn=Decimal(str(round(high, 2))),
            centre_azn=Decimal(str(round(centre, 2))),
            observations=len(values),
            source="history",
        )

    @staticmethod
    def stated_budget(low: Decimal, high: Decimal) -> Budget:
        centre = (low + high) / 2
        return Budget(
            low_azn=low,
            high_azn=high,
            centre_azn=centre,
            observations=0,
            source="stated",
        )

    # --- what to show them --------------------------------------------------

    async def recommend(self, budget: Budget, limit: int = DEFAULT_LIMIT) -> list[Recommendation]:
        latest = (
            await self.session.scalars(
                select(func.max(MarketSnapshot.snapshot_date))
                .where(MarketSnapshot.region == REGION)
                .where(MarketSnapshot.window_days == WINDOW_DAYS)
            )
        ).first()

        statement = (
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
            )
            .join(
                VehicleConfigurationRow,
                VehicleConfigurationRow.config_id == Listing.config_id,
            )
            .outerjoin(
                MarketSnapshot,
                (MarketSnapshot.config_id == Listing.config_id)
                & (MarketSnapshot.snapshot_date == latest)
                & (MarketSnapshot.region == REGION)
                & (MarketSnapshot.window_days == WINDOW_DAYS),
            )
            .where(Listing.status == "ACTIVE")
            .where(Listing.price_azn >= budget.low_azn)
            .where(Listing.price_azn <= budget.high_azn)
            # Never recommend a car priced above its own market. Affordable and
            # overpriced is still overpriced, and this screen exists to spend a
            # budget well rather than merely to spend it.
            .where(
                (MarketSnapshot.median_azn.is_(None))
                | (Listing.price_azn <= MarketSnapshot.median_azn)
            )
            .order_by(desc(Listing.last_seen_at))
            .limit(limit)
        )

        rows = (await self.session.execute(statement)).all()
        return [
            Recommendation(
                listing_id=row.id,
                source_url=row.source_url,
                make=row.make,
                model=row.model,
                model_year=row.model_year,
                city=row.city,
                mileage_km=row.mileage_km,
                price_azn=row.price_azn,
                median_azn=row.median_azn,
            )
            for row in rows
        ]


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated quantile. Small samples are the normal case here."""
    if not sorted_values:
        raise ValueError("no values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


async def record_analysis(
    session: AsyncSession,
    *,
    user: User | None,
    analysis,  # app.services.analysis.Analysis
) -> None:
    """Keep a signed-in user's analysis, so a budget can be inferred later.

    Anonymous analyses are not stored. There would be nobody to attribute them
    to, and keeping them anyway would be collecting for its own sake.
    """
    if user is None:
        return

    result = analysis.result
    position = result.position
    valuation = result.valuation

    session.add(
        AnalysisRecord(
            analysis_id=result.analysis_id,
            user_id=user.id,
            config_id=result.subject.configuration.config_id,
            asking_price_azn=(
                position.asking_price.amount if position.asking_price else None
            ),
            central_estimate_azn=(
                valuation.central_estimate.amount if valuation.central_estimate else None
            ),
            fair_market_low_azn=(
                valuation.fair_market_low.amount if valuation.fair_market_low else None
            ),
            fair_market_high_azn=(
                valuation.fair_market_high.amount if valuation.fair_market_high else None
            ),
            price_basis=valuation.basis.value,
            rating=position.rating.value,
            price_difference_pct=position.difference_pct,
            price_percentile=position.percentile,
            risk_score=result.risk.score,
            confidence=result.confidence.percent,
            comparable_count=result.comparables.size,
            evidence_bundle=analysis.evidence_bundle,
            narrative=None,
            narrative_source=(
                analysis.narrative.generated_by if analysis.narrative else "none"
            ),
            generated_at=result.generated_at or datetime.now(UTC),
        )
    )
