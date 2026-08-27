"""PostgreSQL schema (spec §10).

Two decisions shape this schema more than any other.

**Identity keys are denormalized onto listings.** Every listing row carries its
``config_id``, ``powertrain_key``, ``generation_key`` and ``model_key``. That
looks redundant, and it is — deliberately. The comparable engine widens down
that ladder (audit §6), and without indexed columns at each rung, widening means
a table scan on every analysis. This is the single most performance-critical
access path in the product.

**Listing history is a separate table, not a JSON column.** Days-on-market,
price-change counts and reduction behaviour (spec §8) are the raw material of
the proprietary dataset that spec §63 identifies as the actual moat. They need
to be queryable and aggregatable, not buried in a document.

Prices are stored twice: in their original currency, and normalized to AZN.
Roughly a third of listings in this market quote USD, and mixing currencies in
one comparable set produces a bimodal distribution and a meaningless median
(see ``app/domain/money.py``). The FX rate used is stored on the row so a
historical figure stays auditable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- Reference data --------------------------------------------------------


class MarketSource(Base, TimestampMixin):
    """A registered market data source (spec §6).

    Sources are rows rather than an enum so that adding a dealer feed or a
    partner integration is a configuration change, not a migration and a deploy.
    """

    __tablename__ = "market_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """Ships disabled. Ingestion from a public site requires a human sign-off on
    that site's terms first (audit §4)."""

    trust_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    """Relative confidence in this source's data quality, applied when a value
    from one source contradicts another."""

    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class VehicleConfigurationRow(Base, TimestampMixin):
    """A canonical vehicle configuration (spec §4).

    The primary key is the deterministic content hash from
    ``VehicleConfiguration.config_id``, not a sequence. The same real-world
    configuration therefore resolves to the same row from any ingestion run or
    service without coordination.
    """

    __tablename__ = "vehicle_configurations"

    config_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    model_key: Mapped[str] = mapped_column(String(128), nullable=False)
    generation_key: Mapped[str] = mapped_column(String(192), nullable=False)
    powertrain_key: Mapped[str] = mapped_column(String(384), nullable=False)
    canonical_string: Mapped[str] = mapped_column(String(512), nullable=False)

    make: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(96))
    model_year: Mapped[int | None] = mapped_column(Integer)
    generation: Mapped[str | None] = mapped_column(String(64))
    trim: Mapped[str | None] = mapped_column(String(96))
    engine_code: Mapped[str | None] = mapped_column(String(48))
    displacement_l: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    fuel: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    transmission: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    drivetrain: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    body: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    horsepower: Mapped[int | None] = mapped_column(Integer)
    import_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)

    specificity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    __table_args__ = (
        Index("ix_config_model_key", "model_key"),
        Index("ix_config_generation_key", "generation_key"),
        Index("ix_config_powertrain_key", "powertrain_key"),
        Index("ix_config_make_model_year", "make", "model", "model_year"),
        CheckConstraint(
            "model_year IS NULL OR (model_year BETWEEN 1900 AND 2100)",
            name="ck_config_model_year_range",
        ),
    )


# --- Market observations ---------------------------------------------------


