"""Listings priced below their own configuration's market."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.routes import get_container
from app.container import Container
from app.schemas.finds import FindOut, FindsResponse
from app.services.finds import DEFAULT_LIMIT, MIN_SAMPLE_SIZE, WINDOW_DAYS, FindsService

router = APIRouter()

CAVEAT = (
    "These are listings whose asking price sits below the first quartile for "
    "their exact configuration. That is a statement about price, not about the "
    "car: the commonest reason one is cheaper than its peers is that something "
    "about it is worse, and nothing here has inspected it. Check the mileage "
    "figure first, then run the full analysis."
)


@router.get("/finds", response_model=FindsResponse, tags=["market"])
async def todays_finds(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=50),
    container: Container = Depends(get_container),
) -> FindsResponse:
    """Today's finds.

    Reads the stored snapshots rather than valuing every listing on request.
    An empty list means no snapshots have been built yet, which is reported
    plainly rather than filled with something computed on weaker terms.
    """
    async with container.database.read_session() as session:
        finds = await FindsService(session=session).today(limit=limit)

    return FindsResponse(
        generated_from_snapshot=bool(finds),
        window_days=WINDOW_DAYS,
        min_sample_size=MIN_SAMPLE_SIZE,
        caveat=CAVEAT,
        finds=[
            FindOut(
                listing_id=f.listing_id,
                source_url=f.source_url,
                make=f.make,
                model=f.model,
                model_year=f.model_year,
                city=f.city,
                mileage_km=f.mileage_km,
                price_azn=f.price_azn,
                median_azn=f.median_azn,
                below_median_pct=round(f.below_median_pct, 1),
                sample_size=f.sample_size,
                dispersion=f.dispersion,
                median_mileage_km=f.median_mileage_km,
                mileage_vs_median_pct=(
                    None if f.mileage_vs_median_pct is None
                    else round(f.mileage_vs_median_pct, 1)
                ),
            )
            for f in finds
        ],
    )
