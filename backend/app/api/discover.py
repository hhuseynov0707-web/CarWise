"""Budget-matched recommendations."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, status

from app.api.routes import get_container
from app.container import Container
from app.schemas.discover import (
    BudgetOut,
    DiscoverResponse,
    RecommendationOut,
)
from app.services.auth import SESSION_COOKIE, AuthService
from app.services.discover import MIN_OBSERVATIONS, DiscoverService

router = APIRouter()

NOTE_INFERRED = (
    "This budget is inferred from the vehicles you have analysed and saved, not "
    "from anything you told us. It is a guess about you rather than a measurement "
    "of the market, and you can replace it."
)
NOTE_STATED = "Showing vehicles inside the range you set."
NOTE_UNKNOWN = (
    "Not enough to estimate a budget yet. Analyse a few vehicles in the range you "
    "have in mind, or set a range directly."
)


@router.get("/discover", response_model=DiscoverResponse, tags=["market"])
async def discover(
    budget_low: Decimal | None = Query(default=None, ge=0),
    budget_high: Decimal | None = Query(default=None, ge=0),
    limit: int = Query(default=12, ge=1, le=50),
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> DiscoverResponse:
    """Vehicles that fit what this person appears to be shopping for.

    A stated range wins over an inferred one: someone who says what they can
    spend has given better evidence than their browsing.
    """
    if (budget_low is None) != (budget_high is None):
        raise HTTPException(
            status_code=422, detail="Give both ends of the range, or neither."
        )
    if budget_low is not None and budget_high is not None and budget_low > budget_high:
        raise HTTPException(status_code=422, detail="The range runs backwards.")

    async with container.database.session() as session:
        service = DiscoverService(session=session)

        if budget_low is not None and budget_high is not None:
            budget = service.stated_budget(budget_low, budget_high)
            note = NOTE_STATED
        else:
            user = await AuthService(session=session).user_for_token(autointel_session)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Sign in, or set a range directly.",
                )
            budget = await service.estimate_budget(user)
            note = NOTE_INFERRED if budget else NOTE_UNKNOWN

        recommendations = await service.recommend(budget, limit=limit) if budget else []

    return DiscoverResponse(
        budget=(
            None
            if budget is None
            else BudgetOut(
                low_azn=budget.low_azn,
                high_azn=budget.high_azn,
                centre_azn=budget.centre_azn,
                observations=budget.observations,
                source=budget.source,
            )
        ),
        observations_needed=MIN_OBSERVATIONS,
        note=note,
        recommendations=[
            RecommendationOut(
                listing_id=r.listing_id,
                source_url=r.source_url,
                make=r.make,
                model=r.model,
                model_year=r.model_year,
                city=r.city,
                mileage_km=r.mileage_km,
                price_azn=r.price_azn,
                median_azn=r.median_azn,
                vs_median_pct=None if r.vs_median_pct is None else round(r.vs_median_pct, 1),
            )
            for r in recommendations
        ],
    )
