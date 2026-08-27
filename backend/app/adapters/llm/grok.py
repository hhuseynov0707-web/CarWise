"""Grok (xAI) provider.

Speaks the OpenAI-compatible chat-completions shape that the xAI API exposes.
The model identifier is configuration, never a literal in code — model names
change, and pinning one in a source file guarantees a future outage.

This class knows nothing about vehicles. It sends text and returns text. All
vehicle reasoning lives in the prompt and all verification lives in
``validation.py``, so swapping this file for another provider changes nothing
else.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.adapters.llm.base import (
    CompletionRequest,
    CompletionResponse,
    LLMError,
    LLMUnavailable,
)

DEFAULT_BASE_URL = "https://api.x.ai/v1"

#: Statuses worth retrying: transient server problems and rate limiting.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class GrokProvider:
    """Chat-completions client for the xAI API."""

    name = "grok"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailable("no API key configured for the Grok provider")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "AutoIntel/0.1 (+https://autointel.az)"},
        )

    async def complete_json(self, request: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": {"type": "json_object"},
        }

        started = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    raise LLMUnavailable(f"Grok request timed out after {attempt} attempts") from exc
                continue
            except httpx.HTTPError as exc:
                raise LLMUnavailable(f"Grok request failed: {exc}") from exc

            if response.status_code in _RETRYABLE_STATUS:
                last_error = LLMError(f"Grok returned {response.status_code}")
                if attempt == self._max_attempts:
                    raise LLMUnavailable(
                        f"Grok unavailable after {attempt} attempts "
                        f"(last status {response.status_code})"
                    )
                continue

            if response.status_code == 401:
                raise LLMUnavailable("Grok rejected the API key")
            if response.status_code >= 400:
                raise LLMError(
                    f"Grok returned {response.status_code}: {response.text[:400]}"
                )

            return self._parse(response, started)

        raise LLMUnavailable(f"Grok request failed: {last_error}")

    def _parse(self, response: httpx.Response, started: float) -> CompletionResponse:
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("Grok returned a non-JSON envelope") from exc

        choices = body.get("choices") or []
        if not choices:
            raise LLMError("Grok returned no choices")

        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise LLMError("Grok returned an empty completion")

        usage = body.get("usage") or {}
        return CompletionResponse(
            text=content,
            model=body.get("model", self._model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
