"""Language-model provider abstraction.

Spec §72 requires the LLM provider to be replaceable without rewriting the
application. Nothing outside this package may import a concrete provider; the
rest of the system depends on :class:`LLMProvider` and receives an
implementation through configuration.

The interface is deliberately minimal — text in, JSON text out. Anything richer
(tool calling, streaming, provider-specific structured-output modes) would leak
one vendor's shape into the contract and make the next one harder to adopt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Any failure to obtain a usable completion."""


class LLMUnavailable(LLMError):
    """The provider could not be reached, or is not configured.

    Distinct from :class:`LLMError` because it is not a failure of the analysis:
    the caller falls back to the deterministic narrative and the report is still
    delivered in full.
    """


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    system: str
    user: str
    max_tokens: int = 3000
    """Reserved for the completion.

    Providers charge this against a per-minute budget before a token is
    generated, so an over-generous reservation costs whether or not it is used.
    A finished narrative measures well under 1,500 tokens, and the reservation
    was 4,000 — most of a small tier's entire per-minute allowance spent on
    headroom that is never reached."""
    temperature: float = 0.2
    """Low by default. This layer explains computed evidence; creative variance
    is not a feature here."""


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal contract every reasoning provider must satisfy."""

    name: str

    async def complete_json(self, request: CompletionRequest) -> CompletionResponse:
        """Return a JSON document as text. Must raise :class:`LLMError` on failure."""
        ...

    async def close(self) -> None:
        """Release any held resources."""
        ...
