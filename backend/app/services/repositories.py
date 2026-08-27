"""Repository providers.

The analysis service needs a repository scoped to a unit of work. Where that
repository comes from is a deployment decision, so it is expressed as a port and
resolved in the composition root.

The practical payoff is that the API can be exercised end-to-end against an
in-memory market — no database, no container, no fixtures beyond a list of
listings. A transport layer that can only be tested against live PostgreSQL
tends, in practice, not to be tested at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from app.db.repository import SqlMarketRepository
from app.db.session import Database
from app.domain.market import MarketListing, TransactionObservation
from app.services.analysis import InMemoryMarketRepository, MarketRepository


class RepositoryProvider(Protocol):
    """Supplies a market repository for the duration of one operation."""

    def scope(self) -> AbstractAsyncContextManager[MarketRepository]:
        ...


class SqlRepositoryProvider:
    """Opens a read-only database session per operation."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[MarketRepository]:
        async with self._database.read_session() as session:
            yield SqlMarketRepository(session)


class StaticRepositoryProvider:
    """Serves a fixed in-memory market.

    For tests and for offline demonstration. Never wired into a deployment that
    serves real users — synthetic listings presented as market data would be
    exactly the fabrication the product exists to avoid.
    """

    def __init__(
        self,
        listings: Sequence[MarketListing] = (),
        transactions: Sequence[TransactionObservation] = (),
    ) -> None:
        self._repository = InMemoryMarketRepository(listings, transactions)

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[MarketRepository]:
        yield self._repository
