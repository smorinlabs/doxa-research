"""Agent API wire contracts, including durable checkpoint identity and safe failures."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx2
import pytest
from click.testing import CliRunner

from doxa_research.cli import cli
from doxa_research.errors import DoxaError, ProviderError
from doxa_research.providers.perplexity import PerplexityProvider


def _payload(status: str = "queued", **extra: Any) -> dict[str, Any]:
    return {
        "id": "opaque-id",
        "object": "response",
        "created_at": 1,
        "model": "test/model",
        "status": status,
        "output": (
            [
                {
                    "type": "message",
                    "id": "msg-id",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Answer."}],
                }
            ]
            if status == "completed"
            else []
        ),
        **extra,
    }


@asynccontextmanager
async def _provider(handler: Any, config: dict[str, Any] | None = None):
    provider = PerplexityProvider(
        "offline-key", {"model": "sonar-deep-research", "kind": "background", **(config or {})}
    )
    await provider._async_http.aclose()
    provider._async_http = httpx2.AsyncClient(
        base_url="https://api.perplexity.ai",
        headers={"Authorization": "Bearer offline-key"},
        transport=httpx2.MockTransport(handler),
    )
    try:
        yield provider
    finally:
        await provider._async_http.aclose()
        await provider.client.close()


def test_agent_create_payload_and_checkpoint_identity() -> None:
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json=_payload())

    async def run():
        async with _provider(respond) as provider:
            job = await provider.submit("research query", "perplexity_deep_research", "Be precise")
            assert job == "agent:opaque-id"
            assert provider.jobs[job]["response_data"]["id"] == "opaque-id"

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/agent"
    assert requests[0].headers["authorization"] == "Bearer offline-key"
    assert json.loads(requests[0].content) == {
        "input": "research query",
        "instructions": "Be precise",
        "preset": "high",
        "background": True,
        "store": True,
        "stream": False,
    }


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError,
        httpx2.ReadTimeout,
        httpx2.ReadError,
        httpx2.WriteError,
        httpx2.RemoteProtocolError,
    ],
)
def test_ambiguous_agent_submission_is_never_retried(failure) -> None:
    requests = []

    def respond(request):
        requests.append(request)
        raise failure("ambiguous creation", request=request)

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(ProviderError):
                await provider.submit("query", "perplexity_deep_research")

    asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize(
    "upstream,expected",
    [
        ("queued", "queued"),
        ("in_progress", "running"),
        ("completed", "completed"),
        ("failed", "permanent_error"),
        ("incomplete", "permanent_error"),
        ("cancelled", "cancelled"),
    ],
)
def test_agent_status_translation(upstream, expected) -> None:
    def respond(request):
        assert request.method == "GET" and request.url.path == "/v1/agent/opaque-id"
        return httpx2.Response(200, json=_payload(upstream, error={"message": "provider detail"}))

    async def run():
        async with _provider(respond) as provider:
            await provider.reconnect("agent:opaque-id")
            result = await provider.check_status("agent:opaque-id")
            assert result["status"] == expected
            if expected == "permanent_error":
                assert "provider detail" in result["error"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad",
    [
        _payload("future_status"),
        _payload(id="wrong-id"),
        [],
        _payload(output={}),
        _payload(id=None),
    ],
)
def test_agent_malformed_poll_fails_permanently_without_caching(bad) -> None:
    def respond(request):
        return httpx2.Response(200, json=bad)

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {"background": True, "response_data": _payload()}
            assert (await provider.check_status("agent:opaque-id"))["status"] == "permanent_error"
            assert provider.jobs["agent:opaque-id"]["response_data"] == _payload()

    asyncio.run(run())


def test_agent_typed_result_excludes_reasoning_and_renders_sources_cost() -> None:
    payload = _payload(
        "completed",
        output=[
            {
                "type": "reasoning",
                "content": [{"type": "output_text", "text": "PRIVATE_REASONING"}],
            },
            {
                "type": "message",
                "id": "msg-id",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Answer.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.org",
                                "title": "Example",
                            }
                        ],
                    }
                ],
            },
            {
                "type": "search_results",
                "results": [{"url": "https://example.org", "title": "Example"}],
            },
        ],
        usage={"cost": {"total_cost": 0.1234}},
    )

    def respond(request):
        return httpx2.Response(200, json=payload)

    async def run():
        async with _provider(respond) as provider:
            await provider.reconnect("agent:opaque-id")
            result = await provider.get_result("agent:opaque-id")
            assert result.startswith("Answer.") and "PRIVATE_REASONING" not in result
            assert result.count("https://example.org") == 1
            assert "## Sources" in result and "$0.1234" in result

    asyncio.run(run())


@pytest.mark.parametrize("output", [[], [{"type": "reasoning", "text": "not an answer"}]])
def test_completed_agent_without_assistant_answer_is_actionable(output) -> None:
    def respond(request):
        return httpx2.Response(200, json=_payload("completed", output=output))

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(ProviderError, match="answer"):
                await provider.reconnect("agent:opaque-id")
                await provider.get_result("agent:opaque-id")

    asyncio.run(run())


@pytest.mark.parametrize("method", ["reconnect", "cancel"])
@pytest.mark.parametrize("job_id", ["legacy-sonar-request", "agent:", "agent:bad\nvalue"])
def test_legacy_or_malformed_ids_never_call_http(method, job_id) -> None:
    def respond(request):
        raise AssertionError("Legacy checkpoint must never reach HTTP")

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(ProviderError):
                await getattr(provider, method)(job_id)

    asyncio.run(run())


def test_agent_cancel_acknowledgment_is_pending_and_id_checked() -> None:
    def respond(request):
        assert request.method == "POST" and request.url.path == "/v1/agent/opaque-id/cancel"
        return httpx2.Response(200, json={"response_id": "opaque-id", "status": "cancelling"})

    async def run():
        async with _provider(respond) as provider:
            assert (await provider.cancel("agent:opaque-id"))["status"] == "cancelling"

    asyncio.run(run())


@pytest.mark.parametrize(
    "ack",
    [
        {},
        {"response_id": "wrong", "status": "cancelling"},
        {"response_id": "opaque-id", "status": "unknown"},
    ],
)
def test_invalid_cancel_ack_is_not_reported_as_cancelled(ack) -> None:
    def respond(request):
        return httpx2.Response(200, json=ack)

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(ProviderError):
                await provider.cancel("agent:opaque-id")

    asyncio.run(run())


def test_agent_maps_legacy_known_request_fields_and_rejects_unsupported() -> None:
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=_payload())

    async def run():
        async with _provider(
            respond, {"max_tokens": 512, "perplexity": {"reasoning_effort": "low", "max_steps": 3}}
        ) as provider:
            await provider.submit("query", "perplexity_deep_research")
        async with _provider(respond, {"perplexity": {"stream_mode": "concise"}}) as provider:
            with pytest.raises(ProviderError, match="stream_mode"):
                await provider.submit("query", "perplexity_deep_research")

    asyncio.run(run())
    assert bodies == [
        {
            "input": "query",
            "preset": "high",
            "background": True,
            "store": True,
            "stream": False,
            "max_output_tokens": 512,
            "max_steps": 3,
            "reasoning": {"effort": "low"},
        }
    ]


def test_cli_cancel_renders_pending_without_claiming_upstream_completion(monkeypatch) -> None:
    from doxa_research import commands

    async def cancel(*args, **kwargs):
        return {
            "status": "ok",
            "operation_id": "op",
            "providers": {"perplexity": {"status": "cancelling"}},
        }

    monkeypatch.setattr(commands, "cancel_operation", cancel)
    result = CliRunner().invoke(cli, ["cancel", "op"])
    assert result.exit_code == 0
    assert "cancellation requested" in result.output
    assert "cancelled upstream" not in result.output and "✗" not in result.output


@pytest.mark.parametrize("status", [400, 401, 402, 403, 429, 500, 503])
def test_agent_http_creation_errors_make_exactly_one_post(status) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(status, json={"error": {"message": "rejected"}})

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(DoxaError):
                await provider.submit("query", "perplexity_deep_research")

    asyncio.run(run())
    assert len(calls) == 1 and calls[0].method == "POST"


@pytest.mark.parametrize("wire_id", ["opaque/id", "opaque?query#part", "percent%2Fencoded"])
def test_agent_opaque_id_is_encoded_as_one_wire_segment(wire_id) -> None:
    from urllib.parse import quote

    def respond(request):
        assert request.url.raw_path == ("/v1/agent/" + quote(wire_id, safe="")).encode()
        return httpx2.Response(200, json=_payload(id=wire_id))

    async def run():
        async with _provider(respond) as provider:
            await provider.reconnect("agent:" + wire_id)

    asyncio.run(run())


@pytest.mark.parametrize("wire_id", [".", ".."])
def test_agent_dot_segments_never_reach_http(wire_id) -> None:
    def respond(request):
        raise AssertionError("unsafe URL traversal")

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(ProviderError):
                await provider.reconnect("agent:" + wire_id)

    asyncio.run(run())


def test_agent_valid_create_id_survives_invalid_initial_status() -> None:
    def respond(request):
        return httpx2.Response(200, json=_payload("future_status"))

    async def run():
        async with _provider(respond) as provider:
            job = await provider.submit("query", "perplexity_deep_research")
            assert job == "agent:opaque-id"
            assert (await provider.check_status(job))["status"] == "permanent_error"
            assert job in provider.jobs

    asyncio.run(run())


def test_agent_mapped_search_overrides_do_not_add_other_preset_tools() -> None:
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=_payload())

    async def run():
        async with _provider(
            respond,
            {
                "perplexity": {
                    "preset": "fast",
                    "search_domain_filter": ["example.org"],
                    "web_search_options": {"search_context_size": "low"},
                }
            },
        ) as provider:
            await provider.submit("query", "perplexity_deep_research")

    asyncio.run(run())
    assert bodies[0]["tools"] == [
        {
            "type": "web_search",
            "search_context_size": "low",
            "filters": {"search_domain_filter": ["example.org"]},
        }
    ]


def test_agent_cancel_terminal_race_is_verified_by_get() -> None:
    calls = []

    def respond(request):
        calls.append(request.method)
        return (
            httpx2.Response(400, json={})
            if request.method == "POST"
            else httpx2.Response(200, json=_payload("completed"))
        )

    async def run():
        async with _provider(respond) as provider:
            assert (await provider.cancel("agent:opaque-id"))["status"] == "completed"

    asyncio.run(run())
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_agent_transient_poll_cannot_complete_unvalidated_create_payload(status) -> None:
    def respond(request):
        raise httpx2.ReadTimeout("timeout", request=request)

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {
                "background": True,
                "validated": False,
                "response_data": _payload("completed"),
            }
            assert (await provider.check_status("agent:opaque-id"))["status"] == "transient_error"

    asyncio.run(run())


def test_agent_result_returns_final_completed_assistant_message_only() -> None:
    def message(text, **extra):
        return {
            "type": "message",
            "id": "msg",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
            **extra,
        }

    def respond(request):
        return httpx2.Response(
            200,
            json=_payload(
                "completed",
                output=[
                    message("Earlier commentary"),
                    message("Explicit commentary", phase="commentary"),
                    message("Final answer."),
                ],
            ),
        )

    async def run():
        async with _provider(respond) as provider:
            await provider.reconnect("agent:opaque-id")
            assert await provider.get_result("agent:opaque-id") == "Final answer."
            assert provider.model == "test/model"

    asyncio.run(run())


@pytest.mark.parametrize(
    "message_status,text", [("in_progress", "unfinished"), ("completed", "  ")]
)
def test_completed_agent_cannot_accept_unfinished_or_empty_message(message_status, text) -> None:
    def respond(request):
        return httpx2.Response(
            200,
            json=_payload(
                "completed",
                output=[
                    {
                        "type": "message",
                        "id": "msg",
                        "status": message_status,
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
            ),
        )

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {"background": True, "response_data": _payload()}
            assert (await provider.check_status("agent:opaque-id"))["status"] == "permanent_error"

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,cached,expected",
    [
        (500, "completed", "completed"),
        (503, "completed", "completed"),
        (429, "completed", "completed"),
        (500, "queued", "transient_error"),
        (429, "in_progress", "transient_error"),
        (400, "completed", "permanent_error"),
        (401, "queued", "permanent_error"),
        (402, "queued", "permanent_error"),
        (403, "queued", "permanent_error"),
        (422, "queued", "permanent_error"),
    ],
)
def test_agent_poll_retains_retryable_cache_and_permanent_http_taxonomy(
    status, cached, expected
) -> None:
    def respond(request):
        return httpx2.Response(status, json={"error": {"message": "provider error"}})

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {
                "background": True,
                "validated": True,
                "response_data": _payload(cached),
            }
            assert (await provider.check_status("agent:opaque-id"))["status"] == expected

    asyncio.run(run())


@pytest.mark.parametrize(
    "cached,expected",
    [("completed", "completed"), ("queued", "transient_error"), ("in_progress", "transient_error")],
)
@pytest.mark.parametrize(
    "failure",
    [httpx2.ConnectError, httpx2.ReadError, httpx2.WriteError, httpx2.RemoteProtocolError],
)
def test_agent_network_poll_fallback_requires_validated_completion(
    cached, expected, failure
) -> None:
    def respond(request):
        raise failure("offline", request=request)

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {
                "background": True,
                "validated": True,
                "response_data": _payload(cached),
            }
            assert (await provider.check_status("agent:opaque-id"))["status"] == expected

    asyncio.run(run())


def test_agent_quota_429_remains_permanent_even_with_completed_cache() -> None:
    def respond(request):
        return httpx2.Response(
            429, json={"error": {"code": "insufficient_quota", "message": "no credits"}}
        )

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {
                "background": True,
                "validated": True,
                "response_data": _payload("completed"),
            }
            assert (await provider.check_status("agent:opaque-id"))["status"] == "permanent_error"

    asyncio.run(run())


def test_agent_validated_completed_result_does_not_refetch() -> None:
    def respond(request):
        raise AssertionError("cached result must not make HTTP request")

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {
                "background": True,
                "validated": True,
                "response_data": _payload("completed"),
            }
            assert await provider.get_result("agent:opaque-id") == "Answer."

    asyncio.run(run())


@pytest.mark.parametrize(
    "unsupported",
    [
        {"stream_mode": "full"},
        {"response_format": {"type": "regex"}},
        {"input": "override"},
        {"background": False},
        {"store": False},
        {"stream": True},
        {"stop": ["x"]},
    ],
)
def test_agent_rejects_legacy_or_structural_options_before_creation(unsupported) -> None:
    def respond(request):
        raise AssertionError("unsupported config must fail before HTTP")

    async def run():
        async with _provider(respond, {"perplexity": unsupported}) as provider:
            with pytest.raises(ProviderError):
                await provider.submit("query", "perplexity_deep_research")

    asyncio.run(run())


@pytest.mark.parametrize("key", ["stop", "stop_sequences"])
def test_agent_rejects_flat_legacy_stop_fields_before_creation(key) -> None:
    def respond(request):
        raise AssertionError("unsupported flat config must fail before HTTP")

    async def run():
        async with _provider(respond, {key: ["x"]}) as provider:
            with pytest.raises(ProviderError):
                await provider.submit("query", "perplexity_deep_research")

    asyncio.run(run())


def test_builtin_all_deep_research_factory_uses_agent_without_other_providers(tmp_path):
    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = ConfigManager(config_path=tmp_path / "missing.toml")
    config.load_all_layers({})
    provider = cast(
        PerplexityProvider,
        create_provider(
            "perplexity",
            config,
            cli_api_key="offline-key",
            mode_config=config.get_mode_config("all_deep_research"),
        ),
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json=_payload())

    async def run():
        await provider._async_http.aclose()
        provider._async_http = httpx2.AsyncClient(
            base_url="https://api.perplexity.ai", transport=httpx2.MockTransport(respond)
        )
        try:
            assert await provider.submit("query", "all_deep_research") == "agent:opaque-id"
        finally:
            await provider._async_http.aclose()
            await provider.client.close()

    asyncio.run(run())
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["preset"] == "high"
    assert "model" not in body
    assert "reasoning" not in body


@pytest.mark.parametrize("method", ["check_status", "reconnect", "cancel"])
def test_agent_missing_stored_id_is_actionable_and_permanent(method):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(404, json={"error": {"message": "missing"}})

    async def run():
        async with _provider(respond) as provider:
            if method == "check_status":
                provider.jobs["agent:opaque-id"] = {
                    "background": True,
                    "validated": False,
                    "response_data": _payload(),
                }
                result = await provider.check_status("agent:opaque-id")
                assert result["status"] == "permanent_error"
                assert "account" in result["error"] and "stored" in result["error"]
            else:
                with pytest.raises(ProviderError, match="account|stored"):
                    await getattr(provider, method)("agent:opaque-id")

    asyncio.run(run())
    assert len(calls) == 1 and calls[0].method == ("POST" if method == "cancel" else "GET")


def test_unknown_local_agent_status_and_result_do_not_request_http():
    calls = []

    def respond(request):
        calls.append(request)
        raise AssertionError("unexpected HTTP")

    async def run():
        async with _provider(respond) as provider:
            assert (await provider.check_status("agent:missing"))["status"] == "not_found"
            with pytest.raises(ProviderError):
                await provider.get_result("agent:missing")

    asyncio.run(run())
    assert calls == []


def test_agent_queued_result_refreshes_once_before_rendering():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=_payload("completed"))

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["agent:opaque-id"] = {
                "background": True,
                "validated": True,
                "response_data": _payload(),
            }
            assert "Answer." in await provider.get_result("agent:opaque-id")

    asyncio.run(run())
    assert len(calls) == 1 and calls[0].method == "GET"


def test_immediate_cached_sdk_result_and_status_never_use_agent_http():
    from tests.test_provider_perplexity import _stub_response

    calls = []

    def respond(request):
        calls.append(request)
        raise AssertionError("unexpected Agent HTTP")

    async def run():
        async with _provider(respond) as provider:
            provider.jobs["sync-id"] = {"response": _stub_response("Immediate answer")}
            assert (await provider.check_status("sync-id"))["status"] == "completed"
            assert "Immediate answer" in await provider.get_result("sync-id")

    asyncio.run(run())
    assert calls == []


@pytest.mark.parametrize("body", [{}, {"id": ""}, {"id": ".."}, {"id": 12}, []])
def test_unidentified_agent_creation_never_retries_and_reports_uncertainty(body):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json=body)

    async def run():
        async with _provider(respond) as provider:
            with pytest.raises(ProviderError, match="no usable ID; no retry") as error:
                await provider.submit("query", "perplexity_deep_research")
            assert "dashboard" in (error.value.suggestion or "")
            assert not provider.jobs

    asyncio.run(run())
    assert len(requests) == 1
