"""Account endpoints.

The session token travels in an HttpOnly cookie rather than in the response
body. A token the page can read is a token an injected script can read, and
the whole point of the session is that it stands in for the password. The
cookie is SameSite=Lax, which is enough here because the browser treats
different ports on the same host as the same site, so the app and the API are
not cross-site to each other.

``Secure`` follows the environment: a cookie marked Secure is not sent over
plain HTTP, which would break local development, and one *not* marked Secure
in production is a cookie that can be sent in the clear.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.api.routes import get_container
from app.container import Container
from app.db.models import User
from app.schemas.auth import (
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    UserResponse,
)
from app.services.auth import AuthError, AuthService, EmailAlreadyRegistered, SESSION_TTL

router = APIRouter()

SESSION_COOKIE = "autointel_session"


def _set_session_cookie(response: Response, token: str, container: Container) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        # Secure everywhere except local development, where there is no TLS to
        # send it over. Keying this off "not production" would leave staging
        # sending the session in the clear.
        secure=container.settings.environment != "local",
        path="/",
    )


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        display_name=user.display_name,
        birth_year=user.birth_year,
        locale=user.locale,
        plan=user.plan,
    )


async def require_user(
    request: Request,
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> UserResponse:
    """Resolve the signed-in user, or refuse.

    Returns the response shape rather than the ORM row: the row belongs to a
    session that closes when this dependency returns, and handing a detached
    instance to a route is how lazy-load errors appear in production only.
    """
    container = get_container(request)
    async with container.database.session() as session:
        user = await AuthService(session=session).user_for_token(autointel_session)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in to continue.",
                headers={"WWW-Authenticate": "Cookie"},
            )
        return _to_response(user)


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
async def register(
    payload: RegisterRequest,
    response: Response,
    container: Container = Depends(get_container),
) -> UserResponse:
    async with container.database.session() as session:
        service = AuthService(session=session)
        try:
            user = await service.register(
                email=payload.email,
                password=payload.password,
                first_name=payload.first_name,
                last_name=payload.last_name,
                birth_year=payload.birth_year,
                locale=payload.locale,
            )
        except EmailAlreadyRegistered as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except AuthError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        token = await service.issue_session(user)
        body = _to_response(user)

    _set_session_cookie(response, token, container)
    return body


@router.post("/auth/login", response_model=UserResponse, tags=["auth"])
async def login(
    payload: LoginRequest,
    response: Response,
    container: Container = Depends(get_container),
) -> UserResponse:
    async with container.database.session() as session:
        service = AuthService(session=session)
        try:
            user = await service.authenticate(email=payload.email, password=payload.password)
        except AuthError as exc:
            # 401 with one message for both halves. Distinguishing them would
            # turn this route into an address-enumeration oracle.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc

        token = await service.issue_session(user)
        body = _to_response(user)

    _set_session_cookie(response, token, container)
    return body


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def logout(
    response: Response,
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    async with container.database.session() as session:
        await AuthService(session=session).revoke(autointel_session)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me", response_model=UserResponse, tags=["auth"])
async def me(user: UserResponse = Depends(require_user)) -> UserResponse:
    return user


@router.patch("/auth/me", response_model=UserResponse, tags=["auth"])
async def update_me(
    payload: ProfileUpdateRequest,
    request: Request,
    container: Container = Depends(get_container),
    autointel_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> UserResponse:
    async with container.database.session() as session:
        service = AuthService(session=session)
        user = await service.user_for_token(autointel_session)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
            )

        if payload.first_name is not None:
            user.first_name = payload.first_name.strip() or None
        if payload.last_name is not None:
            user.last_name = payload.last_name.strip() or None
        if payload.birth_year is not None:
            user.birth_year = payload.birth_year
        if payload.locale is not None:
            user.locale = payload.locale

        parts = [p for p in (user.first_name, user.last_name) if p]
        user.display_name = " ".join(parts) or None

        return _to_response(user)
