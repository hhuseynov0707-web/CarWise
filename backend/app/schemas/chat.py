"""Request and response shapes for the expert conversation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Enough for a real exchange without letting a client replay an unbounded
#: history at the model on every turn.
MAX_HISTORY = 20
MAX_MESSAGE_CHARS = 4000


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_HISTORY)

    config_id: str | None = Field(default=None, max_length=32)
    """A configuration to ground the conversation in. When set, the real
    market statistics for it are put in front of the model, which is the only
    way it is allowed to state a figure."""

    language: Literal["az", "ru", "en"] | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    is_ai_generated: bool = True
    grounded_in: str | None = None
    """The configuration whose statistics were supplied, if any."""

    unavailable_reason: str | None = None
