"""Behavior contracts for first-party HTTPX2 clients and retained Google HTTPX errors."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import openai
import pytest

from doxa_research.errors import ProviderError
from doxa_research.providers.gemini import _follow_dr_redirect, _map_gemini_error
from doxa_research.providers.openai import OpenAIProvider
from doxa_research.providers.perplexity import PerplexityProvider


def test_provider_timeouts_and_raw_perplexity_client_use_httpx2() -> None:
    async def exercise() -> None:
        op = OpenAIProvider("sk-placeholder", {"timeout": 12.0})
        pp = PerplexityProvider("pplx-placeholder", {"timeout": 12.0})
        try:
            assert isinstance(op.client.timeout, httpx2.Timeout)
            assert op.client.timeout.connect == 5.0
            assert isinstance(pp.client.timeout, httpx2.Timeout)
            assert isinstance(pp._async_http, httpx2.AsyncClient)
            assert pp._async_http.timeout.read == 12.0
        finally:
            await op.client.close()
            await pp.client.close()
            await pp._async_http.aclose()

    asyncio.run(exercise())


def test_openai_sdk_sends_and_parses_httpx2_wire_response() -> None:
    async def exercise() -> None:
        requests: list[httpx2.Request] = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(
                200,
                json={
                    "id": "resp_transport",
                    "object": "response",
                    "status": "completed",
                    "output": [],
                },
            )

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
            async with openai.AsyncOpenAI(api_key="sk-placeholder", http_client=http) as sdk:
                response = await sdk.responses.create(model="test-model", input="hello")
                assert response.id == "resp_transport"
        assert len(requests) == 1
        assert requests[0].url.path == "/v1/responses"
        assert requests[0].headers["authorization"] == "Bearer sk-placeholder"

    asyncio.run(exercise())


def test_perplexity_async_wire_keeps_raw_endpoint_payload_and_auth() -> None:
    async def exercise() -> None:
        requests: list[httpx2.Request] = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, json={"id": "job_transport"})

        provider = PerplexityProvider("pplx-placeholder")
        await provider._async_http.aclose()
        provider._async_http = httpx2.AsyncClient(
            base_url="https://api.perplexity.ai",
            headers={"Authorization": "Bearer pplx-placeholder"},
            transport=httpx2.MockTransport(respond),
        )
        try:
            response = await provider._submit_async_with_retry(
                {"request": {"model": "sonar-deep-research", "messages": []}}
            )
            assert response.json()["id"] == "job_transport"
            assert len(requests) == 1
            assert requests[0].url.path == "/v1/async/sonar"
            assert requests[0].headers["authorization"] == "Bearer pplx-placeholder"
            assert b'"model":"sonar-deep-research"' in requests[0].content
        finally:
            await provider.client.close()
            await provider._async_http.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [False, True])
def test_gemini_redirect_uses_httpx2_and_preserves_borrowed_client(failure: bool) -> None:
    async def exercise() -> None:
        redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/test"

        def respond(request: httpx2.Request) -> httpx2.Response:
            if failure:
                raise httpx2.ReadTimeout("timeout", request=request)
            return httpx2.Response(302, headers={"Location": "https://example.com/source"})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            result = await _follow_dr_redirect(redirect, client=client)
            assert result == (None if failure else "https://example.com/source")
            assert not client.is_closed

    asyncio.run(exercise())


def test_gemini_keeps_google_sdk_httpx_error_translation() -> None:
    timeout = _map_gemini_error(httpx.TimeoutException("timeout"), model=None)
    connection = _map_gemini_error(httpx.ConnectError("offline"), model=None)
    assert isinstance(timeout, ProviderError)
    assert "timed out" in timeout.message.lower()
    assert isinstance(connection, ProviderError)
    assert "connection" in connection.message.lower()


def test_httpx2_read_only_cassette_rejects_unrecorded_request(tmp_path: Path) -> None:
    from vcr.errors import CannotOverwriteExistingCassetteException

    from tests.conftest import doxa_vcr

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            await client.get("https://example.invalid/unrecorded")

    with doxa_vcr.use_cassette(str(tmp_path / "empty.yaml")):
        with pytest.raises(CannotOverwriteExistingCassetteException):
            asyncio.run(exercise())


def test_interactive_clarification_retries_sdk_translated_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doxa_research import interactive

    calls: list[object] = []
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="clarified"))]
    )
    create = AsyncMock(
        side_effect=[
            openai.APITimeoutError(request=httpx2.Request("POST", "https://example.com")),
            completion,
        ]
    )

    class Client:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs["timeout"])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(interactive, "AsyncOpenAI", Client)
    session = object.__new__(interactive.InteractiveSession)
    session.config = SimpleNamespace(
        data={"clarification": {"interactive": {"retry_attempts": 2, "retry_delay": 0.0}}}
    )
    session.cli_api_keys = {"openai": "sk-placeholder"}
    session.clarification_session = interactive.ClarificationSession()
    session._log_retry_attempt = lambda _: None
    result = asyncio.run(session._get_clarification_suggestions("query"))
    assert "clarified" in result
    assert create.await_count == 2
    assert all(isinstance(timeout, httpx2.Timeout) for timeout in calls)


@pytest.mark.parametrize("failure", [httpx2.ReadTimeout, httpx2.ConnectError])
def test_clarification_real_sdk_limits_transport_attempts_and_closes_clients(
    monkeypatch: pytest.MonkeyPatch, failure: type[httpx2.RequestError]
) -> None:
    from doxa_research import interactive

    clients: list[httpx2.AsyncClient] = []
    sdk_clients: list[openai.AsyncOpenAI] = []
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise failure("offline failure", request=request)

    def make_sdk(*, api_key: str, timeout: httpx2.Timeout, max_retries: int) -> openai.AsyncOpenAI:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        clients.append(http)
        sdk = openai.AsyncOpenAI(
            http_client=http, api_key=api_key, timeout=timeout, max_retries=max_retries
        )
        sdk_clients.append(sdk)
        return sdk

    monkeypatch.setattr(interactive, "AsyncOpenAI", make_sdk)
    session = object.__new__(interactive.InteractiveSession)
    session.config = SimpleNamespace(
        data={"clarification": {"interactive": {"retry_attempts": 2, "retry_delay": 0.0}}}
    )
    session.cli_api_keys = {"openai": "sk-placeholder"}
    session.clarification_session = interactive.ClarificationSession()
    session._log_retry_attempt = lambda _: None
    with pytest.raises(Exception, match="Failed to get clarification after 2 attempts"):
        asyncio.run(session._get_clarification_suggestions("query"))
    assert len(requests) == 2
    assert all(request.url.path == "/v1/chat/completions" for request in requests)
    assert len(clients) == len(sdk_clients) == 2
    assert all(sdk.max_retries == 0 for sdk in sdk_clients)
    assert all(http.is_closed for http in clients)
