"""Risk signal types and disclosure detection.

Two things live here: the shape of a risk signal, and the text analysis that
reads disclosures out of seller descriptions.

The wording discipline of spec §19 and §32 is enforced structurally, not by
convention. A :class:`RiskSignal` has no field for a conclusion. It has
``evidence`` (what we observed), ``interpretation`` (what that might mean,
always hedged) and ``recommended_verification`` (how the buyer can find out for
themselves). There is nowhere to write "this car has hidden accident damage",
because the type does not permit that claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.enums import EvidenceStrength, RiskSeverity, RiskType, severity_rank
from app.domain.normalization import fold


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """One detected risk indicator.

    ``severity`` describes how strong the *indicator* is, never how likely the
    car is to be bad. Spec §20 is explicit about this distinction and the whole
    product's credibility rests on holding it.
    """

    risk_type: RiskType
    severity: RiskSeverity
    title: str
    evidence: tuple[str, ...]
    """Factual, checkable observations. Each one must be something a reader
    could verify against the data we showed them."""

    interpretation: str
    """What the evidence *might* mean. Always hedged — "may", "can indicate",
    "is often associated with". Never asserted."""

    recommended_verification: str
    """The concrete action that would resolve the uncertainty."""

    source: str
    confidence: float
    """How sure we are the *indicator is present*, not that a problem exists."""

    strength: EvidenceStrength = EvidenceStrength.MEDIUM

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if not self.evidence:
            raise ValueError(
                f"risk signal {self.risk_type.value} has no evidence; "
                "signals without evidence are not permitted"
            )

    @property
    def rank(self) -> int:
        return severity_rank(self.severity)


@dataclass(frozen=True, slots=True)
class PositiveSignal:
    """A reassuring observation.

    A report that only ever lists problems is not an analysis, it is an alarm.
    These are held to the same evidence standard as risk signals.
    """

    title: str
    evidence: tuple[str, ...]
    source: str


# --- Disclosure detection --------------------------------------------------

# Seller descriptions in this market mix Azerbaijani and Russian, and the same
# sentence often carries both a claim and its negation ("vurulmayıb, rənglənib"
# — not hit, but repainted). Negations are therefore checked first for each
# concept, and a matched negation suppresses the positive pattern.

_DAMAGE_NEGATIVE = (
    r"vurulmay[ıi]b",
    # How Turbo.az words it in the condition field, which is a statement by
    # the seller in a structured field rather than prose and so the most
    # reliable form of it we get.
    r"vuru[ğg]u\s*yoxdur",
    r"qəzas[ıi]z",
    r"qezasiz",
    r"не\s*бит",
    r"без\s*авари",
    r"не\s*битый",
)
_DAMAGE_POSITIVE = (
    r"vurulub",
    r"vuru[ğg]u\s*var",
    r"qəza",
    r"qeza",
    r"битый",
    r"\bбит\b",
    r"авари",
    r"после\s*дтп",
    r"\bдтп\b",
)

_REPAINT_NEGATIVE = (
    r"rənglənməy[ıi]b",
    r"renglenmeyib",
    r"boyanmay[ıi]b",
    r"не\s*краш",
    r"без\s*покраск",
    r"родная\s*краска",
)
_REPAINT_POSITIVE = (
    r"rənglən",
    r"renglen",
    r"boyan[ıi]b",
    r"краш",
    r"покраш",
    r"перекраш",
)

_NEEDS_REPAIR = (
    r"təmir\s*tələb",
    r"temir\s*teleb",
    r"требует\s*ремонт",
    r"на\s*запчаст",
    r"ehtiyat\s*hissə",
)

# Unqualified superlatives. Not evidence of anything wrong — but they are
# unverifiable claims, and the product's job is to separate claims from facts.
_STRONG_CLAIMS = (
    r"ideal\s*vəziyyət",
    r"ideal\s*veziyyet",
    r"əla\s*vəziyyət",
    r"идеальн",
    r"отличн[оеый]+\s*состоян",
    r"как\s*новый",
    r"yeni\s*kimi",
)


def _matches(patterns: tuple[str, ...], folded: str, raw_lower: str) -> bool:
    for pattern in patterns:
        if re.search(pattern, folded) or re.search(pattern, raw_lower):
            return True
    return False


@dataclass(frozen=True, slots=True)
class DisclosureReading:
    """What a seller description states about condition.

    Every field is tri-state. ``None`` means *the description does not say*,
    which is different from ``False`` meaning *the seller states it did not
    happen*. Collapsing those two is how a system ends up implying a clean
    history that nobody ever claimed.
    """

    damage: bool | None = None
    repaint: bool | None = None
    needs_repair: bool | None = None
    unverified_superlatives: tuple[str, ...] = ()

    @property
    def has_any_disclosure(self) -> bool:
        return any(v is not None for v in (self.damage, self.repaint, self.needs_repair))


def read_disclosures(description: str | None) -> DisclosureReading:
    """Extract condition disclosures from a free-text seller description.

    Returns tri-state values so that "the seller says it was never hit" and
    "the seller does not mention accidents" stay distinguishable all the way
    into the report.
    """
    if not description or not description.strip():
        return DisclosureReading()

    raw_lower = description.lower()
    folded = fold(description)

    if _matches(_DAMAGE_NEGATIVE, folded, raw_lower):
        damage: bool | None = False
    elif _matches(_DAMAGE_POSITIVE, folded, raw_lower):
        damage = True
    else:
        damage = None

    if _matches(_REPAINT_NEGATIVE, folded, raw_lower):
        repaint: bool | None = False
    elif _matches(_REPAINT_POSITIVE, folded, raw_lower):
        repaint = True
    else:
        repaint = None

    needs_repair = True if _matches(_NEEDS_REPAIR, folded, raw_lower) else None

    superlatives = tuple(
        pattern
        for pattern in _STRONG_CLAIMS
        if re.search(pattern, folded) or re.search(pattern, raw_lower)
    )

    return DisclosureReading(
        damage=damage,
        repaint=repaint,
        needs_repair=needs_repair,
        unverified_superlatives=superlatives,
    )
