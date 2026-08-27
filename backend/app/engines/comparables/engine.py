"""Comparable-vehicle selection and similarity scoring (spec §11).

The engine answers one question: *which listings genuinely inform the value of
this vehicle, and how much should each of them count?*

Two scores are produced per candidate, and keeping them separate matters:

``config_similarity``
    How close the *vehicle* is — generation, year, engine, gearbox, drive,
    body, trim. This is what determines tier membership.

``weight``
    How much the *observation* should influence the estimate — similarity
    sharpened by an exponent, then discounted for staleness, mileage distance
    and geography.

Mileage is deliberately a weak down-weighting factor rather than a filter. The
valuation engine fits the mileage/price slope *from this same comparable set*
(spec §14), so aggressively excluding high- and low-mileage cars would destroy
the very variation that fit depends on. Mileage difference is priced as an
adjustment, not hidden by exclusion.

Pure computation. No I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import ComparableTier, Drivetrain, FuelType, Transmission
from app.domain.identity import KeyLevel, VehicleConfiguration
from app.domain.market import MarketListing, SubjectVehicle
from app.domain.normalization import market_region

# --- Feature weights -------------------------------------------------------

# Contributions to configuration similarity. Sum to 1.0.
#
# Model year and generation carry the most weight because they bound the
# equipment, safety and styling generation of the car. Body carries least
# because within a single model it rarely varies, and when it does the trim and
# powertrain fields usually already capture the difference.
FEATURE_WEIGHTS: dict[str, float] = {
    "model_year": 0.20,
    "generation": 0.18,
    "displacement": 0.14,
    "drivetrain": 0.12,
    "fuel": 0.12,
    "transmission": 0.10,
    "trim": 0.08,
    "body": 0.06,
}

# Credit given when a feature cannot be compared because one side is UNKNOWN.
# Neither reward nor punish: we genuinely do not know. The resulting uncertainty
# is surfaced through the confidence engine instead of being buried in the
# similarity number.
UNKNOWN_CREDIT = 0.5

# Similarity is raised to this power before weighting, so that a 0.95 match
# counts roughly four times a 0.65 match rather than merely 1.5 times. Spec §11:
# "only high-quality comparables should strongly influence the valuation".
SIMILARITY_SHARPENING = 2.5

# Tier thresholds on configuration similarity.
TIER_1_THRESHOLD = 0.90
TIER_2_THRESHOLD = 0.75
TIER_3_THRESHOLD = 0.55

# Freshness half-life. A listing observed three months ago carries half the
# weight of one observed today.
RECENCY_HALF_LIFE_DAYS = 90.0

# Mileage distance at which weight falls to ~0.61. Deliberately generous.
MILEAGE_SCALE_KM = 80_000.0
MILEAGE_WEIGHT_FLOOR = 0.5

# Years over which model-year similarity decays to zero.
YEAR_DECAY_SPAN = 4.0


@dataclass(frozen=True, slots=True)
class FeatureScore:
    """One feature's contribution, kept for explainability (spec §69)."""

    feature: str
    score: float
    weight: float
    comparable: bool
    """False when the feature could not be compared (either side UNKNOWN)."""

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass(frozen=True, slots=True)
class ComparableMatch:
    """A scored candidate comparable."""

    listing: MarketListing
    tier: ComparableTier
    config_similarity: float
    weight: float
    recency_factor: float
    mileage_factor: float
    geo_factor: float
    features: tuple[FeatureScore, ...]
    differences: tuple[str, ...]
    """Plain-language differences from the subject, shown in the UI."""

    @property
    def price_azn(self) -> float:
        return self.listing.price_azn()

    @property
    def uncomparable_features(self) -> tuple[str, ...]:
        return tuple(f.feature for f in self.features if not f.comparable)


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """Tunable thresholds for comparable selection.

    Exposed as data rather than constants so the same engine serves a dense
    segment (Baku, mainstream sedan) and a sparse one (rare import) without
    branching logic.
    """

    target_sample: int = 25
    """Sample size at which we stop widening. Not a hard cap."""

    min_sample: int = 5
    """Below this the valuation engine will decline to produce a range."""

    max_sample: int = 300
    min_similarity: float = 0.45
    """Candidates below this are never included at any tier."""

    max_tier: ComparableTier = ComparableTier.TIER_3
    """Tier 4 (cross-model substitutes) is opt-in; it informs context, not price."""

    observation_window_days: int = 180
    """Listings last seen longer ago than this are excluded as stale."""

    include_inactive: bool = True
    """Keep listings that have since been removed. Excluding them would bias the
    sample toward cars that did not sell (audit §7.5)."""


