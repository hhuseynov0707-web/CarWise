"""Request and response shapes for accounts.

The response never carries the password hash, the session token or anything
else the caller did not send. It is easier to keep that true by listing the
fields than by excluding them from an ORM dump.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: Long enough that the key-derivation function is doing the work rather
#: than the length. Short minimums push people toward passwords a wordlist
#: already contains.
MIN_PASSWORD_LENGTH = 10

#: The earliest birth year the form will accept. Anything older is a typo.
MIN_BIRTH_YEAR = 1920


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    first_name: str | None = Field(default=None, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)
    birth_year: int | None = Field(default=None, ge=MIN_BIRTH_YEAR, le=2100)
    locale: str = Field(default="az", max_length=8)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class ProfileUpdateRequest(BaseModel):
    """Every field optional: this is a patch, not a replacement.

    ``None`` means "leave alone" rather than "clear", which is why there is no
    way to unset a name here. Clearing a field is a different intention and
    deserves a different request than a form that happened to omit it.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)
    birth_year: int | None = Field(default=None, ge=MIN_BIRTH_YEAR, le=2100)
    locale: str | None = Field(default=None, max_length=8)


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    email: str
    first_name: str | None
    last_name: str | None
    display_name: str | None
    birth_year: int | None
    locale: str
    plan: str
