"""Database engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """Owns the async engine and session factory.

    Held on the application container rather than as a module-level global so
    tests can build an isolated instance per case without monkey-patching.
    """

    def __init__(
        self,
        url: str,
        echo: bool = False,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_recycle_seconds: int = 1800,
    ) -> None:
        self._url = url
        self._echo = echo
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_recycle_seconds = pool_recycle_seconds
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        """The engine, created on first use.

        Deferred rather than built in ``__init__`` because SQLAlchemy imports the
        database driver eagerly when an engine is constructed. Creating the
        engine lazily means a process that never touches the database — a test
        of the HTTP layer, a worker running only in-memory work — does not need
        the driver installed at all.
        """
        if self._engine is None:
            self._engine = create_async_engine(
                self._url,
                echo=self._echo,
                pool_size=self._pool_size,
                max_overflow=self._max_overflow,
                pool_pre_ping=True,
                pool_recycle=self._pool_recycle_seconds,
            )
            self._sessionmaker = async_sessionmaker(
                self._engine, expire_on_commit=False, class_=AsyncSession
            )
        return self._engine

    def _factory(self) -> async_sessionmaker[AsyncSession]:
        self.engine  # noqa: B018 - triggers lazy construction
        assert self._sessionmaker is not None
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session that commits on success and rolls back on any exception."""
        async with self._factory()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def read_session(self) -> AsyncIterator[AsyncSession]:
        """A session for reads. Never commits, so a read path cannot write."""
        async with self._factory()() as session:
            yield session

    async def dispose(self) -> None:
        """Close the pool if one was ever opened."""
        if self._engine is not None:
            await self._engine.dispose()
