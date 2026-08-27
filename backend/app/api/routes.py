"""HTTP routes.

Routes are thin by design (audit §8): parse, delegate, map, return. Any
computation appearing here would be untestable without spinning up the whole
transport stack, and would sit outside the pure-engine boundary that the rest of
the architecture depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.mappers import to_response
from app.container import Container
from app.domain.enums import Currency, ImportStatus
from app.domain.identity import VehicleConfiguration
from app.domain.market import SubjectVehicle
from app.domain.money import Money
from app.domain.normalization import (
    normalize_body,
    normalize_city,
    normalize_drivetrain,
    normalize_fuel,
    normalize_make,
    normalize_seller_type,
    normalize_transmission,
)
from app.domain.provenance import user_provenance
from app.schemas.analysis import (
    AnalysisResponse,
    HealthResponse,
    ManualAnalysisRequest,
    ManualVehicleInput,
    ReferenceDataResponse,
)
from app.services.analysis import AnalysisService

#: Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY and deprecated the old
#: spelling; the replacement does not exist in older versions. A literal
#: avoids depending on which side of that rename the installed version is on.
HTTP_422_UNPROCESSABLE = 422

router = APIRouter()


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - only on misconfiguration
        raise HTTPException(status_code=500, detail="application container is not initialised")
    return container


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(container: Container = Depends(get_container)) -> HealthResponse:
    """Liveness plus a truthful account of which subsystems are actually on."""
    settings = container.settings
    database_state = "unknown"
    try:
        async with container.database.read_session() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        database_state = "ok"
    except Exception:
        database_state = "unavailable"

    return HealthResponse(
        status="ok" if database_state == "ok" else "degraded",
        environment=settings.environment,
        database=database_state,
        reasoning="enabled" if settings.reasoning_configured else "disabled",
        ingestion="enabled" if settings.ingestion_enabled else "disabled",
    )


@router.get("/reference", response_model=ReferenceDataResponse, tags=["reference"])
async def reference_data() -> ReferenceDataResponse:
    """Vocabulary for UI dropdowns and client-side validation."""
    from app.domain.enums import BodyStyle, Drivetrain, FuelType, Transmission

    def values(enum_cls) -> list[str]:  # type: ignore[no-untyped-def]
        return [member.value for member in enum_cls if member.value != "UNKNOWN"]

    return ReferenceDataResponse(
        fuels=values(FuelType),
        transmissions=values(Transmission),
        drivetrains=values(Drivetrain),
        bodies=values(BodyStyle),
    )


@router.post(
    "/analysis/manual",
    response_model=AnalysisResponse,
    tags=["analysis"],
    summary="Analyse a manually described vehicle",
)
async def analyse_manual(
    payload: ManualAnalysisRequest,
    container: Container = Depends(get_container),
) -> AnalysisResponse:
    """Mode B of spec §3.

    Returns 422 when the make cannot be resolved. Guessing at an unrecognized
    make would produce a comparable set of one — its own misspelling — and an
    authoritative-looking valuation built on nothing.
    """
    if payload.vehicle.currency != "AZN" and payload.vehicle.asking_price is not None:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE,
            detail=(
                f"Prices in {payload.vehicle.currency} cannot be analysed yet: the "
                f"exchange-rate source is not wired up, and converting at a guessed "
                f"rate would corrupt the comparison. Please enter the price in AZN."
            ),
        )

    subject = build_subject(payload.vehicle)
    if not subject.configuration.is_resolvable:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE,
            detail=(
                f"'{payload.vehicle.make}' was not recognised as a vehicle make. "
                f"Check the spelling, or pick one from /reference."
            ),
        )

    now = datetime.now(UTC)
    async with container.repositories.scope() as repository:
        service = AnalysisService.build(
            repository, container.reasoning, container.selection_policy
        )
        analysis = await service.analyse(
            subject, now, language=payload.language, narrate=payload.include_narrative
        )

    return to_response(analysis.result, analysis.narrative)


@router.post(
    "/analysis/vin",
    response_model=AnalysisResponse,
    tags=["analysis"],
    summary="Analyse a vehicle by VIN",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def analyse_vin() -> AnalysisResponse:
    """Mode A of spec §3.

    Not implemented. VIN decoding needs a decoder integration that covers the
    European and Gulf-market vehicles common in Azerbaijan; the freely-available
    decoders cover the US market well and everything else poorly. Returning 501
    is correct until a decoder is chosen — a stub returning fabricated
    specifications would be far worse than an honest gap.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "VIN decoding is not yet available. Use /analysis/manual and supply the "
            "vehicle details directly."
        ),
    )


@router.post(
    "/analysis/listing",
    response_model=AnalysisResponse,
    tags=["analysis"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def analyse_listing() -> AnalysisResponse:
    """Mode C of spec §3.

    Not implemented. Fetching an arbitrary third-party listing on demand is
    exactly the access pattern that ingestion is careful to avoid, and it must
    not ship before the source-terms question in audit §4 is settled.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Listing-URL analysis is not yet available. Use /analysis/manual with the "
            "details from the listing."
        ),
    )


def build_subject(payload: ManualVehicleInput) -> SubjectVehicle:
    """Turn validated API input into a domain subject with provenance.

    Everything entered by hand is recorded as ``USER`` provenance, which is what
    lets the report distinguish "the seller says 95,000 km" from a figure
    anything has actually verified (spec §5).
    """
    now = datetime.now(UTC)
    configuration = VehicleConfiguration.from_raw(
        make=payload.make,
        model=payload.model,
        model_year=payload.model_year,
        generation=payload.generation,
        trim=payload.trim,
        engine_code=payload.engine_code,
        displacement=payload.displacement,
        fuel=payload.fuel,
        transmission=payload.transmission,
        drivetrain=payload.drivetrain,
        body=payload.body,
        horsepower=payload.horsepower,
        import_status=ImportStatus.UNKNOWN,
    )

    subject = SubjectVehicle(
        configuration=configuration,
        # Only AZN reaches here; the route rejects other currencies until an
        # FX source is wired, rather than converting at an assumed rate.
        asking_price=(
            Money.of(payload.asking_price, Currency.AZN)
            if payload.asking_price is not None
            else None
        ),
        mileage_km=payload.mileage_km,
        city=normalize_city(payload.city),
        seller_type=normalize_seller_type(payload.seller_type),
        vin=payload.vin,
        listing_url=payload.listing_url,
        has_damage_disclosure=payload.has_damage_disclosure,
        has_repaint_disclosure=payload.has_repaint_disclosure,
        service_records_provided=payload.service_records_provided,
        owner_count=payload.owner_count,
        description=payload.description,
    )

    provenance = user_provenance(now, detail="manual entry")
    for name, value in (
        ("make", normalize_make(payload.make)),
        ("model", configuration.model),
        ("model_year", payload.model_year),
        ("trim", payload.trim),
        ("fuel", normalize_fuel(payload.fuel).value),
        ("transmission", normalize_transmission(payload.transmission).value),
        ("drivetrain", normalize_drivetrain(payload.drivetrain).value),
        ("body", normalize_body(payload.body).value),
        ("mileage_km", payload.mileage_km),
        ("city", normalize_city(payload.city)),
    ):
        subject.ledger.record(name, value, provenance)

    return subject
