"""Request and response shapes for saved vehicles."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

#: A label is the user's own note, not an identifier. Long enough for
#: "2019 BMW 530 xDrive — the silver one in Xırdalan".
MAX_LABEL_LENGTH = 128


class SaveVehicleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(min_length=1, max_length=32)
    analysis_id: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)
    target_price_azn: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    notify_on_price_drop: bool = True
    notify_on_removal: bool = True


class SavedVehicleUpdate(BaseModel):
    """A patch. Omitted fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)
    target_price_azn: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    notify_on_price_drop: bool | None = None
    notify_on_removal: bool | None = None


class SavedVehicleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    config_id: str | None
    analysis_id: str | None
    label: str | None
    target_price_azn: Decimal | None
    last_seen_price_azn: Decimal | None
    notify_on_price_drop: bool
    notify_on_removal: bool
    created_at: datetime