@dataclass(frozen=True, slots=True)
class ComparableSet:
    """The selected comparables plus the story of how they were selected."""

    matches: tuple[ComparableMatch, ...]
    tier_used: ComparableTier
    key_level_used: KeyLevel
    widened: bool
    candidates_considered: int
    excluded_stale: int
    excluded_low_similarity: int
    policy: SelectionPolicy

    @property
    def size(self) -> int:
        return len(self.matches)

    @property
    def prices(self) -> tuple[float, ...]:
        return tuple(m.price_azn for m in self.matches)

    @property
    def weights(self) -> tuple[float, ...]:
        return tuple(m.weight for m in self.matches)

    @property
    def mean_similarity(self) -> float:
        if not self.matches:
            return 0.0
        return sum(m.config_similarity for m in self.matches) / len(self.matches)

    @property
    def weighted_mean_similarity(self) -> float:
        """Similarity weighted by influence — the number that actually matters.

        A set of 50 comparables whose weight is concentrated in three excellent
        matches is not equivalent to 50 mediocre ones, and confidence should
        reflect that.
        """
        total_weight = sum(m.weight for m in self.matches)
        if total_weight <= 0:
            return self.mean_similarity
        return sum(m.config_similarity * m.weight for m in self.matches) / total_weight

    @property
    def effective_sample_size(self) -> float:
        """Kish effective sample size: ``(Σw)² / Σw²``.

        Reports how many *equally-weighted* observations the set is worth. Fifty
        comparables where one carries 90% of the weight has an effective size
        near 1, and confidence must be computed from this rather than from the
        raw count.
        """
        weights = self.weights
        if not weights:
            return 0.0
        total = sum(weights)
        sum_sq = sum(w * w for w in weights)
        if sum_sq <= 0:
            return 0.0
        return (total * total) / sum_sq

    def tier_counts(self) -> dict[ComparableTier, int]:
        counts: dict[ComparableTier, int] = {}
        for match in self.matches:
            counts[match.tier] = counts.get(match.tier, 0) + 1
        return counts

    def top(self, n: int) -> tuple[ComparableMatch, ...]:
        return self.matches[:n]


# --- Feature comparison ----------------------------------------------------


def _score_year(subject: int | None, candidate: int | None) -> tuple[float, bool]:
    if subject is None or candidate is None:
        return UNKNOWN_CREDIT, False
    delta = abs(subject - candidate)
    return max(0.0, 1.0 - delta / YEAR_DECAY_SPAN), True


def _score_generation(subject: str | None, candidate: str | None) -> tuple[float, bool]:
    if not subject or not candidate:
        return UNKNOWN_CREDIT, False
    return (1.0 if subject == candidate else 0.0), True


def _score_displacement(subject: float | None, candidate: float | None) -> tuple[float, bool]:
    if subject is None or candidate is None:
        return UNKNOWN_CREDIT, False
    delta = abs(subject - candidate)
    if delta < 0.05:
        return 1.0, True
    if delta <= 0.2:
        return 0.7, True
    if delta <= 0.5:
        return 0.4, True
    if delta <= 1.0:
        return 0.15, True
    return 0.0, True


_AUTOMATIC_FAMILY = frozenset(
    {Transmission.AUTOMATIC, Transmission.CVT, Transmission.DCT, Transmission.AMT}
)


