"""Vehicle identity resolution (spec §4).

Two vehicles are not comparable merely because make, model and year match. A
2019 BMW 530i xDrive and a 2019 BMW 530i are different cars with different
values; so are a locally-sold and a US-imported example of the same trim.

Identity here is a **ladder of keys**, not a single ID:

    config_id       make | model | generation | year | trim | powertrain | body
    powertrain_key  make | model | generation | fuel | displacement | gearbox | drive
    generation_key  make | model | generation
    model_key       make | model

The comparable engine walks down this ladder when the tightest key is too
sparse, and reports which rung it landed on. That makes widening a *visible
piece of evidence* in the report rather than a hidden fallback — the user is
told "we had to compare across model years to find 30 cars", which is exactly
the kind of thing that should not be silent.

``UNKNOWN`` is preserved distinctly at every level. Coercing "trim not stated"
into "base trim" would bias comparable sets downward invisibly (audit §6).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import IntEnum

from app.domain.enums import (
    BodyStyle,
    Drivetrain,
    FuelType,
    ImportStatus,
    Transmission,
)
from app.domain.normalization import (
    normalize_body,
    normalize_drivetrain,
    normalize_fuel,
    normalize_make,
    normalize_model,
    normalize_transmission,
)

MIN_MODEL_YEAR = 1900
MAX_MODEL_YEAR = 2100

_UNKNOWN = "?"
_SEP = "|"


class KeyLevel(IntEnum):
    """Rungs of the identity ladder, tightest first."""

    CONFIG = 0
    POWERTRAIN = 1
    GENERATION = 2
    MODEL = 3

    @property
    def label(self) -> str:
        return {
            KeyLevel.CONFIG: "exact configuration",
            KeyLevel.POWERTRAIN: "same powertrain",
            KeyLevel.GENERATION: "same generation",
            KeyLevel.MODEL: "same model",
        }[self]


def _token(value: object | None) -> str:
    """Render one identity component as a stable key token."""
    if value is None:
        return _UNKNOWN
    if isinstance(value, str):
        cleaned = value.strip().upper()
        return cleaned or _UNKNOWN
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value).upper()


def _is_known(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (FuelType, Transmission, Drivetrain, BodyStyle, ImportStatus)):
        return value.value != "UNKNOWN"
    return True


def normalize_displacement(raw: float | int | None) -> float | None:
    """Canonicalize engine displacement to litres, rounded to 0.1.

    Sources state displacement in litres (2.0), cubic centimetres (1998) or
    occasionally millilitres. Left unnormalized these fragment identity: a
    "1998" engine and a "2.0" engine would key differently despite being the
    same engine.
    """
    if raw is None:
        return None
    value = float(raw)
    if value <= 0:
        return None
    if value > 100:  # stated in cc
        value = value / 1000.0
    if value > 20:  # implausible for a passenger vehicle even in litres
        return None
    return round(value, 1)


@dataclass(frozen=True, slots=True)
class VehicleConfiguration:
    """The canonical structure a vehicle is identified by.

    Horsepower is intentionally **not** part of any key. It is quoted
    differently by different sources (DIN vs SAE, gross vs net) and including
    it would fragment identity for a single real configuration. It is carried
    as an attribute for display and valuation features only.
    """

    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    generation: str | None = None
    trim: str | None = None
    engine_code: str | None = None
    displacement_l: float | None = None
    fuel: FuelType = FuelType.UNKNOWN
    transmission: Transmission = Transmission.UNKNOWN
    drivetrain: Drivetrain = Drivetrain.UNKNOWN
    body: BodyStyle = BodyStyle.UNKNOWN
    horsepower: int | None = None
    import_status: ImportStatus = ImportStatus.UNKNOWN

    def __post_init__(self) -> None:
        if self.model_year is not None and not (
            MIN_MODEL_YEAR <= self.model_year <= MAX_MODEL_YEAR
        ):
            raise ValueError(
                f"model_year {self.model_year} outside [{MIN_MODEL_YEAR}, {MAX_MODEL_YEAR}]"
            )
        if self.displacement_l is not None and self.displacement_l <= 0:
            raise ValueError(f"displacement_l must be positive, got {self.displacement_l}")
        if self.horsepower is not None and not (1 <= self.horsepower <= 2000):
            raise ValueError(f"horsepower {self.horsepower} implausible")

    # --- construction ------------------------------------------------------

    @classmethod
    def from_raw(
        cls,
        *,
        make: str | None = None,
        model: str | None = None,
        model_year: int | None = None,
        generation: str | None = None,
        trim: str | None = None,
        engine_code: str | None = None,
        displacement: float | int | None = None,
        fuel: str | None = None,
        transmission: str | None = None,
        drivetrain: str | None = None,
        body: str | None = None,
        horsepower: int | None = None,
        import_status: ImportStatus = ImportStatus.UNKNOWN,
    ) -> VehicleConfiguration:
        """Build a configuration from free-text source fields.

        This is the single entry point that applies market normalization, so
        every path into the system — VIN, manual entry, listing URL, OCR —
        produces identically-keyed configurations.
        """
        canonical_make = normalize_make(make)
        return cls(
            make=canonical_make,
            model=normalize_model(canonical_make, model),
            model_year=model_year,
            generation=(generation or "").strip().upper() or None,
            trim=(trim or "").strip().upper() or None,
            engine_code=(engine_code or "").strip().upper() or None,
            displacement_l=normalize_displacement(displacement),
            fuel=normalize_fuel(fuel),
            transmission=normalize_transmission(transmission),
            drivetrain=normalize_drivetrain(drivetrain),
            body=normalize_body(body),
            horsepower=horsepower,
            import_status=import_status,
        )

    def with_updates(self, **changes: object) -> VehicleConfiguration:
        """Return a copy with selected fields replaced."""
        return replace(self, **changes)  # type: ignore[arg-type]

    # --- identity keys -----------------------------------------------------

    @property
    def is_resolvable(self) -> bool:
        """Whether there is enough information to key this vehicle at all.

        Make and model are the floor. Without them no rung of the ladder is
        meaningful and the vehicle cannot participate in market comparison.
        """
        return bool(self.make and self.model)

    @property
    def model_key(self) -> str:
        return _SEP.join((_token(self.make), _token(self.model)))

    @property
    def generation_key(self) -> str:
        return _SEP.join((self.model_key, _token(self.generation)))

    @property
    def powertrain_key(self) -> str:
        return _SEP.join(
            (
                self.generation_key,
                _token(self.fuel),
                _token(self.displacement_l),
                _token(self.engine_code),
                _token(self.transmission),
                _token(self.drivetrain),
            )
        )

    @property
    def canonical_string(self) -> str:
        """Full human-readable identity string that ``config_id`` hashes.

        Kept accessible because an opaque hash is undebuggable; when two
        vehicles unexpectedly key apart, this is what you diff.
        """
        return _SEP.join(
            (
                self.powertrain_key,
                _token(self.model_year),
                _token(self.trim),
                _token(self.body),
                _token(self.import_status),
            )
        )

    @property
    def config_id(self) -> str:
        """Stable, deterministic identifier for this exact configuration.

        Content-addressed rather than sequential so that the same vehicle
        resolves to the same ID across services, ingestion runs and database
        rebuilds without coordination.
        """
        digest = hashlib.sha256(self.canonical_string.encode("utf-8")).hexdigest()
        return f"cfg_{digest[:20]}"

    def key_at(self, level: KeyLevel) -> str:
        """The identity key at a given rung of the ladder."""
        return {
            KeyLevel.CONFIG: self.config_id,
            KeyLevel.POWERTRAIN: self.powertrain_key,
            KeyLevel.GENERATION: self.generation_key,
            KeyLevel.MODEL: self.model_key,
        }[level]

    # --- specificity -------------------------------------------------------

    #: Fields that meaningfully narrow a comparable set, and their relative
    #: contribution to how completely a vehicle is specified.
    _SPECIFICITY_WEIGHTS: tuple[tuple[str, float], ...] = (
        ("make", 0.14),
        ("model", 0.14),
        ("model_year", 0.13),
        ("generation", 0.10),
        ("trim", 0.09),
        ("displacement_l", 0.09),
        ("fuel", 0.08),
        ("transmission", 0.08),
        ("drivetrain", 0.08),
        ("body", 0.04),
        ("engine_code", 0.03),
    )

    @property
    def specificity(self) -> float:
        """How completely this vehicle is described, in ``[0, 1]``.

        Feeds the confidence engine directly (spec §48): an analysis of "2019
        BMW 5 Series, everything else unknown" must not claim the confidence of
        an analysis of a fully-decoded VIN.
        """
        total = 0.0
        for name, weight in self._SPECIFICITY_WEIGHTS:
            if _is_known(getattr(self, name)):
                total += weight
        return round(total, 4)

    @property
    def known_fields(self) -> tuple[str, ...]:
        return tuple(
            name for name, _ in self._SPECIFICITY_WEIGHTS if _is_known(getattr(self, name))
        )

    @property
    def unknown_fields(self) -> tuple[str, ...]:
        """Fields a user could supply to sharpen the analysis.

        Surfaced in the UI as "add these to improve confidence", which turns a
        low-confidence result into an actionable one.
        """
        return tuple(
            name for name, _ in self._SPECIFICITY_WEIGHTS if not _is_known(getattr(self, name))
        )

    def describe(self) -> str:
        """Short human label, e.g. ``2019 BMW 530I xDrive 2.0 PETROL AWD``."""
        parts: list[str] = []
        if self.model_year:
            parts.append(str(self.model_year))
        if self.make:
            parts.append(self.make)
        if self.model:
            parts.append(self.model)
        if self.trim:
            parts.append(self.trim)
        if self.displacement_l:
            parts.append(f"{self.displacement_l:.1f}L")
        if self.fuel is not FuelType.UNKNOWN:
            parts.append(self.fuel.value.title())
        if self.drivetrain is not Drivetrain.UNKNOWN:
            parts.append(self.drivetrain.value)
        return " ".join(parts) if parts else "Unidentified vehicle"


def age_in_years(config: VehicleConfiguration, reference_year: int) -> int | None:
    """Vehicle age against a reference year, floored at zero.

    Model years run ahead of calendar years, so a 2026 model observed in 2025
    is age 0, not age -1.
    """
    if config.model_year is None:
        return None
    return max(0, reference_year - config.model_year)
