"""Saved-vehicle endpoints.

Every route resolves the user inside the same database session it then works
in. Reusing the ``require_user`` dependency would hand back a user loaded in a
session that has already closed, and the saved rows have to be read and
written alongside a live one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import get_container
from app.container import Container
from app.db.models import SavedVehicle, User
from app.schemas.saved import (
    SavedVehicleResponse,
    SavedVehicleUpdate,
    SaveVehicleRequest,
)
from app.services.auth import SESSION_COOKIE, AuthService
from app.services.saved import AlreadySaved, SavedVehicleService

router = APIRouter()


async def _signed_in(session: AsyncSession, token: str | None) -> User:
    user = await AuthService(session=session).user_for_token(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def _to_response(row: SavedVehicle) -> SavedVehicleResponse:
    return SavedVehicleResponse(
        id=row.id,
        config_id=row.config_id,
        analysis_id=row.analysis_id,
        label=row.label,
        target_price_azn=row.target_price_azn,
        last_seen_price_azn=row.last_seen_price_azn,
        notify_on_price_drop=row.notify_on_price_drop,
        notify_on_removal=row.notify_on_removal,
        created_at=row.created_at,
    )


@router.get("/saved", response_model=list[SavedVehicleResponse], tags=["saved"])
async def list_saved(
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> list[SavedVehicleResponse]:
    async with container.database.session() as session:
        user = await _signed_in(session, autointel_session)
        rows = await SavedVehicleService(session=session).list_for(user)
        return [_to_response(row) for row in rows]


@router.post(
    "/saved",
    response_model=SavedVehicleResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["saved"],
)
async def save_vehicle(
    payload: SaveVehicleRequest,
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> SavedVehicleResponse:
    async with container.database.session() as session:
        user = await _signed_in(session, autointel_session)
        try:
            row = await SavedVehicleService(session=session).save(
                user,
                config_id=payload.config_id,
                analysis_id=payload.analysis_id,
                label=payload.label,
                target_price_azn=payload.target_price_azn,
                notify_on_price_drop=payload.notify_on_price_drop,
                notify_on_removal=payload.notify_on_removal,
            )
        except AlreadySaved as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return _to_response(row)


@router.patch("/saved/{saved_id}", response_model=SavedVehicleResponse, tags=["saved"])
async def update_saved(
    saved_id: int,
    payload: SavedVehicleUpdate,
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> SavedVehicleResponse:
    async with container.database.session() as session:
        user = await _signed_in(session, autointel_session)
        service = SavedVehicleService(session=session)
        row = await service.get(user, saved_id)
        if row is None:
            # 404 rather than 403 for a row belonging to someone else: telling
            # a caller that an id exists but is not theirs is still telling
            # them it exists.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

        await service.update(
            row,
            label=payload.label,
            target_price_azn=payload.target_price_azn,
            notify_on_price_drop=payload.notify_on_price_drop,
            notify_on_removal=payload.notify_on_removal,
        )
        return _to_response(row)


@router.delete("/saved/{saved_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["saved"])
async def delete_saved(
    saved_id: int,
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    async with container.database.session() as session:
        user = await _signed_in(session, autointel_session)
        service = SavedVehicleService(session=session)
        row = await service.get(user, saved_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
        await service.remove(row)
