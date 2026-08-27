"""Anti-hallucination validation (spec §32).

Prompt instructions are guidance, not enforcement. A model told "do not invent
numbers" will still occasionally invent one, and a product whose central claim
is trustworthy market data cannot ship a control that works most of the time.

So every response is checked in code:

1. **Echo check.** The market figures the model reports must equal the ones the
   engines computed. A mismatch means the model was not reading the evidence it
   was given, which invalidates its prose as well as its numbers.
2. **Registry check.** Every number appearing in free prose must exist in the
   evidence bundle (within rounding tolerance). Numbers that do not are
   fabrications.
3. **Rating consistency.** The model may not upgrade or downgrade the computed
   deal rating.
4. **Claim discipline.** Statements about unverifiable matters — accident
   history, hidden damage, mechanical condition — may not be tagged ``FACT``.

Failures are returned as structured findings so the caller can retry with the
specific problem fed back to the model, rather than blindly resampling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.adapters.llm.schema import Claim, ClaimKind, VehicleReport
from app.engines.evidence.bundle import (
    NumericRegistry,
    is_trivial_number,
    numeric_registry,
)

#: Numbers in prose, including thousands separators and decimals.
_NUMBER_PATTERN = re.compile(r"(?<![\w.])(\d{1,3}(?:[ ,]\d{3})+|\d+(?:\.\d+)?)(?![\w])")

#: Relative tolerance when comparing an echoed figure to the computed one.
_ECHO_TOLERANCE = 0.005

#: Subjects that cannot be established from listing data. A model asserting any
#: of these as FACT has overstepped what the evidence can support.
_UNVERIFIABLE_SUBJECTS = (
    "accident",
    "crash",
    "flood",
    "odometer rollback",
    "odometer fraud",
    "tampered",
    "stolen",
    "salvage",
    "engine failure",
    "transmission failure",
    "head gasket",
    "rust damage",
    "never been repaired",
    "has been repaired",
)


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """One problem detected in a model response."""

    code: str
    message: str
    field_path: str = ""

    def __str__(self) -> str:
        return f"[{self.code}] {self.field_path}: {self.message}" if self.field_path else (
            f"[{self.code}] {self.message}"
        )


@dataclass
class ValidationResult:
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, code: str, message: str, field_path: str = "") -> None:
        self.findings.append(ValidationFinding(code, message, field_path))

    def feedback(self) -> str:
        """Corrective text fed back to the model on retry."""
        lines = ["The previous response was rejected for these reasons:"]
        lines.extend(f"- {finding}" for finding in self.findings)
        lines.append(
            "Correct these issues. Use only figures present in the supplied evidence, "
            "and do not restate any number that is not in it."
        )
        return "\n".join(lines)


def extract_numbers(text: str) -> list[float]:
    """Pull numeric literals out of prose, ignoring trivially small integers."""
    out: list[float] = []
    for match in _NUMBER_PATTERN.finditer(text):
        raw = match.group(1).replace(",", "").replace(" ", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if is_trivial_number(value):
            continue
        out.append(value)
    return out


def validate_report(
    report: VehicleReport,
    bundle: dict[str, Any],
    registry: NumericRegistry | None = None,
) -> ValidationResult:
    """Check a model response against the evidence it was given."""
    result = ValidationResult()
    registry = registry or numeric_registry(bundle)

    _check_echoed_figures(report, bundle, result)
    _check_rating(report, bundle, result)
    _check_prose_numbers(report, registry, result)
    _check_claim_discipline(report, result)
    _check_coverage(report, bundle, result)

    return result


def _check_echoed_figures(
    report: VehicleReport, bundle: dict[str, Any], result: ValidationResult
) -> None:
    """The echoed market figures must match what the engines computed."""
    valuation = bundle.get("valuation", {})
    position = bundle.get("price_position", {})
    confidence = bundle.get("confidence", {})
    assessment = report.market_assessment

    expected = {
        "central_estimate": valuation.get("central_estimate_azn"),
        "fair_market_low": valuation.get("fair_market_low_azn"),
        "fair_market_high": valuation.get("fair_market_high_azn"),
        "asking_price": position.get("asking_price_azn"),
        "price_difference_percent": position.get("difference_percent"),
        "price_percentile": position.get("percentile"),
    }

    for name, want in expected.items():
        got = getattr(assessment, name)
        if want is None and got is None:
            continue
        if want is None and got is not None:
            result.add(
                "INVENTED_FIGURE",
                f"reported {name}={got} but the evidence contains no such figure",
                f"market_assessment.{name}",
            )
            continue
        if got is None:
            continue
        scale = max(abs(float(want)), 1.0)
        if abs(float(got) - float(want)) / scale > _ECHO_TOLERANCE:
            result.add(
                "FIGURE_MISMATCH",
                f"reported {name}={got} but the computed value is {want}",
                f"market_assessment.{name}",
            )

    want_confidence = confidence.get("score_percent")
    if want_confidence is not None and abs(assessment.confidence - want_confidence) > 1:
        result.add(
            "FIGURE_MISMATCH",
            f"reported confidence={assessment.confidence} but the computed value is "
            f"{want_confidence}",
            "market_assessment.confidence",
        )


def _check_rating(report: VehicleReport, bundle: dict[str, Any], result: ValidationResult) -> None:
    computed = bundle.get("price_position", {}).get("rating")
    if computed is None:
        return
    if report.market_assessment.rating != computed:
        result.add(
            "RATING_MISMATCH",
            f"reported rating {report.market_assessment.rating!r} but the computed rating "
            f"is {computed!r}; the rating is determined by the valuation engine and may "
            f"not be changed",
            "market_assessment.rating",
        )


def _check_prose_numbers(
    report: VehicleReport, registry: NumericRegistry, result: ValidationResult
) -> None:
    """Every non-trivial number in prose must trace to the evidence bundle."""
    prose_fields = {
        "vehicle_summary": report.vehicle_summary,
        "market_context": report.market_context,
        "price_explanation": report.price_explanation,
        "final_assessment": report.final_assessment,
    }
    for path, text in prose_fields.items():
        for value in extract_numbers(text):
            if not registry.contains(value):
                result.add(
                    "UNSUPPORTED_NUMBER",
                    f"the figure {value:,.0f} does not appear in the supplied evidence",
                    path,
                )

    claim_groups = {
        "positive_signals": report.positive_signals,
        "risk_signals": report.risk_signals,
        "model_specific_concerns": report.model_specific_concerns,
    }
    for group, claims in claim_groups.items():
        for index, claim in enumerate(claims):
            for value in extract_numbers(claim.statement):
                if not registry.contains(value):
                    result.add(
                        "UNSUPPORTED_NUMBER",
                        f"the figure {value:,.0f} does not appear in the supplied evidence",
                        f"{group}[{index}]",
                    )

    if report.negotiation_strategy:
        for value in extract_numbers(report.negotiation_strategy.summary):
            if not registry.contains(value):
                result.add(
                    "UNSUPPORTED_NUMBER",
                    f"the figure {value:,.0f} does not appear in the supplied evidence",
                    "negotiation_strategy.summary",
                )


def _check_claim_discipline(report: VehicleReport, result: ValidationResult) -> None:
    """Unverifiable subjects may not be asserted as fact (spec §32)."""
    groups = {
        "risk_signals": report.risk_signals,
        "positive_signals": report.positive_signals,
        "model_specific_concerns": report.model_specific_concerns,
    }
    for group, claims in groups.items():
        for index, claim in enumerate(claims):
            if claim.kind is not ClaimKind.FACT:
                continue
            lowered = claim.statement.lower()
            for subject in _UNVERIFIABLE_SUBJECTS:
                if subject in lowered and not _is_disclosure_reference(lowered):
                    result.add(
                        "OVERSTATED_CLAIM",
                        f"statement about {subject!r} is tagged FACT, but nothing in the "
                        f"evidence can establish it; tag it POSSIBILITY and state how it "
                        f"could be verified",
                        f"{group}[{index}]",
                    )
                    break


def _is_disclosure_reference(text: str) -> bool:
    """Whether a claim is reporting a disclosure rather than asserting a fact.

    "The seller states the car was in an accident" is a fact about the listing
    and is legitimately tagged FACT. "The car was in an accident" is not.
    """
    markers = (
        "seller states",
        "seller disclosed",
        "listing states",
        "the listing discloses",
        "disclosed by",
        "according to the listing",
        "the description states",
        "is disclosed",
        "was disclosed",
    )
    return any(marker in text for marker in markers)


def _check_coverage(
    report: VehicleReport, bundle: dict[str, Any], result: ValidationResult
) -> None:
    """The model must not silently drop high-severity findings."""
    severe = [
        signal
        for signal in bundle.get("risk_signals", [])
        if signal.get("severity") in ("HIGH", "CRITICAL")
    ]
    if severe and not report.risk_signals:
        result.add(
            "OMITTED_RISK",
            f"the evidence contains {len(severe)} high-severity risk signal(s) but the "
            f"response reports none",
            "risk_signals",
        )

    if not report.limitations:
        result.add(
            "MISSING_LIMITATIONS",
            "the response omits the limitations section, which the report must always "
            "carry",
            "limitations",
        )


def claim_from_evidence(kind: ClaimKind, statement: str, basis: str = "") -> Claim:
    """Helper for constructing claims in the deterministic fallback narrative."""
    return Claim(kind=kind, statement=statement, basis=basis)