def _score_transmission(subject: Transmission, candidate: Transmission) -> tuple[float, bool]:
    if subject is Transmission.UNKNOWN or candidate is Transmission.UNKNOWN:
        return UNKNOWN_CREDIT, False
    if subject is candidate:
        return 1.0, True
    # Buyers substitute freely within self-shifting gearboxes; manual and
    # automatic are treated as near-different cars because the local market
    # prices them very differently.
    if subject in _AUTOMATIC_FAMILY and candidate in _AUTOMATIC_FAMILY:
        return 0.6, True
    return 0.1, True


def _score_drivetrain(subject: Drivetrain, candidate: Drivetrain) -> tuple[float, bool]:
    if subject is Drivetrain.UNKNOWN or candidate is Drivetrain.UNKNOWN:
        return UNKNOWN_CREDIT, False
    if subject is candidate:
        return 1.0, True
    if Drivetrain.AWD in (subject, candidate):
        return 0.3, True
    return 0.35, True


_COMBUSTION = frozenset({FuelType.PETROL, FuelType.DIESEL, FuelType.LPG, FuelType.CNG})
_ELECTRIFIED = frozenset({FuelType.HYBRID, FuelType.PLUGIN_HYBRID, FuelType.ELECTRIC})


def _score_fuel(subject: FuelType, candidate: FuelType) -> tuple[float, bool]:
    if subject is FuelType.UNKNOWN or candidate is FuelType.UNKNOWN:
        return UNKNOWN_CREDIT, False
    if subject is candidate:
        return 1.0, True
    pair = {subject, candidate}
    if pair == {FuelType.PETROL, FuelType.LPG}:
        # An LPG conversion is the same car with an aftermarket system.
        return 0.75, True
    if pair == {FuelType.HYBRID, FuelType.PLUGIN_HYBRID}:
        return 0.6, True
    if pair <= _ELECTRIFIED:
        return 0.35, True
    if subject in _COMBUSTION and candidate in _COMBUSTION:
        return 0.25, True
    return 0.0, True


def _score_trim(subject: str | None, candidate: str | None) -> tuple[float, bool]:
    if not subject or not candidate:
        return UNKNOWN_CREDIT, False
    if subject == candidate:
        return 1.0, True
    # Same model, different trim: still informative about the model's market
    # level, but equipment differences are real money.
    return 0.25, True


_BODY_AFFINITY: dict[frozenset[str], float] = {
    frozenset({"SUV", "CROSSOVER"}): 0.8,
    frozenset({"SEDAN", "LIFTBACK"}): 0.75,
    frozenset({"HATCHBACK", "LIFTBACK"}): 0.75,
    frozenset({"SEDAN", "WAGON"}): 0.6,
    frozenset({"WAGON", "LIFTBACK"}): 0.6,
    frozenset({"HATCHBACK", "SEDAN"}): 0.5,
    frozenset({"MINIVAN", "VAN"}): 0.6,
    frozenset({"COUPE", "CONVERTIBLE"}): 0.6,
}


def _score_body(subject: object, candidate: object) -> tuple[float, bool]:
    s, c = str(subject), str(candidate)
    if s == "UNKNOWN" or c == "UNKNOWN":
        return UNKNOWN_CREDIT, False
    if s == c:
        return 1.0, True
    return _BODY_AFFINITY.get(frozenset({s, c}), 0.2), True


# --- Similarity ------------------------------------------------------------


def score_configuration(
    subject: VehicleConfiguration,
    candidate: VehicleConfiguration,
) -> tuple[float, tuple[FeatureScore, ...]]:
    """Weighted configuration similarity in ``[0, 1]``, with its breakdown.

    Returns ``0.0`` immediately when make or model differ: cross-model
    comparison is a Tier-4 substitute question, handled separately, not a
    matter of degree within this score.
    """
    if subject.make != candidate.make or subject.model != candidate.model:
        return 0.0, ()

    raw: list[tuple[str, float, bool]] = [
        ("model_year", *_score_year(subject.model_year, candidate.model_year)),
        ("generation", *_score_generation(subject.generation, candidate.generation)),
        ("displacement", *_score_displacement(subject.displacement_l, candidate.displacement_l)),
        ("drivetrain", *_score_drivetrain(subject.drivetrain, candidate.drivetrain)),
        ("fuel", *_score_fuel(subject.fuel, candidate.fuel)),
        ("transmission", *_score_transmission(subject.transmission, candidate.transmission)),
        ("trim", *_score_trim(subject.trim, candidate.trim)),
        ("body", *_score_body(subject.body, candidate.body)),
    ]

    features = tuple(
        FeatureScore(feature=name, score=score, weight=FEATURE_WEIGHTS[name], comparable=comparable)
        for name, score, comparable in raw
    )
    total = sum(f.contribution for f in features)
    return round(min(1.0, max(0.0, total)), 4), features


