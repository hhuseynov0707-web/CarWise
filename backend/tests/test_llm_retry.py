"""How the reasoning provider answers a refusal.

A rate limit is not a transient blip that goes away if you ask again straight
away. It means the budget is spent, and a prompt large enough to spend it once
spends it again on arrival — so retrying without waiting turns one failed call
into three, and can leave the window worse than it found it. That is what
these pin.

The other half is knowing when not to wait at all. The narrative is the one
part of a report the product can do without, because the figures are computed
either way, so a long wait buys prose at the cost of the answer.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.adapters.llm.base import CompletionRequest, LLMUnavailable
from app.adapters.llm.openai import OpenAIProvider


class _ScriptedClient:
    """Replies from a script, recording how long the caller waited."""

    def __init__(self, statuses: list[int], retry_after: str | None = None) -> None:
        self.statuses = statuses
        self.retry_after = retry_after
        self.calls = 0
        self.gaps: list[float] = []
        self._last: float | None = None

    async def post(self, url: str, json: dict, headers: dict) -> httpx.Response:  # noqa: A002
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._last is not None:
            self.gaps.append(now - self._last)
        self._last = now

        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1

        request = httpx.Request("POST", url)
        headers_out = {}
        if status == 429 and self.retry_after is not None:
            headers_out["Retry-After"] = self.retry_after
        if status == 200:
            body = {
                "choices": [{"message": {"content": '{"final_assessment": "ok"}'}}],
                "usage": {"total_tokens": 10},
            }
            return httpx.Response(200, json=body, request=request, headers=headers_out)
        return httpx.Response(status, text="rate limited", request=request, headers=headers_out)

    async def aclose(self) -> None:
        return None


def _provider(client: _ScriptedClient, attempts: int = 3) -> OpenAIProvider:
    return OpenAIProvider(
        api_key="test-key",
        model="test-model",
        max_attempts=attempts,
        client=client,  # type: ignore[arg-type]
    )


def _request() -> CompletionRequest:
    return CompletionRequest(system="s", user="u")


class TestRateLimitRetries:
    def test_a_retry_waits_instead_of_firing_straight_back(self) -> None:
        client = _ScriptedClient([429, 200])

        async def run():
            return await _provider(client).complete_json(_request())

        asyncio.run(run())

        assert client.calls == 2
        assert client.gaps, "the second attempt was not separated from the first"
        assert client.gaps[0] > 0.5, (
            "a retry sent immediately spends the budget the refusal said was gone"
        )

    def test_the_providers_own_retry_after_is_honoured(self) -> None:
        client = _ScriptedClient([429, 200], retry_after="1")

        async def run():
            return await _provider(client).complete_json(_request())

        asyncio.run(run())
        assert client.gaps[0] == pytest.approx(1.0, abs=0.4)

    def test_a_long_wait_falls_back_rather_than_holding_the_request(self) -> None:
        """Nobody should wait half a minute to be told what the engines already
        computed. The caller's fallback narrative is the better answer."""
        client = _ScriptedClient([429, 200], retry_after="30")

        async def run():
            with pytest.raises(LLMUnavailable, match="30s"):
                await _provider(client).complete_json(_request())

        asyncio.run(run())
        assert client.calls == 1, "it should not have tried again at all"

    def test_exhausting_the_attempts_reports_unavailable(self) -> None:
        client = _ScriptedClient([429])

        async def run():
            with pytest.raises(LLMUnavailable, match="after 2 attempts"):
                await _provider(client, attempts=2).complete_json(_request())

        asyncio.run(run())
        assert client.calls == 2

    def test_a_successful_reply_is_returned_without_waiting(self) -> None:
        client = _ScriptedClient([200])

        async def run():
            return await _provider(client).complete_json(_request())

        result = asyncio.run(run())

        assert client.calls == 1
        assert client.gaps == []
        assert json.loads(result.text)["final_assessment"] == "ok"


class _StatusClient:
    """Answers with one fixed status, and counts the attempts."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.calls = 0

    async def post(self, url: str, json: dict, headers: dict) -> httpx.Response:  # noqa: A002
        self.calls += 1
        return httpx.Response(
            self.status, text="Authorization failed", request=httpx.Request("POST", url)
        )

    async def aclose(self) -> None:
        return None


class TestRejectedCredential:
    """A key the provider will not accept is not a failed analysis.

    It means there is no usable provider, and the caller's answer to that is
    the deterministic narrative — the figures were computed without the model
    either way. The distinction is carried by the exception type, so getting it
    wrong turns a report that would have been delivered into an error.

    401 is the usual status for this. NVIDIA's gateway reserves 401 for a
    missing header and answers a rejected key with 403, which is what made this
    worth pinning rather than assuming.
    """

    @pytest.mark.parametrize("status", [401, 403])
    def test_is_unavailable_rather_than_an_error(self, status: int) -> None:
        client = _StatusClient(status)
        provider = OpenAIProvider(api_key="k", model="m", client=client)  # type: ignore[arg-type]

        with pytest.raises(LLMUnavailable):
            asyncio.run(provider.complete_json(CompletionRequest(system="s", user="u")))

    @pytest.mark.parametrize("status", [401, 403])
    def test_is_not_retried(self, status: int) -> None:
        """Asking again with the same key gets the same refusal."""
        client = _StatusClient(status)
        provider = OpenAIProvider(api_key="k", model="m", client=client)  # type: ignore[arg-type]

        with pytest.raises(LLMUnavailable):
            asyncio.run(provider.complete_json(CompletionRequest(system="s", user="u")))
        assert client.calls == 1
