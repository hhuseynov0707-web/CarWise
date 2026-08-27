"""Anti-hallucination and output-discipline guarantees (spec §32, §33, §35).

These are the tests that make the product's central claim defensible. The claim
is not "we prompt the model carefully" — it is "a fabricated number cannot reach
the user", and that is only true if it is enforced in code and tested.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.adapters.llm.base import CompletionRequest, CompletionResponse, LLMUnavailable
from app.adapters.llm.fallback import build_fallback_report
from app.adapters.llm.schema import Claim, ClaimKind, MarketAssessment, VehicleReport
from app.adapters.llm.service import ReasoningService
from app.adapters.llm.validation import extract_numbers, validate_report
from app.engines.evidence.bundle import build_evidence_bundle, numeric_registry
from app.services.analysis import AnalysisService, InMemoryMarketRepository
from tests.factories import REFERENCE_NOW, make_subject, synthetic_market


@pytest.fixture
def analysis():
    listings, _ = synthetic_market(count=60)
    service = AnalysisService.build(
        InMemoryMarketRepository(listings), ReasoningService(None, enabled=False)
    )
    subject = make_subject(mileage_km=140_000, asking_price=36_000)
    result = service.compute(subject, listings, [], REFERENCE_NOW)
    return result, build_evidence_bundle(result)


class TestForbiddenVerdicts:
    """Spec §35: the platform never tells the user whether to buy."""

    @pytest.mark.parametrize(
        "text",
        [
            "You should buy this car.",
            "I recommend buying it at this price.",
            "Do not buy this vehicle.",
            "This car is worth buying.",
            "Avoid this car entirely.",
        ],
    )
    def test_verdicts_are_rejected_at_the_type_level(self, text: str) -> None:
        with pytest.raises(ValidationError):
            Claim(kind=ClaimKind.INFERENCE, statement=text)

    @pytest.mark.parametrize(
        "text",
        [
            "The mileage is worth checking against service records.",
            "An inspection is worth arranging before proceeding.",
            "The asking price sits below the comparable median.",
        ],
    )
    def test_legitimate_prose_survives(self, text: str) -> None:
        """The filter must not be so blunt that ordinary advice trips it."""
        assert Claim(kind=ClaimKind.INFERENCE, statement=text).statement == text

    def test_guarantees_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            VehicleReport(
                vehicle_summary="A car.",
                market_assessment=MarketAssessment(rating="FAIR_VALUE", confidence=50),
                final_assessment="This vehicle is guaranteed to be accident-free.",
            )


class TestNumericRegistry:
    def test_bundle_figures_are_recognized(self, analysis) -> None:
        _, bundle = analysis
        registry = numeric_registry(bundle)
        central = bundle["valuation"]["central_estimate_azn"]
        assert registry.contains(central)

    def test_rounding_is_tolerated(self, analysis) -> None:
        """A model writing 44,300 for a computed 44,279 is rounding, not lying."""
        _, bundle = analysis
        registry = numeric_registry(bundle)
        central = bundle["valuation"]["central_estimate_azn"]
        assert registry.contains(round(central, -2))

    def test_fabricated_figures_are_caught(self, analysis) -> None:
        _, bundle = analysis
        registry = numeric_registry(bundle)
        assert not registry.contains(999_777.0)

    def test_magnitudes_are_recognized(self, analysis) -> None:
        """The bundle stores a discount as negative; prose states the magnitude."""
        _, bundle = analysis
        registry = numeric_registry(bundle)
        gap = bundle["price_position"]["gap_analysis"]
        if gap:
            assert registry.contains(abs(gap["unexplained_azn"]))

    def test_figures_quoted_from_evidence_prose_are_recognized(self, analysis) -> None:
        """Explanations in the bundle contain figures; quoting them is citing."""
        _, bundle = analysis
        registry = numeric_registry(bundle)
        assert registry.contains(1000.0)  # "per 1,000 km" appears in adjustment prose


class TestExtractNumbers:
    def test_handles_thousands_separators(self) -> None:
        assert 43_500.0 in extract_numbers("The asking price is 43,500 AZN.")

    def test_ignores_trivial_integers(self) -> None:
        """"three risk signals" style counts would produce endless false positives."""
        assert extract_numbers("There are 3 risk signals and 12 inspection items.") == []

    def test_captures_decimals(self) -> None:
        assert 4321.5 in extract_numbers("A difference of 4321.5 AZN.")


class TestReportValidation:
    def test_the_deterministic_narrative_passes_its_own_validator(self, analysis) -> None:
        """The strongest available check on the validator itself.

        The fallback narrative is constructed only from the bundle, so it cannot
        contain a fabrication. If the validator rejects it, the validator is
        wrong — and would be rejecting correct model output too.
        """
        _, bundle = analysis
        report = build_fallback_report(bundle)
        result = validate_report(report, bundle)
        assert result.ok, [str(f) for f in result.findings]

    def test_altered_central_estimate_is_rejected(self, analysis) -> None:
        _, bundle = analysis
        report = build_fallback_report(bundle)
        report.market_assessment.central_estimate = 99_999.0

        result = validate_report(report, bundle)
        assert not result.ok
        assert any(f.code == "FIGURE_MISMATCH" for f in result.findings)

    def test_altered_rating_is_rejected(self, analysis) -> None:
        """The model may explain the rating; it may not change it."""
        _, bundle = analysis
        report = build_fallback_report(bundle)
        computed = bundle["price_position"]["rating"]
        report.market_assessment.rating = (
            "OVERPRICED" if computed != "OVERPRICED" else "GREAT_VALUE"
        )

        result = validate_report(report, bundle)
        assert any(f.code == "RATING_MISMATCH" for f in result.findings)

    def test_invented_price_in_prose_is_rejected(self, analysis) -> None:
        _, bundle = analysis
        report = build_fallback_report(bundle)
        report.market_context = "Similar cars typically sell for 77,777 AZN in Baku."

        result = validate_report(report, bundle)
        assert any(f.code == "UNSUPPORTED_NUMBER" for f in result.findings)

    def test_asserting_an_accident_as_fact_is_rejected(self, analysis) -> None:
        """Spec §32: nothing in listing data can establish accident history."""
        _, bundle = analysis
        report = build_fallback_report(bundle)
        report.risk_signals = [
            Claim(kind=ClaimKind.FACT, statement="This vehicle was in an accident.")
        ]

        result = validate_report(report, bundle)
        assert any(f.code == "OVERSTATED_CLAIM" for f in result.findings)

    def test_reporting_a_disclosure_as_fact_is_allowed(self, analysis) -> None:
        """"The seller states it was in an accident" is a fact about the listing."""
        _, bundle = analysis
        report = build_fallback_report(bundle)
        report.risk_signals = [
            Claim(
                kind=ClaimKind.FACT,
                statement="The seller states the vehicle was in an accident.",
            )
        ]

        result = validate_report(report, bundle)
        assert not any(f.code == "OVERSTATED_CLAIM" for f in result.findings)

    def test_dropping_the_limitations_section_is_rejected(self, analysis) -> None:
        _, bundle = analysis
        report = build_fallback_report(bundle)
        report.limitations = []

        result = validate_report(report, bundle)
        assert any(f.code == "MISSING_LIMITATIONS" for f in result.findings)

    def test_feedback_names_the_specific_problem(self, analysis) -> None:
        """Retries succeed because the model is told what it got wrong."""
        _, bundle = analysis
        report = build_fallback_report(bundle)
        report.market_assessment.central_estimate = 12_345.0

        feedback = validate_report(report, bundle).feedback()
        assert "12345" in feedback.replace(",", "") or "12,345" in feedback


class _FakeProvider:
    """Scripted provider for exercising the retry loop."""

    name = "fake"

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = responses
        self.calls: list[CompletionRequest] = []

    async def complete_json(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        item = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return CompletionResponse(text=item, model="fake-1")

    async def close(self) -> None:
        return None


class TestReasoningService:
    def test_falls_back_when_the_provider_is_unavailable(self, analysis) -> None:
        _, bundle = analysis
        provider = _FakeProvider([LLMUnavailable("no key")])
        service = ReasoningService(provider, max_attempts=2)

        result = asyncio.run(service.narrate(bundle))
        assert result.generated_by == "fallback"
        assert not result.is_ai_generated
        assert result.degraded_reason

    def test_falls_back_on_unparseable_output(self, analysis) -> None:
        _, bundle = analysis
        service = ReasoningService(_FakeProvider(["not json at all"]), max_attempts=2)

        result = asyncio.run(service.narrate(bundle))
        assert result.generated_by == "fallback"
        assert result.attempts == 2
        assert any("schema violation" in f for f in result.validation_failures)

    def test_the_report_is_always_produced(self, analysis) -> None:
        """The invariant from audit §5, stated as a test."""
        _, bundle = analysis
        for responses in ([LLMUnavailable("x")], ["garbage"], ["{}"]):
            service = ReasoningService(_FakeProvider(list(responses)), max_attempts=2)
            result = asyncio.run(service.narrate(bundle))
            assert result.report is not None
            assert result.report.final_assessment
            assert result.report.limitations

    def test_disabled_service_skips_the_provider_entirely(self, analysis) -> None:
        _, bundle = analysis
        provider = _FakeProvider(["{}"])
        service = ReasoningService(provider, enabled=False)

        result = asyncio.run(service.narrate(bundle))
        assert result.generated_by == "fallback"
        assert provider.calls == []

    def test_a_valid_response_is_accepted(self, analysis) -> None:
        _, bundle = analysis
        good = build_fallback_report(bundle).model_dump_json()
        service = ReasoningService(_FakeProvider([good]), max_attempts=2)

        result = asyncio.run(service.narrate(bundle))
        assert result.generated_by == "fake"
        assert result.attempts == 1

    def test_a_bad_response_is_retried_then_accepted(self, analysis) -> None:
        _, bundle = analysis
        bad = build_fallback_report(bundle)
        bad.market_assessment.central_estimate = 88_888.0
        good = build_fallback_report(bundle)

        service = ReasoningService(
            _FakeProvider([bad.model_dump_json(), good.model_dump_json()]), max_attempts=2
        )
        result = asyncio.run(service.narrate(bundle))

        assert result.generated_by == "fake"
        assert result.attempts == 2

    def test_markdown_fenced_json_is_tolerated(self, analysis) -> None:
        _, bundle = analysis
        fenced = f"```json\n{build_fallback_report(bundle).model_dump_json()}\n```"
        service = ReasoningService(_FakeProvider([fenced]), max_attempts=1)

        result = asyncio.run(service.narrate(bundle))
        assert result.generated_by == "fake"
