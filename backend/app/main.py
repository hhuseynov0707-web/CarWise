"""FastAPI application entry point."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.discover import router as discover_router
from app.api.finds import router as finds_router
from app.api.saved import router as saved_router
from app.api.routes import router
from app.config import Settings, get_settings
from app.container import Container

# On Windows the database driver cannot run on the default proactor loop, and
# uvicorn ignores the event loop policy — nothing this module could do at import
# time would help. The loop is chosen at launch instead:
#     uvicorn app.main:app --loop app.eventloop:loop_factory

#: Response headers applied to every response.
#: Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY and deprecated the old
#: spelling; the replacement does not exist in older versions. A literal
#: avoids depending on which side of that rename the installed version is on.
HTTP_422_UNPROCESSABLE = 422


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    container = Container.build(settings)
    app.state.container = container
    try:
        yield
    finally:
        await container.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Vehicle market intelligence for the Azerbaijani used-car market. "
            "Every figure returned by this API is computed from market data; none "
            "originates from a language model."
        ),
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept-Language"],
        max_age=600,
    )

    _install_middleware(app, settings)
    _install_error_handlers(app)
    app.include_router(router, prefix=settings.api_prefix)
    app.include_router(admin_router, prefix=settings.api_prefix)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(saved_router, prefix=settings.api_prefix)
    app.include_router(finds_router, prefix=settings.api_prefix)
    app.include_router(discover_router, prefix=settings.api_prefix)
    return app


def _install_middleware(app: FastAPI, settings: Settings) -> None:
    limiter = _SlidingWindowLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Attach a request id, enforce a rate limit, add security headers.

        The rate limiter here is in-process and therefore per-worker. It is a
        backstop against accidental request storms, not a defence against a
        determined attacker — that belongs at the edge, in front of every
        replica. Said plainly so nobody mistakes this for the real control.
        """
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        if request.url.path.startswith(settings.api_prefix) and not limiter.allow(
            _client_key(request)
        ):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limited",
                    "detail": "Too many requests. Please wait a moment and try again.",
                    "request_id": request_id,
                },
                headers={"Retry-After": "60"},
            )

        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{(time.monotonic() - started) * 1000:.0f}"
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """Surface domain validation failures as 422 rather than 500.

        The domain layer raises ``ValueError`` for genuinely invalid input — a
        negative price, a mixed-currency comparison. Those are the caller's
        problem to fix, and a 500 would hide that.
        """
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE,
            content={
                "error": "invalid_input",
                "detail": str(exc),
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Never leak an internal error message or stack trace to a client."""
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "detail": "An unexpected error occurred while processing the request.",
                "request_id": getattr(request.state, "request_id", None),
            },
        )


class _SlidingWindowLimiter:
    """Per-client sliding-window request counter."""

    def __init__(self, per_minute: int, window_seconds: float = 60.0) -> None:
        self._limit = per_minute
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        cutoff = now - self._window
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


def _client_key(request: Request) -> str:
    """Identify a client for rate limiting.

    Prefers an authenticated subject; falls back to the peer address. The
    ``X-Forwarded-For`` header is deliberately ignored — it is attacker-supplied
    unless a trusted proxy has rewritten it, and trusting it blindly turns the
    limiter into a no-op.
    """
    auth = request.headers.get("Authorization")
    if auth:
        return f"auth:{hash(auth) & 0xFFFFFFFF:x}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


app = create_app()
