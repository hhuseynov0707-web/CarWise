"""Saved vehicles.

The property worth guarding is isolation. A saved row is addressed by an
integer, so a lookup that forgets the user is a working feature that also
hands one person's list to anyone who counts upward. Nothing about the screen
would look wrong.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.db.models import SavedVehicle, User
from app.services.saved import AlreadySaved, SavedVehicleService
from tests.test_auth import FakeSession, _matches


class _SavedSession(FakeSession):
    """FakeSession plus the saved rows and a delete."""

    def __init__(self, users: list[User], saved: list[SavedVehicle]) -> None:
        super().__init__(users=users)
        self.saved = saved
        self.deleted: list[SavedVehicle] = []

    async def scalars(self, statement):  # type: ignore[override]
        entity = statement.column_descriptions[0]["entity"]
        if entity is not SavedVehicle:
            return await super().scalars(statement)
        rows = self.saved
        clause = statement.whereclause
        if clause is not None:
            rows = [row for row in rows if _matches(row, clause)]
        return _Rows(list(rows))

    def add(self, obj) -> None:  # type: ignore[override]
        if isinstance(obj, SavedVehicle):
            self.added.append(obj)
            self.saved.append(obj)
            return
        super().add(obj)

    async def delete(self, obj) -> None:
        self.deleted.append(obj)
        if obj in self.saved:
            self.saved.remove(obj)


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


def _user(user_id: int) -> User:
    user = User(email=f"u{user_id}@example.com")
    user.id = user_id
    return user


def _setup(saved: list[SavedVehicle] | None = None):
    owner, stranger = _user(1), _user(2)
    session = _SavedSession([owner, stranger], saved or [])
    return SavedVehicleService(session=session), session, owner, stranger  # type: ignore[arg-type]


def _row(row_id: int, user_id: int, config_id: str = "cfg_a") -> SavedVehicle:
    row = SavedVehicle(user_id=user_id, config_id=config_id)
    row.id = row_id
    return row


class TestIsolation:
    def test_a_list_contains_only_the_users_own_rows(self) -> None:
        service, _, owner, _ = _setup([_row(1, 1, "cfg_a"), _row(2, 2, "cfg_b")])
        rows = asyncio.run(service.list_for(owner))

        assert [r.id for r in rows] == [1]

    def test_another_users_row_cannot_be_fetched_by_id(self) -> None:
        """The id exists. It is still not this caller's to read."""
        service, _, owner, _ = _setup([_row(7, 2)])

        assert asyncio.run(service.get(owner, 7)) is None

    def test_the_owner_can_fetch_their_own_row(self) -> None:
        service, _, owner, _ = _setup([_row(7, 1)])

        assert asyncio.run(service.get(owner, 7)) is not None


class TestSaving:
    def test_a_vehicle_is_saved_with_its_configuration(self) -> None:
        service, _, owner, _ = _setup()
        row = asyncio.run(
            service.save(owner, config_id="cfg_x", label="  the silver one  ")
        )

        assert row.user_id == owner.id
        assert row.config_id == "cfg_x"
        assert row.label == "the silver one", "surrounding whitespace should not be stored"

    def test_the_same_configuration_is_not_saved_twice(self) -> None:
        """The table's unique constraint is on (user_id, listing_id), and these
        rows carry no listing — Postgres treats the NULLs as distinct, so
        nothing in the database would object."""
        service, _, owner, _ = _setup()
        asyncio.run(service.save(owner, config_id="cfg_x"))

        with pytest.raises(AlreadySaved):
            asyncio.run(service.save(owner, config_id="cfg_x"))

    def test_two_users_may_each_save_the_same_configuration(self) -> None:
        service, _, owner, stranger = _setup()
        asyncio.run(service.save(owner, config_id="cfg_x"))

        # Not a duplicate: it is a different person's list.
        assert asyncio.run(service.save(stranger, config_id="cfg_x")) is not None

    def test_an_empty_label_is_stored_as_nothing(self) -> None:
        service, _, owner, _ = _setup()
        row = asyncio.run(service.save(owner, config_id="cfg_x", label="   "))

        assert row.label is None


class TestUpdating:
    def test_omitted_fields_are_left_alone(self) -> None:
        """A patch, not a replacement: a form that did not send a field is not
        asking for it to be cleared."""
        service, _, _, _ = _setup()
        row = _row(1, 1)
        row.label = "keep me"
        row.target_price_azn = Decimal("40000")

        asyncio.run(service.update(row, notify_on_price_drop=False))

        assert row.label == "keep me"
        assert row.target_price_azn == Decimal("40000")
        assert row.notify_on_price_drop is False

    def test_a_target_price_can_be_set(self) -> None:
        service, _, _, _ = _setup()
        row = _row(1, 1)

        asyncio.run(service.update(row, target_price_azn=Decimal("38500")))

        assert row.target_price_azn == Decimal("38500")


class TestRemoval:
    def test_removing_takes_it_out_of_the_list(self) -> None:
        service, session, owner, _ = _setup([_row(1, 1)])
        row = asyncio.run(service.get(owner, 1))
        assert row is not None

        asyncio.run(service.remove(row))

        assert session.deleted == [row]
        assert asyncio.run(service.list_for(owner)) == []
