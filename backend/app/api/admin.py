"""Operator endpoints (spec §44, §51).

These trigger ingestion, rebuild market snapshots, and expose the market
intelligence dashboard. They are the endpoints the n8n workflows call.

**Authentication is mandatory and fails closed.** With no ``ADMIN_API_KEY``
configured, every route here returns 503 rather than running unauthenticated.
An operator endpoint that ships open because someone forgot to set a variable is
a worse outcome than one that refuses to work until it is configured — the
second gets noticed on the first call, the first gets noticed by whoever finds
it.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.container import Container
from app.db.models import (
    DataQualityObservation,
    IngestionRun,
    Listing,
    ListingObservation,
    MarketSnapshot,
    VehicleConfigurationRow,
)
from app.domain.enums import ListingStatus

router = APIRouter(prefix="/admin", tags=["admin"])


def _container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - misconfiguration only
        raise HTTPException(status_code=500, detail="application container is not initialised")
    return container


async def require_admin(
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> Container:
    """Fail-closed admin authentication.

    Compared with :func:`hmac.compare_digest` so that a wrong key takes the same
    time as a right one; a plain ``==`` on a secret leaks its prefix to anyone
    patient enough to measure.
    """
    container = _container(request)
    configured = container.settings.admin_api_key

    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Operator endpoints are disabled because ADMIN_API_KEY is not set. "
                "Set it to a random 32+ character value to enable them."
            ),
        )

    if not x_admin_key or not hmac.compare_digest(x_admin_key, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-Admin-Key header is required.",
        )

    return container


# --- request / response models --------------------------------------------


class IngestionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="turbo.az", max_length=64)
    max_listings: int = Field(default=500, ge=1, le=10_000)


class IngestionRunResponse(BaseModel):
    status: str
    source: str
    listings_seen: int = 0
    listings_created: int = 0
    listings_updated: int = 0
    listings_removed: int = 0
    price_changes: int = 0
    errors: int = 0
    quality_issues: int = 0
    extraction_rates: dict[str, float] = {}
    degraded_fields: list[str] = []
    """Non-empty means the parser has lost touch with the source's markup. The
    n8n workflow alerts on this rather than on error counts (audit §3)."""

    unmapped_tokens: dict[str, list[str]] = {}
    abort_reason: str | None = None
    refused_reason: str | None = None


class SnapshotBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    windows: list[int] = Field(default=[30, 90, 180], max_length=6)


class SnapshotBuildResponse(BaseModel):
    scopes_considered: int
    snapshots_written: int
    skipped_thin: int


class MarketOverviewResponse(BaseModel):
    """Admin dashboard figures (spec §44)."""

    generated_at: datetime
    active_listings: int
    listings_seen_last_24h: int
    new_listings_last_24h: int
    price_changes_last_24h: int
    removed_last_24h: int
    distinct_configurations: int
    configurations_with_usable_sample: int
    """How many configurations have enough comparables to be valued at all.
    The single most important coverage number the platform has."""

    median_days_on_market: float | None
    data_quality_issues_last_7d: int
    last_ingestion: dict[str, object] | None


# --- routes ----------------------------------------------------------------


@router.post("/ingestion/run", response_model=IngestionRunResponse)
async def run_ingestion(
    payload: IngestionRunRequest,
    container: Container = Depends(require_admin),
) -> IngestionRunResponse:
    """Run one ingestion pass.

    Returns 200 with ``status="REFUSED"`` rather than an error status when the
    pipeline declines, because a refusal is a correct outcome the scheduler
    should record, not a failure it should retry.
    """
    from app.adapters.market.base import MarketSourceAdapter  # noqa: F401
    from app.services.ingestion import IngestionRefused, IngestionService

    adapter = container.market_sources.get_or_none(payload.source)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No market adapter is registered for {payload.source!r}.",
        )

    now = datetime.now(UTC)
    async with container.database.session() as session:
        service = IngestionService(
            session=session,
            fx=container.fx_table,
            ingestion_enabled=container.settings.ingestion_enabled,
        )
        try:
            report = await service.run(adapter, now, max_listings=payload.max_listings)
        except IngestionRefused as refusal:
            return IngestionRunResponse(
                status="REFUSED", source=payload.source, refused_reason=str(refusal)
            )

    return IngestionRunResponse(
        status=report.status,
        source=report.source,
        listings_seen=report.listings_seen,
        listings_created=report.listings_created,
        listings_updated=report.listings_updated,
        listings_removed=report.listings_removed,
        price_changes=report.price_changes,
        errors=report.errors,
        quality_issues=report.quality_issues,
        extraction_rates=report.health.rates(),
        degraded_fields=report.degraded_fields,
        unmapped_tokens=report.unmapped_summary(),
        abort_reason=report.abort_reason,
    )


@router.post("/snapshots/build", response_model=SnapshotBuildResponse)
async def build_snapshots(
    payload: SnapshotBuildRequest,
    container: Container = Depends(require_admin),
) -> SnapshotBuildResponse:
    from app.services.snapshots import SnapshotService

    now = datetime.now(UTC)
    async with container.database.session() as session:
        report = await SnapshotService(session).build(now, tuple(payload.windows))

    return SnapshotBuildResponse(
        scopes_considered=report.scopes_considered,
        snapshots_written=report.snapshots_written,
        skipped_thin=report.skipped_thin,
    )


@router.get("/market/overview", response_model=MarketOverviewResponse)
async def market_overview(
    container: Container = Depends(require_admin),
) -> MarketOverviewResponse:
    """Dashboard figures for operating the market data pipeline."""
    now = datetime.now(UTC)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    async with container.database.read_session() as session:
        active = await _scalar(
            session,
            select(func.count())
            .select_from(Listing)
            .where(Listing.status == ListingStatus.ACTIVE.value),
        )
        seen_24h = await _scalar(
            session,
            select(func.count()).select_from(Listing).where(Listing.last_seen_at >= day_ago),
        )
        new_24h = await _scalar(
            session,
            select(func.count()).select_from(Listing).where(Listing.first_seen_at >= day_ago),
        )
        price_changes = await _scalar(
            session,
            select(func.count())
            .select_from(ListingObservation)
            .where(ListingObservation.change_kind == "PRICE_CHANGE")
            .where(ListingObservation.observed_at >= day_ago),
        )
        removed_24h = await _scalar(
            session,
            select(func.count()).select_from(Listing).where(Listing.removed_at >= day_ago),
        )
        configurations = await _scalar(
            session, select(func.count()).select_from(VehicleConfigurationRow)
        )
        usable = await _scalar(
            session,
            select(func.count())
            .select_from(MarketSnapshot)
            .where(MarketSnapshot.sample_size >= container.settings.comparable_min_sample)
            .where(MarketSnapshot.region == "ALL"),
        )
        median_dom = await _scalar_float(
            session,
            select(func.percentile_cont(0.5).within_group(MarketSnapshot.median_days_on_market))
            .where(MarketSnapshot.median_days_on_market.is_not(None)),
        )
        quality = await _scalar(
            session,
            select(func.count())
            .select_from(DataQualityObservation)
            .where(DataQualityObservation.observed_at >= week_ago),
        )
        last_run = (
            await session.scalars(
                select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(1)
            )
        ).first()

    return MarketOverviewResponse(
        generated_at=now,
        active_listings=active,
        listings_seen_last_24h=seen_24h,
        new_listings_last_24h=new_24h,
        price_changes_last_24h=price_changes,
        removed_last_24h=removed_24h,
        distinct_configurations=configurations,
        configurations_with_usable_sample=usable,
        median_days_on_market=median_dom,
        data_quality_issues_last_7d=quality,
        last_ingestion=(
            {
                "started_at": last_run.started_at.isoformat(),
                "status": last_run.status,
                "listings_seen": last_run.listings_seen,
                "errors": last_run.errors,
                "extraction_rates": last_run.field_extraction_rates,
            }
            if last_run
            else None
        ),
    )


async def _scalar(session, statement) -> int:  # type: ignore[no-untyped-def]
    return int((await session.scalar(statement)) or 0)


async def _scalar_float(session, statement) -> float | None:  # type: ignore[no-untyped-def]
    value = await session.scalar(statement)
    return round(float(value), 1) if value is not None else None


Language = Literal["az", "en", "ru"]
