"""Accounts and sessions.

These assert the properties that are quiet when they break. A login that
answers differently for an unknown address is still a working login; a session
token stored in the clear still signs people in. Both are only visible if
something checks.

The session is a stand-in rather than a database. The project has no async
SQLite driver and these are unit tests; the integration path was exercised
against Postgres separately.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.sql.elements import BooleanClauseList

from app.db.models import User, UserSession
from app.services.auth import (
    AuthError,
    AuthService,
    EmailAlreadyRegistered,
    normalise_email,
)


def _matches(row, clause) -> bool:
    """Evaluate the WHERE clauses the service actually builds.

    Equality and IS NULL, combined with AND. Ignoring the clause entirely would
    have made every lookup return the first row, so a query for a token that
    does not exist would have found one — which is exactly the case worth
    testing.
    """
    if isinstance(clause, BooleanClauseList):
        return all(_matches(row, part) for part in clause.clauses)

    left = getattr(row, clause.left.key, None)
    operator = clause.operator.__name__
    if operator == "is_":
        return left is None
    if operator == "eq":
        return left == clause.right.value
    raise NotImplementedError(f"the stand-in does not evaluate {operator}")


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeSession:
    """Enough AsyncSession to run the service.

    Matching is by type rather than by parsing the statement: the service only
    ever selects one kind of row at a time, so the entity being selected is the
    whole question.
    """

    def __init__(self, users: list[User] | None = None, sessions: list[UserSession] | None = None):
        self.users = users or []
        self.sessions = sessions or []
        self.added: list = []

    async def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = self.users if entity is User else self.sessions
        clause = statement.whereclause
        if clause is not None:
            rows = [row for row in rows if _matches(row, clause)]
        return _Result(list(rows))

    def add(self, obj) -> None:
        self.added.append(obj)
        if isinstance(obj, User):
            self.users.append(obj)
        elif isinstance(obj, UserSession):
            self.sessions.append(obj)

    async def flush(self) -> None:
        """Assign ids and apply column defaults, as a real flush does.

        ``is_active`` and the rest are column defaults, applied by the INSERT
        rather than by the constructor, so an unflushed instance has None where
        a stored one has True. Skipping that here made a signed-in account look
        disabled — a property of the stand-in, not of the service.
        """
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = index
            for column in obj.__table__.columns:
                default = column.default
                if default is None or not default.is_scalar:
                    continue
                if getattr(obj, column.key, None) is None:
                    setattr(obj, column.key, default.arg)

    async def get(self, _model, ident):
        return next((u for u in self.users if u.id == ident), None)


def _service(**kwargs) -> tuple[AuthService, FakeSession]:
    session = FakeSession(**kwargs)
    return AuthService(session=session), session  # type: ignore[arg-type]


def _register(service: AuthService, **kwargs) -> User:
    payload = {"email": "a@example.com", "password": "long-enough-password"}
    payload.update(kwargs)
    return asyncio.run(service.register(**payload))  # type: ignore[arg-type]


class TestEmailHandling:
    def test_addresses_are_compared_case_insensitively(self) -> None:
        assert normalise_email("  Test.User@Example.COM ") == "test.user@example.com"

    def test_a_second_registration_of_the_same_address_is_refused(self) -> None:
        service, _ = _service()
        _register(service, email="Someone@Example.com")

        with pytest.raises(EmailAlreadyRegistered):
            _register(service, email="SOMEONE@example.com")


class TestRegistrationRules:
    def test_a_short_password_is_refused(self) -> None:
        service, _ = _service()
        with pytest.raises(AuthError, match="at least"):
            _register(service, password="short")

    def test_an_implausible_birth_year_is_refused(self) -> None:
        service, _ = _service()
        with pytest.raises(AuthError, match="birth year"):
            _register(service, birth_year=1830)

    def test_the_password_is_not_stored_in_the_clear(self) -> None:
        service, _ = _service()
        user = _register(service, password="a-memorable-passphrase")

        assert user.password_hash is not None
        assert "a-memorable-passphrase" not in user.password_hash
        assert user.password_hash.startswith("$argon2id$")

    def test_a_display_name_is_composed_from_the_parts(self) -> None:
        service, _ = _service()
        user = _register(service, first_name="  Hüseyn ", last_name="Hüseynov")

        assert user.display_name == "Hüseyn Hüseynov"


class TestSignIn:
    def _registered(self) -> tuple[AuthService, User]:
        service, _ = _service()
        user = _register(service, email="owner@example.com", password="correct-horse-staple")
        return service, user

    def test_the_right_password_signs_in(self) -> None:
        service, user = self._registered()
        result = asyncio.run(
            service.authenticate(email="owner@example.com", password="correct-horse-staple")
        )
        assert result is user
        assert result.last_login_at is not None

    def test_the_wrong_password_is_refused(self) -> None:
        service, _ = self._registered()
        with pytest.raises(AuthError) as wrong:
            asyncio.run(service.authenticate(email="owner@example.com", password="not-it-at-all"))
        assert "incorrect" in str(wrong.value)

    def test_an_unknown_address_fails_the_same_way_as_a_wrong_password(self) -> None:
        """Otherwise the login form answers "is this person registered?".

        The service verifies an unknown address against a dummy hash rather
        than returning early, so the two also take comparable time.
        """
        service, _ = self._registered()

        with pytest.raises(AuthError) as unknown:
            asyncio.run(service.authenticate(email="nobody@example.com", password="whatever-here"))
        with pytest.raises(AuthError) as wrong:
            asyncio.run(service.authenticate(email="owner@example.com", password="whatever-here"))

        assert str(unknown.value) == str(wrong.value)

    def test_a_disabled_account_is_refused_with_the_right_password(self) -> None:
        service, user = self._registered()
        user.is_active = False

        with pytest.raises(AuthError, match="disabled"):
            asyncio.run(
                service.authenticate(email="owner@example.com", password="correct-horse-staple")
            )


class TestSessions:
    def _signed_in(self) -> tuple[AuthService, FakeSession, User, str]:
        service, session = _service()
        user = _register(service, email="owner@example.com")
        token = asyncio.run(service.issue_session(user))
        return service, session, user, token

    def test_the_token_is_stored_hashed_never_in_the_clear(self) -> None:
        _, session, _, token = self._signed_in()
        stored = session.sessions[0]

        assert stored.token_hash != token
        assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert len(stored.token_hash) == 64

    def test_a_valid_token_resolves_to_its_user(self) -> None:
        service, _, user, token = self._signed_in()
        assert asyncio.run(service.user_for_token(token)) is user

    def test_an_unknown_token_resolves_to_nobody(self) -> None:
        service, _, _, _ = self._signed_in()
        assert asyncio.run(service.user_for_token("not-a-real-token")) is None
        assert asyncio.run(service.user_for_token(None)) is None

    def test_a_revoked_session_stops_working(self) -> None:
        service, _, _, token = self._signed_in()
        asyncio.run(service.revoke(token))
        assert asyncio.run(service.user_for_token(token)) is None

    def test_an_expired_session_stops_working(self) -> None:
        service, session, _, token = self._signed_in()
        session.sessions[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert asyncio.run(service.user_for_token(token)) is None

    def test_signing_out_everywhere_revokes_every_session(self) -> None:
        """The reason sessions are rows: a signed token could not be recalled."""
        service, session, user, first = self._signed_in()
        second = asyncio.run(service.issue_session(user))

        assert asyncio.run(service.revoke_all(user)) == 2
        assert asyncio.run(service.user_for_token(first)) is None
        assert asyncio.run(service.user_for_token(second)) is None
        assert all(row.revoked_at is not None for row in session.sessions)
