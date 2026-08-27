"""Composition root.

The one module permitted to name concrete implementations. Everything else
depends on ports, which is what makes the LLM provider, the market source and
the storage layer swappable without touching business logic (spec §72). The
architecture test enforces that this stays true.

Wiring decisions live here so that "what is this deployment actually running?"
is answered by reading one file.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.llm.base import LLMProvider, LLMUnavailable
from app.adapters.market.base import AdapterRegistry
from app.adapters.llm.grok import GrokProvider
from app.adapters.llm.service import ReasoningService
from app.config import Settings
from app.db.session import Database
from app.domain.money import FxTable
from app.engines.comparables.engine import SelectionPolicy
from app.services.repositories import RepositoryProvider, SqlRepositoryProvider


@dataclass
class Container:
    """Application-wide singletons, built once at startup."""

    settings: Settings
    database: Database
    reasoning: ReasoningService
    selection_policy: SelectionPolicy
    repositories: RepositoryProvider
    """Where market data comes from. Swapped in tests for an in-memory
    market, which is what lets the HTTP layer be tested without PostgreSQL."""

    market_sources: AdapterRegistry
    """Registered ingestion adapters, resolved by slug rather than imported."""

    fx_table: FxTable
    """Exchange rates for the current batch. Empty until a rate source is
    wired; ingestion then skips non-AZN listings rather than converting them
    at a guessed rate."""

    @classmethod
    def build(cls, settings: Settings) -> Container:
        database = Database(
            url=settings.database_url,
            echo=settings.database_echo,
            pool_size=settings.database_pool_size,
        )
        return cls(
            settings=settings,
            database=database,
            reasoning=ReasoningService(
                provider=_build_llm_provider(settings),
                max_attempts=settings.reasoning_max_attempts,
                enabled=settings.reasoning_enabled,
            ),
            selection_policy=SelectionPolicy(
                target_sample=settings.comparable_target_sample,
                min_sample=settings.comparable_min_sample,
                observation_window_days=settings.observation_window_days,
            ),
            repositories=SqlRepositoryProvider(database),
            market_sources=_build_market_registry(settings),
            fx_table=FxTable(),
        )

    async def shutdown(self) -> None:
        await self.reasoning.close()
        await self.database.dispose()


def _build_market_registry(settings: Settings) -> AdapterRegistry:
    """Register the market adapters this deployment can run.

    Nothing is registered while ingestion is disabled, so a misconfigured
    deployment cannot crawl even if an operator calls the endpoint. The
    adapter itself refuses again on its own terms — two independent gates,
    because this is the action with real-world consequences outside our
    system (audit §4).
    """
    registry = AdapterRegistry()
    if not settings.ingestion_enabled:
        return registry

    from app.adapters.market.http import PoliteHttpClient
    from app.adapters.market.turbo import TurboAdapter

    client = PoliteHttpClient(
        user_agent=settings.crawl_user_agent,
        requests_per_second=settings.crawl_requests_per_second,
        burst=settings.crawl_burst,
        timeout_seconds=settings.crawl_timeout_seconds,
        robots_cache_seconds=settings.robots_cache_seconds,
    )
    registry.register(TurboAdapter(client=client))
    return registry


def _build_llm_provider(settings: Settings) -> LLMProvider | None:
    """Construct the reasoning provider, or ``None`` to run without one.

    A missing API key is a degraded mode, not a startup failure. The platform's
    numbers do not come from the model, so an unconfigured provider costs the
    narrative prose and nothing else — and refusing to boot over it would be a
    self-inflicted outage.
    """
    if not settings.reasoning_enabled:
        return None
    if not settings.grok_api_key:
        return None
    try:
        return GrokProvider(
            api_key=settings.grok_api_key,
            model=settings.grok_model,
            base_url=settings.grok_base_url,
            timeout_seconds=settings.grok_timeout_seconds,
        )
    except LLMUnavailable:
        return None
