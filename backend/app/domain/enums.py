"""Canonical enumerations for the vehicle domain.

Every enum here carries an explicit ``UNKNOWN`` member where absence of
information is possible. Per the architecture audit (docs/00, §6), unknown is a
first-class value: it is never coerced to a default, because silently treating
"trim not stated" as "base trim" biases comparable selection downward in a way
that is invisible in the output.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class FuelType(StrEnum):
    PETROL = "PETROL"
    DIESEL = "DIESEL"
    HYBRID = "HYBRID"
    PLUGIN_HYBRID = "PLUGIN_HYBRID"
    ELECTRIC = "ELECTRIC"
    LPG = "LPG"
    CNG = "CNG"
    UNKNOWN = "UNKNOWN"


class Transmission(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    CVT = "CVT"
    DCT = "DCT"
    AMT = "AMT"
    UNKNOWN = "UNKNOWN"


class Drivetrain(StrEnum):
    FWD = "FWD"
    RWD = "RWD"
    AWD = "AWD"
    UNKNOWN = "UNKNOWN"


class BodyStyle(StrEnum):
    SEDAN = "SEDAN"
    HATCHBACK = "HATCHBACK"
    LIFTBACK = "LIFTBACK"
    WAGON = "WAGON"
    SUV = "SUV"
    CROSSOVER = "CROSSOVER"
    COUPE = "COUPE"
    CONVERTIBLE = "CONVERTIBLE"
    PICKUP = "PICKUP"
    MINIVAN = "MINIVAN"
    VAN = "VAN"
    UNKNOWN = "UNKNOWN"


class SellerType(StrEnum):
    PRIVATE = "PRIVATE"
    DEALER = "DEALER"
    IMPORTER = "IMPORTER"
    UNKNOWN = "UNKNOWN"


class ImportStatus(StrEnum):
    """Origin of the vehicle relative to the local market.

    Grey imports carry region-specific trims and equipment that materially
    change value, so this participates in identity, not just metadata.
    """

    LOCAL = "LOCAL"
    IMPORTED_US = "IMPORTED_US"
    IMPORTED_EU = "IMPORTED_EU"
    IMPORTED_UAE = "IMPORTED_UAE"
    IMPORTED_GE = "IMPORTED_GE"
    IMPORTED_RU = "IMPORTED_RU"
    IMPORTED_OTHER = "IMPORTED_OTHER"
    UNKNOWN = "UNKNOWN"


class ListingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"
    EXPIRED = "EXPIRED"
    SOLD_REPORTED = "SOLD_REPORTED"
    UNKNOWN = "UNKNOWN"


class Currency(StrEnum):
    AZN = "AZN"
    USD = "USD"
    EUR = "EUR"


class PriceBasis(StrEnum):
    """What kind of price a number represents.

    The distinction between an asking price and a settled transaction price is
    the deepest correctness issue in the product (spec §9, audit §2). This enum
    travels with every monetary result so a figure is always self-describing.
    """

    ASKING = "ASKING"
    TRANSACTION = "TRANSACTION"
    MIXED = "MIXED"


class SourceType(StrEnum):
    """Where a value physically came from."""

    VIN_DECODER = "VIN_DECODER"
    MANUFACTURER = "MANUFACTURER"
    PUBLIC_DATABASE = "PUBLIC_DATABASE"
    MARKET_LISTING = "MARKET_LISTING"
    USER = "USER"
    DEALER = "DEALER"
    HISTORY_PROVIDER = "HISTORY_PROVIDER"
    WEB_RESEARCH = "WEB_RESEARCH"
    OCR = "OCR"
    AI_INFERENCE = "AI_INFERENCE"
    COMPUTED = "COMPUTED"


class VerificationStatus(StrEnum):
    """How much weight a value has earned (spec §5).

    Ordering matters: :func:`weight` maps these to multipliers used when a
    single attribute has competing values from different sources.
    """

    VERIFIED = "VERIFIED"
    MARKET_DERIVED = "MARKET_DERIVED"
    USER_PROVIDED = "USER_PROVIDED"
    AI_INTERPRETED = "AI_INTERPRETED"
    UNVERIFIED = "UNVERIFIED"


class EvidenceStrength(StrEnum):
    """Quality tier of a knowledge/research claim (spec §22).

    Weak evidence is never promoted to established fact; the tier is carried
    through to the user-facing report.
    """

    STRONG = "STRONG"
    MEDIUM = "MEDIUM"
    WEAK = "WEAK"


class RiskSeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskType(StrEnum):
    """Detected risk-indicator categories (spec §21)."""

    MARKET_PRICE_ANOMALY = "MARKET_PRICE_ANOMALY"
    MILEAGE_ANOMALY = "MILEAGE_ANOMALY"
    MILEAGE_SEQUENCE_ANOMALY = "MILEAGE_SEQUENCE_ANOMALY"
    INFORMATION_INCONSISTENCY = "INFORMATION_INCONSISTENCY"
    HISTORY_INCOMPLETE = "HISTORY_INCOMPLETE"
    LISTING_BEHAVIOUR = "LISTING_BEHAVIOUR"
    DAMAGE_DISCLOSURE = "DAMAGE_DISCLOSURE"
    CONFIGURATION_ANOMALY = "CONFIGURATION_ANOMALY"
    UNVERIFIED_SELLER_CLAIM = "UNVERIFIED_SELLER_CLAIM"
    DATA_QUALITY = "DATA_QUALITY"
    MODEL_SPECIFIC_CONCERN = "MODEL_SPECIFIC_CONCERN"


class DealRating(StrEnum):
    """Transparent market-position categories (spec §17).

    ``INSUFFICIENT_DATA`` is a legitimate outcome and is returned whenever the
    valuation engine declines to produce a range.
    """

    GREAT_VALUE = "GREAT_VALUE"
    GOOD_VALUE = "GOOD_VALUE"
    FAIR_VALUE = "FAIR_VALUE"
    HIGH_PRICED = "HIGH_PRICED"
    OVERPRICED = "OVERPRICED"
    SUSPICIOUSLY_CHEAP = "SUSPICIOUSLY_CHEAP"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ValuationOutcome(StrEnum):
    """Whether the valuation engine was able to answer at all.

    ``INSUFFICIENT_DATA`` is a first-class return value, not an error. See
    audit §1: an empty or thin market is the default state of the system, not
    an exceptional one.
    """

    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ComparableTier(IntEnum):
    """Comparable-set membership tier (spec §11). Lower is closer."""

    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4


class AdjustmentReason(StrEnum):
    """Why a valuation adjustment produced the value it did.

    Critically, this lets an adjustment return exactly zero *with a stated
    reason* rather than a guessed number (audit §10.8).
    """

    APPLIED = "APPLIED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INPUT_UNKNOWN = "INPUT_UNKNOWN"
    NOT_MATERIAL = "NOT_MATERIAL"
    DISABLED = "DISABLED"


class ConditionGrade(StrEnum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    DAMAGED = "DAMAGED"
    UNKNOWN = "UNKNOWN"


# --- Ordered helpers -------------------------------------------------------

_VERIFICATION_WEIGHT: dict[VerificationStatus, float] = {
    VerificationStatus.VERIFIED: 1.0,
    VerificationStatus.MARKET_DERIVED: 0.8,
    VerificationStatus.USER_PROVIDED: 0.6,
    VerificationStatus.AI_INTERPRETED: 0.35,
    VerificationStatus.UNVERIFIED: 0.2,
}

_SEVERITY_ORDER: dict[RiskSeverity, int] = {
    RiskSeverity.INFO: 0,
    RiskSeverity.LOW: 1,
    RiskSeverity.MODERATE: 2,
    RiskSeverity.HIGH: 3,
    RiskSeverity.CRITICAL: 4,
}

_STRENGTH_WEIGHT: dict[EvidenceStrength, float] = {
    EvidenceStrength.STRONG: 1.0,
    EvidenceStrength.MEDIUM: 0.65,
    EvidenceStrength.WEAK: 0.3,
}


def verification_weight(status: VerificationStatus) -> float:
    """Relative trust multiplier for a value with this verification status."""
    return _VERIFICATION_WEIGHT[status]


def severity_rank(severity: RiskSeverity) -> int:
    """Sortable rank so risk signals can be ordered most-severe-first."""
    return _SEVERITY_ORDER[severity]


def evidence_weight(strength: EvidenceStrength) -> float:
    """Relative weight of a research claim by the quality of its sourcing."""
    return _STRENGTH_WEIGHT[strength]
