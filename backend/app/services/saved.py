"""Vehicles a user is keeping an eye on.

Two rules are worth naming because both are easy to get wrong quietly.

**Every query is scoped by user.** A saved row is addressed by its own id, so
a lookup by id alone would let anyone who can guess an integer read or delete
somebody else's list. The user is part of every WHERE clause here rather than
checked afterwards, so there is no path that forgets.

**The same configuration is not saved twice.** The table's unique constraint
is on ``(user_id, listing_id)``, and these rows carry no listing — they track
a configuration, not one advert. Postgres treats NULLs as distinct, so that
constraint never fires for them and nothing in the database would object to
the same car appearing in a list five times.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SavedVehicle, User


class SavedVehicleError(RuntimeError):
    """The request was refused. The message is safe to show."""


class AlreadySaved(SavedVehicleError):
    pass


@dataclass
class SavedVehicleService:
    session: AsyncSession

    async def list_for(self, user: User) -> list[SavedVehicle]:
        return list(
            (
                await self.session.scalars(
                    select(SavedVehicle)
                    .where(SavedVehicle.user_id == user.id)
                    .order_by(SavedVehicle.created_at.desc())
                )
            ).all()
        )

    async def get(self, user: User, saved_id: int) -> SavedVehicle | None:
        """Scoped by user on purpose — see the module docstring."""
        return (
            await self.session.scalars(
                select(SavedVehicle)
                .where(SavedVehicle.id == saved_id)
                .where(SavedVehicle.user_id == user.id)
            )
        ).first()

    async def save(
        self,
        user: User,
        *,
        config_id: str,
        analysis_id: str | None = None,
        label: str | None = None,
        target_price_azn: Decimal | None = None,
        notify_on_price_drop: bool = True,
        notify_on_removal: bool = True,
    ) -> SavedVehicle:
        existing = (
            await self.session.scalars(
                select(SavedVehicle)
                .where(SavedVehicle.user_id == user.id)
                .where(SavedVehicle.config_id == config_id)
            )
        ).first()
        if existing is not None:
            raise AlreadySaved("This vehicle is already in your saved list.")

        row = SavedVehicle(
            user_id=user.id,
            config_id=config_id,
            analysis_id=analysis_id,
            label=(label or "").strip() or None,
            target_price_azn=target_price_azn,
            notify_on_price_drop=notify_on_price_drop,
            notify_on_removal=notify_on_removal,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self,
        row: SavedVehicle,
        *,
        label: str | None = None,
        target_price_azn: Decimal | None = None,
        notify_on_price_drop: bool | None = None,
        notify_on_removal: bool | None = None,
    ) -> SavedVehicle:
        if label is not None:
            row.label = label.strip() or None
        if target_price_azn is not None:
            row.target_price_azn = target_price_azn
        if notify_on_price_drop is not None:
            row.notify_on_price_drop = notify_on_price_drop
        if notify_on_removal is not None:
            row.notify_on_removal = notify_on_removal
        row.updated_at = datetime.now(UTC)
        return row

    async def remove(self, row: SavedVehicle) -> None:
        await self.session.delete(row)
