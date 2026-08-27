"""Structured output contract for the reasoning layer (spec §33).

The model must return JSON matching these models exactly. Anything else is
rejected and retried; unvalidated model output never reaches the frontend.

Two design choices worth stating.

**Numeric fields are echoes, not computations.** ``MarketAssessment`` carries
the same figures the valuation engine produced. The model is asked to copy them
so that the validator can verify it was looking at the right evidence — a model
that echoes a different central estimate than the one we computed has drifted,
and its prose cannot be trusted either.

**Every narrative claim is tagged.** :class:`Claim` forces the model to mark
each statement as FACT, INFERENCE or POSSIBILITY (spec §32). This is not
decoration: the UI renders the three differently, and a "possibility" presented
with the visual weight of a fact is precisely the failure mode the product
exists to avoid.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClaimKind(StrEnum):
    """Epistemic status of a statement (spec §32)."""

    FACT = "FACT"
    """Directly supported by a number or observation in the evidence bundle."""

    INFERENCE = "INFERENCE"
    """A reasonable reading of the evidence, stated as such."""

    POSSIBILITY = "POSSIBILITY"
    """Something that cannot be established from the evidence and needs
    independent verification."""


class Claim(BaseModel):
    """One tagged statement."""

    model_config = ConfigDict(extra="forbid")

    kind: ClaimKind
    statement: str = Field(min_length=1, max_length=600)
    basis: str = Field(
        default="",
        max_length=300,
        description="Which part of the supplied evidence supports this statement.",
    )

    @field_validator("statement")
    @classmethod
    def _no_verdicts(cls, value: str) -> str:
        """Reject purchase verdicts outright (spec §35).

        The product's core philosophy is that the decision stays with the user.
        A model that writes "you should buy this car" has broken that contract,
        and the cheapest place to enforce it is the type system.
        """
        lowered = value.lower()
        for phrase in _FORBIDDEN_VERDICTS:
            if phrase in lowered:
                raise ValueError(
                    f"statement contains a purchase verdict ({phrase!r}); the report "
                    f"presents evidence and leaves the decision to the user"
                )
        return value


class MarketAssessment(BaseModel):
    """Echoed market figures. Validated against the computed evidence."""

    model_config = ConfigDict(extra="forbid")

    asking_price: float | None = None
    fair_market_low: float | None = None
    fair_market_high: float | None = None
    central_estimate: float | None = None
    price_difference_percent: float | None = None
    price_percentile: float | None = None
    rating: str
    confidence: int = Field(ge=0, le=100)


class NegotiationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=1200)
    opening_offer: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    key_arguments: list[str] = Field(default_factory=list, max_length=8)


class VehicleReport(BaseModel):
    """The complete structured narrative (spec §33, §34)."""

    model_config = ConfigDict(extra="forbid")

    vehicle_summary: str = Field(max_length=1500)
    market_assessment: MarketAssessment
    market_context: str = Field(default="", max_length=2000)

    positive_signals: list[Claim] = Field(default_factory=list, max_length=10)
    risk_signals: list[Claim] = Field(default_factory=list, max_length=15)
    model_specific_concerns: list[Claim] = Field(default_factory=list, max_length=10)

    price_explanation: str = Field(default="", max_length=2000)
    """Narrative for spec §19 — why this car sits where it does against the market."""

    seller_questions: list[str] = Field(default_factory=list, max_length=12)
    inspection_priorities: list[str] = Field(default_factory=list, max_length=15)
    negotiation_strategy: NegotiationOutput | None = None

    final_assessment: str = Field(max_length=2500)
    limitations: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("final_assessment", "vehicle_summary", "market_context", "price_explanation")
    @classmethod
    def _no_verdicts(cls, value: str) -> str:
        lowered = value.lower()
        for phrase in _FORBIDDEN_VERDICTS:
            if phrase in lowered:
                raise ValueError(
                    f"text contains a purchase verdict ({phrase!r}); spec §35 requires "
                    f"the report to present evidence and leave the decision to the user"
                )
        return value

    @field_validator("final_assessment")
    @classmethod
    def _no_guarantees(cls, value: str) -> str:
        """Reject guarantees the product explicitly does not make (spec §1)."""
        lowered = value.lower()
        for phrase in _FORBIDDEN_GUARANTEES:
            if phrase in lowered:
                raise ValueError(
                    f"final assessment contains a guarantee ({phrase!r}); the platform "
                    f"guarantees no condition, history or valuation claim"
                )
        return value


#: Phrases that constitute a purchase verdict. Checked case-insensitively as
#: substrings, so they must be specific enough not to catch legitimate prose —
#: "worth checking" must survive, "worth buying" must not.
_FORBIDDEN_VERDICTS = (
    "you should buy",
    "you should not buy",
    "you shouldn't buy",
    "do not buy",
    "don't buy",
    "definitely buy",
    "i recommend buying",
    "i recommend purchasing",
    "we recommend buying",
    "avoid this car",
    "avoid this vehicle",
    "walk away from this car",
    "this is a must-buy",
    "buy it",
    "worth buying",
    "not worth buying",
)

#: Guarantees the product does not make (spec §1).
_FORBIDDEN_GUARANTEES = (
    "guaranteed",
    "we guarantee",
    "is certainly accident-free",
    "definitely has no",
    "certainly free of",
    "no hidden problems",
    "problem-free",
)
