"""Accounts and sessions.

Three decisions are worth stating, because each has a tempting cheaper version.

**Argon2id for passwords.** A password is low-entropy and an attacker with the
hashes gets unlimited offline guesses, so the hash has to be expensive on
purpose. Argon2id is the current first choice and is the one KDF this
environment already carries.

**Opaque server-side sessions, stored hashed.** A signed token cannot be
revoked before it expires, which makes "sign out everywhere" a lie. A row can.
And the token is kept as a SHA-256 digest, so a leaked database contains no
usable sessions — the same reasoning that applies to the password, applied to
the thing that stands in for it.

SHA-256 rather than Argon2 for the token specifically: a 256-bit random string
has nothing to brute-force, and running a deliberately slow KDF on every
authenticated request would buy nothing and cost every request.

**Login does not say which half was wrong.** "No such account" turns the login
form into a way to ask whether an address is registered. An unknown address is
verified against a dummy hash anyway, so answering takes the same time either
way.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserSession
from app.schemas.auth import MIN_BIRTH_YEAR, MIN_PASSWORD_LENGTH

#: How long a session stays valid without being refreshed.
SESSION_TTL = timedelta(days=30)

#: Cookie the session token travels in. Defined here rather than in the API
#: module so that routes which only want to know "who is asking, if anyone"
#: can read it without importing the auth endpoints — which import them.
SESSION_COOKIE = "autointel_session"

# The password and birth-year limits live in the schema layer, where the
# request is validated. Importing them the other way round would have the
# schemas depending on the services, which the layering forbids and which
# would also put an input constraint further from the input than it needs.


class AuthError(RuntimeError):
    """Registration or sign-in was refused. The message is safe to show."""


class EmailAlreadyRegistered(AuthError):
    pass


def normalise_email(raw: str) -> str:
    """Lowercase and strip. Addresses are compared case-insensitively.

    Only the domain is formally case-insensitive, but no provider a person is
    likely to use treats the local part as case-sensitive, and letting
    ``A@x.com`` and ``a@x.com`` register separately would surprise everyone.
    """
    return raw.strip().lower()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class AuthService:
    session: AsyncSession
    hasher: PasswordHasher = field(default_factory=PasswordHasher)

    #: Verified against when the address is unknown, so that a wrong address
    #: and a wrong password cost the same. Built once; the value is irrelevant.
    _dummy_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self._dummy_hash = self.hasher.hash("timing-equaliser")

    # --- registration ------------------------------------------------------

    async def register(
        self,
        *,
        email: str,
        password: str,
        first_name: str | None = None,
        last_name: str | None = None,
        birth_year: int | None = None,
        locale: str = "az",
    ) -> User:
        address = normalise_email(email)
        if "@" not in address or len(address) < 5:
            raise AuthError("That does not look like an email address.")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError(
                f"Use at least {MIN_PASSWORD_LENGTH} characters so the password is "
                f"worth hashing."
            )
        if birth_year is not None:
            this_year = datetime.now(UTC).year
            if not MIN_BIRTH_YEAR <= birth_year <= this_year:
                raise AuthError(f"Enter a birth year between {MIN_BIRTH_YEAR} and {this_year}.")

        existing = (
            await self.session.scalars(select(User).where(User.email == address))
        ).first()
        if existing is not None:
            raise EmailAlreadyRegistered("That address is already registered.")

        user = User(
            email=address,
            password_hash=self.hasher.hash(password),
            first_name=(first_name or "").strip() or None,
            last_name=(last_name or "").strip() or None,
            birth_year=birth_year,
            display_name=_display_name(first_name, last_name) or None,
            locale=locale,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    # --- sign in -----------------------------------------------------------

    async def authenticate(self, *, email: str, password: str) -> User:
        address = normalise_email(email)
        user = (await self.session.scalars(select(User).where(User.email == address))).first()

        stored = user.password_hash if user and user.password_hash else self._dummy_hash
        try:
            self.hasher.verify(stored, password)
        except (VerifyMismatchError, InvalidHashError):
            raise AuthError("Email or password is incorrect.") from None

        # Reached only when the hash matched, so a user with no password set
        # cannot arrive here — the dummy hash never verifies against theirs.
        if user is None or not user.password_hash:
            raise AuthError("Email or password is incorrect.")
        if not user.is_active:
            raise AuthError("This account has been disabled.")

        # Argon2 parameters change over time; rehash quietly when they have.
        if self.hasher.check_needs_rehash(user.password_hash):
            user.password_hash = self.hasher.hash(password)

        user.last_login_at = datetime.now(UTC)
        return user

    # --- sessions ----------------------------------------------------------

    async def issue_session(self, user: User) -> str:
        """Create a session and return the token. It is never stored in clear."""
        token = secrets.token_urlsafe(32)
        self.session.add(
            UserSession(
                user_id=user.id,
                token_hash=_hash_token(token),
                expires_at=datetime.now(UTC) + SESSION_TTL,
            )
        )
        await self.session.flush()
        return token

    async def user_for_token(self, token: str | None) -> User | None:
        """Resolve a token to its user, or None if it is not usable."""
        if not token:
            return None

        row = (
            await self.session.scalars(
                select(UserSession).where(UserSession.token_hash == _hash_token(token))
            )
        ).first()
        if row is None or row.revoked_at is not None:
            return None
        if _aware(row.expires_at) <= datetime.now(UTC):
            return None

        user = await self.session.get(User, row.user_id)
        if user is None or not user.is_active:
            return None

        row.last_used_at = datetime.now(UTC)
        return user

    async def revoke(self, token: str | None) -> None:
        if not token:
            return
        row = (
            await self.session.scalars(
                select(UserSession).where(UserSession.token_hash == _hash_token(token))
            )
        ).first()
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)

    async def revoke_all(self, user: User) -> int:
        """Sign out everywhere. The reason sessions are rows rather than tokens."""
        rows = (
            await self.session.scalars(
                select(UserSession)
                .where(UserSession.user_id == user.id)
                .where(UserSession.revoked_at.is_(None))
            )
        ).all()
        now = datetime.now(UTC)
        for row in rows:
            row.revoked_at = now
        return len(rows)


def _display_name(first: str | None, last: str | None) -> str:
    return " ".join(part.strip() for part in (first, last) if part and part.strip())


def _aware(value: datetime) -> datetime:
    """Postgres hands back an aware datetime; SQLite in tests does not."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
