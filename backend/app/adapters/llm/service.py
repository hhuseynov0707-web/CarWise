"""Reasoning service: prompt, call, validate, retry, or fall back.

This is the only place the rest of the application touches a language model, and
it enforces the two invariants from audit §5:

* no unvalidated model output reaches the frontend, and
* the report is always produced, model or no model.

The retry loop feeds the specific validation failures back to the model rather
than resampling blindly. A model told "you reported a central estimate of 46,200
but the computed value is 44,279" corrects reliably; the same model asked again
with an identical prompt usually makes the same mistake.

After the retry budget is spent, the deterministic narrative is used. A plainer
report is a far better outcome than a fluent one containing an invented number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.adapters.llm.base import (
    CompletionRequest,
    LLMError,
    LLMProvider,
    LLMUnavailable,
)
from app.adapters.llm.fallback import build_fallback_report
from app.adapters.llm.prompt import SYSTEM_PROMPT, build_retry_message, build_user_message
from app.adapters.llm.schema import VehicleReport
from app.adapters.llm.validation import ValidationResult, validate_report
from app.engines.evidence.bundle import numeric_registry


@dataclass(frozen=True, slots=True)
class NarrativeResult:
    """The narrative plus an honest account of how it was produced."""

    report: VehicleReport
    generated_by: str
    """``"openai"``, ``"fallback"`` — surfaced in the API so the client can label
    an AI-written narrative as such."""

    attempts: int = 0
    validation_failures: tuple[str, ...] = ()
    model: str | None = None
    latency_ms: int | None = None
    degraded_reason: str | None = None

    @property
    def is_ai_generated(self) -> bool:
        return self.generated_by != "fallback"


class ReasoningService:
    """Turns an evidence bundle into a validated narrative."""

    def __init__(
        self,
        provider: LLMProvider | None,
        max_attempts: int = 2,
        enabled: bool = True,
    ) -> None:
        self._provider = provider
        self._max_attempts = max(1, max_attempts)
        self._enabled = enabled

    async def narrate(self, bundle: dict[str, Any], language: str = "en") -> NarrativeResult:
        if not self._enabled or self._provider is None:
            return NarrativeResult(
                report=build_fallback_report(bundle),
                generated_by="fallback",
                degraded_reason=(
                    "The reasoning layer is disabled; this report was generated directly "
                    "from the computed evidence."
                ),
            )

        registry = numeric_registry(bundle)
        failures: list[str] = []
        user_message = build_user_message(bundle, language)

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._provider.complete_json(
                    CompletionRequest(system=SYSTEM_PROMPT, user=user_message)
                )
            except LLMUnavailable as exc:
                return self._degrade(bundle, attempt, failures, f"provider unavailable: {exc}")
            except LLMError as exc:
                failures.append(f"provider error: {exc}")
                if attempt == self._max_attempts:
                    return self._degrade(bundle, attempt, failures, str(exc))
                continue

            parsed, parse_error = _parse_report(response.text)
            if parsed is None:
                failures.append(f"schema violation: {parse_error}")
                if attempt == self._max_attempts:
                    return self._degrade(bundle, attempt, failures, parse_error)
                user_message = build_retry_message(
                    bundle,
                    f"The previous response did not match the required schema: {parse_error}",
                    language,
                )
                continue

            validation: ValidationResult = validate_report(parsed, bundle, registry)
            if validation.ok:
                return NarrativeResult(
                    report=parsed,
                    generated_by=self._provider.name,
                    attempts=attempt,
                    validation_failures=tuple(failures),
                    model=response.model,
                    latency_ms=response.latency_ms,
                )

            failures.extend(str(f) for f in validation.findings)
            if attempt == self._max_attempts:
                return self._degrade(
                    bundle,
                    attempt,
                    failures,
                    f"{len(validation.findings)} validation failure(s) on the final attempt",
                )
            user_message = build_retry_message(bundle, validation.feedback(), language)

        return self._degrade(bundle, self._max_attempts, failures, "retry budget exhausted")

    def _degrade(
        self,
        bundle: dict[str, Any],
        attempts: int,
        failures: list[str],
        reason: str,
    ) -> NarrativeResult:
        return NarrativeResult(
            report=build_fallback_report(bundle),
            generated_by="fallback",
            attempts=attempts,
            validation_failures=tuple(failures),
            degraded_reason=(
                f"The AI narrative could not be verified against the evidence ({reason}); "
                f"this report was generated directly from the computed analysis instead. "
                f"All figures and findings are unaffected."
            ),
        )

    async def close(self) -> None:
        if self._provider is not None:
            await self._provider.close()


def _parse_report(text: str) -> tuple[VehicleReport | None, str]:
    """Parse and schema-validate a model response.

    Tolerates a model wrapping its JSON in a markdown fence, which is a common
    and harmless deviation, but nothing beyond that — anything looser and we
    would be guessing at intent.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, f"response was not valid JSON ({exc.msg} at position {exc.pos})"

    try:
        return VehicleReport.model_validate(payload), ""
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        return None, problems
