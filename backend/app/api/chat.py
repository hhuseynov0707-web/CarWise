"""The expert conversation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, status

from app.adapters.llm.base import LLMError
from app.api.routes import get_container
from app.container import Container
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.auth import SESSION_COOKIE, AuthService
from app.services.chat import ChatService

router = APIRouter()


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    payload: ChatRequest,
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> ChatResponse:
    """Answer a question about the market.

    Signing in is not required — someone should be able to ask what to inspect
    without an account — but a signed-in user gets their name, their language
    and their familiarity with the product taken into account.

    There is no fallback here. A report can degrade to its computed narrative
    because the figures exist either way; a conversation has nothing to degrade
    to, so an unavailable model is reported as exactly that.
    """
    async with container.database.session() as session:
        user = await AuthService(session=session).user_for_token(autointel_session)
        language = payload.language or (user.locale if user else None) or "az"

        service = ChatService(session=session, provider=container.reasoning.provider)
        try:
            reply, grounded = await service.reply(
                messages=[m.model_dump() for m in payload.messages],
                user=user,
                language=language,
                config_id=payload.config_id,
                listing_id=payload.listing_id,
            )
        except LLMError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"The adviser is unavailable right now ({exc}).",
            ) from exc

    return ChatResponse(reply=reply, grounded_in=grounded)
