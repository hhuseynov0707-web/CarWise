"""Data provenance (spec §5).

Every meaningful vehicle attribute carries where it came from, when, how
strongly it is verified, and how confident that source is. This is the product's
central trust mechanism: it is what lets the report say "the seller states 95k
km; the VIN decoder confirms the drivetrain; nobody has verified the odometer"
instead of presenting one undifferentiated blob of facts.

A second, less obvious payoff: when two sources disagree about the same
attribute, that disagreement is itself evidence. :class:`ProvenanceLedger`
surfaces conflicts, and the risk engine consumes them directly as
``INFORMATION_INCONSISTENCY`` signals (spec §21).

This module is pure. It takes no clock — callers pass ``as_of`` explicitly — so
resolution is deterministic and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from app.domain.enums import SourceType, VerificationStatus, verification_weight

T = TypeVar("T")

# Values decay toward half their original weight over this period. Applied only
# to sources that genuinely go stale (market observations); factory
# specifications and VIN-derived facts do not decay.
_DEFAULT_HALF_LIFE = timedelta(days=180)

_DECAYING_SOURCES = frozenset(
    {
        SourceType.MARKET_LISTING,
        SourceType.DEALER,
        SourceType.WEB_RESEARCH,
    }
)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Origin metadata for a single attribute value."""

    source: str
    """Human-readable origin, e.g. ``"turbo.az"``, ``"NHTSA vPIC"``, ``"user"``."""

    source_type: SourceType
    timestamp: datetime
    confidence: float
    """Source-reported reliability in ``[0, 1]``."""

    verification_status: VerificationStatus
    detail: str | None = None
    """Optional specifics: decoder version, listing URL, OCR region, etc."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    def weight(self, as_of: datetime, half_life: timedelta = _DEFAULT_HALF_LIFE) -> float:
        """Effective trust weight of this value at a point in time.

        Combines verification tier, source confidence, and — for sources that
        actually go stale — exponential time decay.
        """
        base = verification_weight(self.verification_status) * self.confidence
        if self.source_type not in _DECAYING_SOURCES:
            return base
        age = as_of - self.timestamp
        if age <= timedelta(0):
            return base
        halvings = age / half_life
        return base * (0.5**halvings)


@dataclass(frozen=True, slots=True)
class Attributed(Generic[T]):
    """A value bound to its provenance."""

    value: T
    provenance: Provenance

    @property
    def is_verified(self) -> bool:
        return self.provenance.verification_status is VerificationStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class AttributeConflict:
    """Two sources disagreeing about one attribute.

    Consumed by the risk engine. A VIN decoder saying "AWD" while the seller
    writes "front-wheel drive" is a genuine, checkable discrepancy, and the
    report should raise it rather than silently picking a winner.
    """

    field_name: str
    accepted: Attributed[object]
    rejected: Attributed[object]
    accepted_weight: float
    rejected_weight: float

    @property
    def is_material(self) -> bool:
        """Whether the losing value had enough standing to be worth reporting.

        A user guess overridden by a factory specification is not interesting.
        Two comparably-trusted sources disagreeing is.
        """
        if self.accepted_weight <= 0:
            return False
        return self.rejected_weight / self.accepted_weight >= 0.5


@dataclass(frozen=True, slots=True)
class Resolution(Generic[T]):
    """Outcome of resolving competing values for one attribute."""

    field_name: str
    accepted: Attributed[T] | None
    conflicts: tuple[AttributeConflict, ...] = ()
    candidate_count: int = 0

    @property
    def value(self) -> T | None:
        return self.accepted.value if self.accepted else None


@dataclass
class ProvenanceLedger:
    """Accumulates candidate values per attribute and resolves them.

    Sources contribute freely and in any order; the ledger decides which value
    wins and keeps the losers as evidence rather than discarding them.

        >>> ledger = ProvenanceLedger()
        >>> ledger.record("drivetrain", "AWD", vin_provenance)
        >>> ledger.record("drivetrain", "FWD", seller_provenance)
        >>> resolution = ledger.resolve("drivetrain", as_of=now)
        >>> resolution.value
        'AWD'
        >>> [c.field_name for c in resolution.conflicts]
        ['drivetrain']
    """

    _candidates: dict[str, list[Attributed[object]]] = field(default_factory=dict)

    def record(self, field_name: str, value: object, provenance: Provenance) -> None:
        """Add a candidate value for an attribute. ``None`` values are ignored."""
        if value is None:
            return
        self._candidates.setdefault(field_name, []).append(Attributed(value, provenance))

    def record_attributed(self, field_name: str, attributed: Attributed[object]) -> None:
        if attributed.value is None:
            return
        self._candidates.setdefault(field_name, []).append(attributed)

    def fields(self) -> list[str]:
        return sorted(self._candidates)

    def candidates(self, field_name: str) -> tuple[Attributed[object], ...]:
        return tuple(self._candidates.get(field_name, ()))

    def resolve(self, field_name: str, as_of: datetime) -> Resolution[object]:
        """Pick the best-supported value for one attribute.

        Values that are equal are merged rather than treated as conflicting —
        two sources agreeing is corroboration, and the higher-weighted one is
        kept as the citation.
        """
        candidates = self._candidates.get(field_name, [])
        if not candidates:
            return Resolution(field_name=field_name, accepted=None, candidate_count=0)

        ranked = sorted(
            candidates,
            key=lambda a: (a.provenance.weight(as_of), a.provenance.timestamp),
            reverse=True,
        )
        winner = ranked[0]
        winner_weight = winner.provenance.weight(as_of)

        conflicts: list[AttributeConflict] = []
        for other in ranked[1:]:
            if _values_agree(other.value, winner.value):
                continue
            conflict = AttributeConflict(
                field_name=field_name,
                accepted=winner,
                rejected=other,
                accepted_weight=winner_weight,
                rejected_weight=other.provenance.weight(as_of),
            )
            if conflict.is_material:
                conflicts.append(conflict)

        return Resolution(
            field_name=field_name,
            accepted=winner,
            conflicts=tuple(conflicts),
            candidate_count=len(candidates),
        )

    def resolve_all(self, as_of: datetime) -> dict[str, Resolution[object]]:
        return {name: self.resolve(name, as_of) for name in self._candidates}

    def all_conflicts(self, as_of: datetime) -> list[AttributeConflict]:
        """Every material disagreement across all attributes."""
        out: list[AttributeConflict] = []
        for resolution in self.resolve_all(as_of).values():
            out.extend(resolution.conflicts)
        return out

    def completeness(self, expected_fields: tuple[str, ...]) -> float:
        """Share of expected attributes that have at least one candidate value.

        Feeds the confidence engine (spec §48): a valuation built on a
        half-specified vehicle should not report the same confidence as one
        built on a fully-specified vehicle.
        """
        if not expected_fields:
            return 0.0
        present = sum(1 for name in expected_fields if self._candidates.get(name))
        return present / len(expected_fields)

    def verified_share(self, as_of: datetime) -> float:
        """Share of resolved attributes whose accepted value is VERIFIED."""
        resolutions = self.resolve_all(as_of)
        if not resolutions:
            return 0.0
        verified = sum(
            1 for r in resolutions.values() if r.accepted is not None and r.accepted.is_verified
        )
        return verified / len(resolutions)


def _values_agree(left: object, right: object) -> bool:
    """Loose equality: case-insensitive for text, tolerant for numbers."""
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().casefold() == right.strip().casefold()
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, bool) or isinstance(right, bool):
            return left is right
        scale = max(abs(left), abs(right), 1.0)
        return abs(left - right) / scale < 0.01
    return left == right


def user_provenance(as_of: datetime, detail: str | None = None) -> Provenance:
    """Provenance for a value the user typed in themselves."""
    return Provenance(
        source="user",
        source_type=SourceType.USER,
        timestamp=as_of,
        confidence=0.9,
        verification_status=VerificationStatus.USER_PROVIDED,
        detail=detail,
    )


def computed_provenance(as_of: datetime, detail: str) -> Provenance:
    """Provenance for a value our own engines derived from market data."""
    return Provenance(
        source="autointel",
        source_type=SourceType.COMPUTED,
        timestamp=as_of,
        confidence=1.0,
        verification_status=VerificationStatus.MARKET_DERIVED,
        detail=detail,
    )