class Listing(Base, TimestampMixin):
    """A vehicle listing observed on a market source."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("market_sources.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    """The source's own identifier. Combined with source_id this is what makes
    re-ingestion idempotent."""

    source_url: Mapped[str | None] = mapped_column(String(1024))
    config_id: Mapped[str | None] = mapped_column(
        ForeignKey("vehicle_configurations.config_id", ondelete="SET NULL")
    )

    # Denormalized identity ladder — see module docstring.
    model_key: Mapped[str | None] = mapped_column(String(128))
    generation_key: Mapped[str | None] = mapped_column(String(192))
    powertrain_key: Mapped[str | None] = mapped_column(String(384))

    price_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_azn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fx_rate_used: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    fx_rate_source: Mapped[str | None] = mapped_column(String(64))

    mileage_km: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(64))
    seller_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    condition: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)

    has_damage_disclosure: Mapped[bool | None] = mapped_column(Boolean)
    """Tri-state. NULL means the listing does not say, which is different from
    the seller stating there is no damage."""

    has_repaint_disclosure: Mapped[bool | None] = mapped_column(Boolean)
    owner_count: Mapped[int | None] = mapped_column(Integer)

    description: Mapped[str | None] = mapped_column(Text)
    """Retained for analysis only — disclosure detection and risk signals. Not
    republished (audit §4.7)."""

    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    """Hash of the summary fields. Lets incremental ingestion skip re-fetching a
    detail page whose summary has not changed (audit §4.4)."""

    raw_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    """The normalized source fields as extracted, kept so a parser fix can be
    replayed against historical rows without re-crawling."""

    history: Mapped[list[ListingObservation]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        # The primary comparable-selection path: widen by identity rung,
        # filtered by observation window.
        Index("ix_listing_model_key_seen", "model_key", "last_seen_at"),
        Index("ix_listing_generation_key_seen", "generation_key", "last_seen_at"),
        Index("ix_listing_config_seen", "config_id", "last_seen_at"),
        Index("ix_listing_region_seen", "region", "last_seen_at"),
        Index("ix_listing_status_seen", "status", "last_seen_at"),
        CheckConstraint("price_azn >= 0", name="ck_listing_price_non_negative"),
        CheckConstraint(
            "mileage_km IS NULL OR mileage_km >= 0", name="ck_listing_mileage_non_negative"
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="ck_listing_seen_order"),
    )


class ListingObservation(Base):
    """One point-in-time observation of a listing (spec §8).

    Written only when something actually changed, so the table records
    transitions rather than a row per crawl. That keeps it small enough to
    aggregate over and makes price-change counting a simple row count.
    """

    __tablename__ = "listing_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    price_azn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    change_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    """``FIRST_SEEN``, ``PRICE_CHANGE``, ``MILEAGE_CHANGE``, ``STATUS_CHANGE``,
    ``DESCRIPTION_CHANGE``."""

    listing: Mapped[Listing] = relationship(back_populates="history")

    __table_args__ = (
        Index("ix_observation_listing_time", "listing_id", "observed_at"),
        Index("ix_observation_kind_time", "change_kind", "observed_at"),
    )


class TransactionObservationRow(Base, TimestampMixin):
    """A reported settled sale price (spec §9).

    Scarce and disproportionately valuable: once a configuration accumulates
    enough of these, its valuation can move from an asking basis to a
    transaction basis. ``listed_price_azn`` is what makes the asking-to-settled
    gap measurable per segment instead of assumed.
    """

    __tablename__ = "transaction_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    config_id: Mapped[str | None] = mapped_column(
        ForeignKey("vehicle_configurations.config_id", ondelete="SET NULL")
    )
    model_key: Mapped[str | None] = mapped_column(String(128))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))

    price_azn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    listed_price_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    mileage_km: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    city: Mapped[str | None] = mapped_column(String(64))

    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_transaction_config_date", "config_id", "transaction_date"),
        Index("ix_transaction_model_date", "model_key", "transaction_date"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_transaction_confidence"),
        CheckConstraint("price_azn > 0", name="ck_transaction_price_positive"),
    )


class MarketSnapshot(Base):
    """Periodic aggregate statistics per configuration and region (spec §10, §36).

    Computed over listings *observed within* the window rather than currently
    active ones, to counter the survivorship bias described in audit §7.5:
    cars that sell quickly leave the active set quickly, which biases a
    snapshot of live listings upward.
    """

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    config_id: Mapped[str | None] = mapped_column(String(32))
    model_key: Mapped[str | None] = mapped_column(String(128))
    generation_key: Mapped[str | None] = mapped_column(String(192))
    region: Mapped[str] = mapped_column(String(64), default="ALL", nullable=False)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)

    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(16), default="ASKING", nullable=False)

    median_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    mean_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p10_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p25_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p75_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p90_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    dispersion: Mapped[float | None] = mapped_column(Float)

    median_mileage_km: Mapped[int | None] = mapped_column(Integer)
    median_days_on_market: Mapped[float | None] = mapped_column(Float)
    new_listings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    removed_listings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_reductions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    median_reduction_pct: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "config_id", "region", "snapshot_date", "window_days", name="uq_snapshot_scope"
        ),
        Index("ix_snapshot_config_date", "config_id", "snapshot_date"),
        Index("ix_snapshot_model_date", "model_key", "snapshot_date"),
    )


# --- Analysis outputs ------------------------------------------------------


class AnalysisRecord(Base, TimestampMixin):
    """A stored analysis result (spec §10, §40).

    The evidence bundle is stored whole. Market data moves, so re-running an
    analysis six months later will not reproduce today's numbers — and a saved
    report that silently changes its figures would be worse than no saved report
    at all. Storing the bundle makes a report immutable and auditable.
    """

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    config_id: Mapped[str | None] = mapped_column(String(32))
    vin: Mapped[str | None] = mapped_column(String(32))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))

    asking_price_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    central_estimate_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fair_market_low_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fair_market_high_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_basis: Mapped[str] = mapped_column(String(16), default="ASKING", nullable=False)

    rating: Mapped[str] = mapped_column(String(24), nullable=False)
    price_difference_pct: Mapped[float | None] = mapped_column(Float)
    price_percentile: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comparable_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    evidence_bundle: Mapped[dict] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[dict | None] = mapped_column(JSONB)
    narrative_source: Mapped[str] = mapped_column(String(24), default="fallback", nullable=False)
    """Which layer produced the prose. Surfaced in the UI so an AI-written
    narrative is labelled as such."""

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_analysis_user_created", "user_id", "created_at"),
        Index("ix_analysis_config", "config_id"),
        CheckConstraint("risk_score BETWEEN 0 AND 100", name="ck_analysis_risk_range"),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_analysis_confidence_range"),
    )


class RiskObservation(Base):
    """A risk signal recorded against a vehicle or listing (spec §10, §21).

    Persisted separately from the analysis bundle so signal frequency can be
    analysed across the market — which detectors fire most, whether a detector
    is too noisy, and how prevalence shifts over time.
    """

    __tablename__ = "risk_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(String(32))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    config_id: Mapped[str | None] = mapped_column(String(32))

    risk_type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_risk_type_observed", "risk_type", "observed_at"),
        Index("ix_risk_listing", "listing_id"),
    )


class DataQualityObservation(Base):
    """A detected data-quality anomaly (spec §45).

    Stored as structured rows rather than log lines so that quality becomes a
    measurable trend rather than something noticed when a user complains.
    """

    __tablename__ = "data_quality_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("market_sources.id", ondelete="CASCADE"))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE")
    )

    issue: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(64))
    raw_value: Mapped[str | None] = mapped_column(String(512))
    severity: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_quality_issue_time", "issue", "observed_at"),
        Index("ix_quality_field", "field_name"),
    )


class IngestionRun(Base):
    """One execution of an ingestion pipeline (spec §44).

    ``field_extraction_rates`` is the important column. A markup change on the
    source degrades extraction silently — HTTP 200, rows written, mileage now
    NULL on 90% of them. Alerting on per-field extraction rates catches that;
    alerting on error counts does not (audit §3).
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("market_sources.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="RUNNING", nullable=False)

    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    listings_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    listings_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    listings_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    listings_removed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_changes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_made: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    robots_denied: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    field_extraction_rates: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    unmapped_tokens: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    """Vocabulary the normalization tables did not recognize, per field. A rising
    rate means the market's language drifted past our synonym tables."""

    error_detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_run_source_started", "source_id", "started_at"),)


class FxRateRow(Base):
    """Recorded exchange rates.

    Stored per day and per pair so that a price normalized months ago can be
    reconstructed with the rate that was actually applied.
    """

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("base_currency", "quote_currency", "as_of", name="uq_fx_pair_date"),
        Index("ix_fx_pair_date", "base_currency", "quote_currency", "as_of"),
        CheckConstraint("rate > 0", name="ck_fx_rate_positive"),
    )


# --- Users and saved state -------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(128))
    locale: Mapped[str] = mapped_column(String(8), default="az", nullable=False)
    plan: Mapped[str] = mapped_column(String(24), default="free", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_user_email", "email"),)


class SavedVehicle(Base, TimestampMixin):
    """A vehicle a user is tracking (spec §40, §41)."""

    __tablename__ = "saved_vehicles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    analysis_id: Mapped[str | None] = mapped_column(String(32))
    config_id: Mapped[str | None] = mapped_column(String(32))

    label: Mapped[str | None] = mapped_column(String(128))
    target_price_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    notify_on_price_drop: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_on_removal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_price_azn: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", name="uq_saved_user_listing"),
        Index("ix_saved_user", "user_id"),
    )


class AlertEvent(Base):
    """A fired alert (spec §41)."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    saved_vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("saved_vehicles.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_alert_saved_created", "saved_vehicle_id", "created_at"),)


class AuditLog(Base):
    """Security-relevant events (spec §57)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(128))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    """Hashed, never raw. An IP address is personal data and we have no use for
    the original value."""

    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_audit_action_created", "action", "created_at"),)