def describe_differences(
    subject: VehicleConfiguration,
    candidate: VehicleConfiguration,
    candidate_mileage: int | None,
    subject_mileage: int | None,
) -> tuple[str, ...]:
    """Plain-language differences, for the comparables table in the report."""
    out: list[str] = []

    if subject.model_year and candidate.model_year:
        delta = candidate.model_year - subject.model_year
        if delta:
            direction = "newer" if delta > 0 else "older"
            out.append(f"{abs(delta)} model year{'s' if abs(delta) > 1 else ''} {direction}")

    if subject.trim and candidate.trim and subject.trim != candidate.trim:
        out.append(f"trim {candidate.trim} vs {subject.trim}")

    if (
        subject.displacement_l
        and candidate.displacement_l
        and abs(subject.displacement_l - candidate.displacement_l) >= 0.05
    ):
        out.append(f"{candidate.displacement_l:.1f}L engine vs {subject.displacement_l:.1f}L")

    if (
        subject.transmission is not Transmission.UNKNOWN
        and candidate.transmission is not Transmission.UNKNOWN
        and subject.transmission is not candidate.transmission
    ):
        out.append(f"{candidate.transmission.value.lower()} gearbox")

    if (
        subject.drivetrain is not Drivetrain.UNKNOWN
        and candidate.drivetrain is not Drivetrain.UNKNOWN
        and subject.drivetrain is not candidate.drivetrain
    ):
        out.append(f"{candidate.drivetrain.value} drivetrain")

    if (
        subject.fuel is not FuelType.UNKNOWN
        and candidate.fuel is not FuelType.UNKNOWN
        and subject.fuel is not candidate.fuel
    ):
        out.append(f"{candidate.fuel.value.lower()} fuel")

    if subject_mileage is not None and candidate_mileage is not None:
        delta_km = candidate_mileage - subject_mileage
        if abs(delta_km) >= 10_000:
            direction = "more" if delta_km > 0 else "less"
            out.append(f"{abs(delta_km):,} km {direction}")

    return tuple(out)


# --- Observation weighting -------------------------------------------------


def _recency_factor(listing: MarketListing, as_of: datetime) -> float:
    age_days = listing.age_days(as_of)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def _mileage_factor(subject_mileage: int | None, candidate_mileage: int | None) -> float:
    """Mild down-weighting for mileage distance.

    Kept mild on purpose: the valuation engine fits its mileage/price slope from
    this same set, and a set squeezed to near-identical mileage would carry no
    information about that slope at all.
    """
    if subject_mileage is None or candidate_mileage is None:
        return 1.0
    delta = abs(subject_mileage - candidate_mileage)
    factor = math.exp(-0.5 * (delta / MILEAGE_SCALE_KM) ** 2)
    return max(MILEAGE_WEIGHT_FLOOR, factor)


def _geo_factor(subject_city: str | None, candidate_city: str | None) -> float:
    """Geographic relevance. Never zero — this is one national market."""
    if not subject_city or not candidate_city:
        return 0.85
    if subject_city == candidate_city:
        return 1.0
    if market_region(subject_city) == market_region(candidate_city):
        return 0.92
    return 0.7


def _tier_for(similarity: float) -> ComparableTier:
    if similarity >= TIER_1_THRESHOLD:
        return ComparableTier.TIER_1
    if similarity >= TIER_2_THRESHOLD:
        return ComparableTier.TIER_2
    if similarity >= TIER_3_THRESHOLD:
        return ComparableTier.TIER_3
    return ComparableTier.TIER_4


# --- Engine ----------------------------------------------------------------


@dataclass
class ComparableEngine:
    """Selects and weights comparables for a subject vehicle."""

    policy: SelectionPolicy = field(default_factory=SelectionPolicy)

    def select(
        self,
        subject: SubjectVehicle,
        candidates: Sequence[MarketListing],
        as_of: datetime,
    ) -> ComparableSet:
        """Score every candidate, then admit the tightest tier that suffices.

        Widening is deterministic and reported. We start by admitting only
        Tier 1; if that yields fewer than ``target_sample`` we admit Tier 2, and
        so on. The tier we stopped at becomes visible evidence in the report —
        "we had to compare across model years to find 30 cars" is exactly the
        kind of thing that must not be silent.
        """
        scored: list[ComparableMatch] = []
        excluded_stale = 0
        excluded_low_similarity = 0

        for listing in candidates:
            if _is_subject_itself(subject, listing):
                continue  # never compare a vehicle against itself

            if listing.age_days(as_of) > self.policy.observation_window_days:
                excluded_stale += 1
                continue
            if not self.policy.include_inactive and not listing.is_active:
                excluded_stale += 1
                continue

            similarity, features = score_configuration(subject.configuration, listing.configuration)
            if similarity < self.policy.min_similarity:
                excluded_low_similarity += 1
                continue

            recency = _recency_factor(listing, as_of)
            mileage = _mileage_factor(subject.mileage_km, listing.mileage_km)
            geo = _geo_factor(subject.city, listing.city)
            weight = (similarity**SIMILARITY_SHARPENING) * recency * mileage * geo

            scored.append(
                ComparableMatch(
                    listing=listing,
                    tier=_tier_for(similarity),
                    config_similarity=similarity,
                    weight=round(weight, 6),
                    recency_factor=round(recency, 4),
                    mileage_factor=round(mileage, 4),
                    geo_factor=round(geo, 4),
                    features=features,
                    differences=describe_differences(
                        subject.configuration,
                        listing.configuration,
                        listing.mileage_km,
                        subject.mileage_km,
                    ),
                )
            )

        scored.sort(key=lambda m: (m.tier, -m.weight))

        tier_used, admitted, widened = self._admit(scored)
        admitted = admitted[: self.policy.max_sample]

        return ComparableSet(
            matches=tuple(admitted),
            tier_used=tier_used,
            key_level_used=_key_level_for_tier(tier_used),
            widened=widened,
            candidates_considered=len(candidates),
            excluded_stale=excluded_stale,
            excluded_low_similarity=excluded_low_similarity,
            policy=self.policy,
        )

    def _admit(
        self, scored: list[ComparableMatch]
    ) -> tuple[ComparableTier, list[ComparableMatch], bool]:
        """Walk the tiers outward until the sample is large enough."""
        allowed = [t for t in ComparableTier if t <= self.policy.max_tier]
        admitted: list[ComparableMatch] = []
        tier_used = allowed[0]

        for tier in allowed:
            tier_used = tier
            admitted = [m for m in scored if m.tier <= tier]
            if len(admitted) >= self.policy.target_sample:
                break

        widened = tier_used > allowed[0]
        return tier_used, admitted, widened


def _key_level_for_tier(tier: ComparableTier) -> KeyLevel:
    return {
        ComparableTier.TIER_1: KeyLevel.CONFIG,
        ComparableTier.TIER_2: KeyLevel.POWERTRAIN,
        ComparableTier.TIER_3: KeyLevel.GENERATION,
        ComparableTier.TIER_4: KeyLevel.MODEL,
    }[tier]


def _is_subject_itself(subject: SubjectVehicle, listing: MarketListing) -> bool:
    """Whether a candidate is the subject's own listing.

    When the user analyses a listing URL, that listing is very likely already
    in our market database. Leaving it in the comparable set would let the
    asking price help set the fair value it is being judged against.
    """
    if subject.listing_url and listing.source_url:
        return subject.listing_url.rstrip("/") == listing.source_url.rstrip("/")
    return False
