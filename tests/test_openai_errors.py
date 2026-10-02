"""Tests for typed OpenAI SDK exception mapping in OpenAIProvider.submit.

Follows the `asyncio.run(coro)` sync-wrap pattern from tests/test_vcr_openai.py
(no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx2
import openai
import pytest

from doxa_research.__main__ import (
    APIKeyError,
    APIQuotaError,
    APIRateLimitError,
    DoxaError,
    OpenAIProvider,
    ProviderError,
    _map_openai_error,
)


def _fake_sdk_error(
    exc_class: type[openai.APIError],
    *,
    message: str = "boom",
    status: int = 400,
    body: Any = None,
) -> openai.APIError:
    """Construct a fake openai SDK exception with minimal httpx2.Response."""
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    if exc_class is openai.APITimeoutError:
        return exc_class(request=request)
    if exc_class is openai.APIConnectionError:
        return exc_class(message=message, request=request)
    if exc_class is openai.APIError:
        return exc_class(message=message, request=request, body=body)
    response = httpx2.Response(status_code=status, request=request)
    return exc_class(message=message, response=response, body=body)  # ty: ignore[missing-argument,unknown-argument]


def _make_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key", config={"model": "o3-deep-research"})


def _submit_raises(provider: OpenAIProvider, sdk_exc: BaseException) -> BaseException:
    """Invoke submit with responses.create patched to raise sdk_exc, return mapped exception."""
    with patch.object(provider.client.responses, "create", new=AsyncMock(side_effect=sdk_exc)):
        try:
            asyncio.run(provider.submit("test prompt", mode="default"))
        except BaseException as e:  # noqa: BLE001
            return e
    raise AssertionError("submit did not raise")


class TestMapOpenAIError:
    """Direct unit tests on _map_openai_error."""

    def test_authentication_error_maps_to_api_key_error(self) -> None:
        exc = _fake_sdk_error(openai.AuthenticationError, message="bad key", status=401)
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, APIKeyError)
        assert "openai API key not found" in result.message

    def test_rate_limit_error_without_quota_marker_maps_to_provider_error(self) -> None:
        exc = _fake_sdk_error(
            openai.RateLimitError, message="slow down", status=429, body={"error": {}}
        )
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, APIRateLimitError)
        assert not isinstance(result, APIQuotaError)
        assert "rate limit" in result.message.lower()

    def test_rate_limit_error_with_insufficient_quota_maps_to_api_quota_error(
        self,
    ) -> None:
        exc = _fake_sdk_error(
            openai.RateLimitError,
            message="quota",
            status=429,
            body={"error": {"code": "insufficient_quota"}},
        )
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, APIQuotaError)

    def test_rate_limit_error_with_quota_message_maps_to_api_quota_error(self) -> None:
        exc = _fake_sdk_error(
            openai.RateLimitError,
            message="quota",
            status=429,
            body={
                "error": {
                    "message": "You exceeded your current quota, please check billing.",
                    "type": "insufficient_quota",
                }
            },
        )
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, APIQuotaError)

    def test_not_found_error_maps_to_provider_error_with_model(self) -> None:
        exc = _fake_sdk_error(openai.NotFoundError, message="no such model", status=404)
        result = _map_openai_error(exc, model="o3-deep-research")
        assert isinstance(result, ProviderError)
        assert "o3-deep-research" in result.message

    def test_bad_request_temperature_keeps_guidance(self) -> None:
        exc = _fake_sdk_error(
            openai.BadRequestError,
            message="Unsupported parameter: 'temperature' for o3-deep-research",
            status=400,
        )
        result = _map_openai_error(exc, model="o3-deep-research")
        assert isinstance(result, ProviderError)
        assert "temperature" in result.message.lower()
        assert "o3-deep-research" in result.message

    def test_bad_request_generic(self) -> None:
        exc = _fake_sdk_error(openai.BadRequestError, message="bad request", status=400)
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, ProviderError)

    def test_permission_denied_maps_to_provider_error(self) -> None:
        exc = _fake_sdk_error(openai.PermissionDeniedError, message="nope", status=403)
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, ProviderError)

    def test_internal_server_error_maps_to_provider_error(self) -> None:
        exc = _fake_sdk_error(openai.InternalServerError, message="5xx", status=500)
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, ProviderError)

    def test_api_connection_error_maps_to_provider_error(self) -> None:
        exc = _fake_sdk_error(openai.APIConnectionError, message="can't connect")
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, ProviderError)
        assert "connect" in result.message.lower()

    def test_api_timeout_error_maps_to_provider_error_timeout_message(self) -> None:
        exc = _fake_sdk_error(openai.APITimeoutError)
        result = _map_openai_error(exc, model="o3")
        assert isinstance(result, ProviderError)
        assert "timed out" in result.message.lower()

    def test_unknown_exception_falls_through_to_provider_error(self) -> None:
        result = _map_openai_error(Exception("weird"), model="o3")
        assert isinstance(result, ProviderError)
        assert isinstance(result, DoxaError)


class TestSubmitRaisesMappedErrors:
    """End-to-end: submit() catches typed SDK errors and raises mapped DoxaError."""

    def test_authentication_error_bubbles_as_api_key_error(self) -> None:
        provider = _make_provider()
        exc = _fake_sdk_error(openai.AuthenticationError, message="bad key", status=401)
        mapped = _submit_raises(provider, exc)
        assert isinstance(mapped, APIKeyError)

    def test_timeout_retries_then_maps_to_provider_error(self) -> None:
        provider = _make_provider()
        exc = _fake_sdk_error(openai.APITimeoutError)
        mock = AsyncMock(side_effect=exc)
        with patch.object(provider.client.responses, "create", new=mock):
            with pytest.raises(ProviderError) as info:
                asyncio.run(provider.submit("p", mode="default"))
        assert "timed out" in info.value.message.lower()
        assert mock.call_count == 3, (
            f"expected tenacity to retry 3x on APITimeoutError, got {mock.call_count}"
        )


class TestVCRHappyPathDoesNotInvokeMapper:
    """VCR replay must not hit _map_openai_error on the success path."""

    def test_happy_path_does_not_call_map_openai_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from doxa_research import __main__ as doxa_main

        def _fail(*a, **kw):
            raise AssertionError("_map_openai_error called on happy path")

        monkeypatch.setattr(doxa_main, "_map_openai_error", _fail)

        provider = _make_provider()

        class _Resp:
            id = "resp_happy"
            status = "in_progress"

        with patch.object(provider.client.responses, "create", new=AsyncMock(return_value=_Resp())):
            job_id = asyncio.run(provider.submit("p", mode="default"))
        assert job_id == "resp_happy"


def test_openai_constants_use_suffix_naming() -> None:
    """OpenAI module-level constants follow the cross-provider suffix convention."""
    from doxa_research.providers import openai as op

    assert hasattr(op, "_DIRECT_SDK_KEYS_OPENAI"), (
        "_DIRECT_SDK_KEYS_OPENAI must exist (introduced for cross-provider parity)"
    )
    assert hasattr(op, "_PROVIDER_NAME_OPENAI"), (
        "_PROVIDER_NAME_OPENAI must exist (introduced for cross-provider parity)"
    )
    assert op._PROVIDER_NAME_OPENAI == "openai"
    # The Responses API kwargs the immediate path passes:
    assert "temperature" in op._DIRECT_SDK_KEYS_OPENAI
    assert "max_tool_calls" in op._DIRECT_SDK_KEYS_OPENAI
    assert "tools" in op._DIRECT_SDK_KEYS_OPENAI


def test_openai_sources_block_escapes_html_in_title() -> None:
    """OpenAI's ## Sources block must use md_link_title to escape HTML in titles."""
    from types import SimpleNamespace

    from doxa_research.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="dummy", config={})
    fake_response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                status="completed",
                phase="final_answer",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="Answer body.",
                        annotations=[
                            {"url": "https://example.com", "title": "<script>alert(1)</script>"},
                        ],
                    )
                ],
            )
        ],
    )
    provider.jobs["test"] = {"response": fake_response, "background": False, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    assert "<script>" not in rendered, (
        "raw HTML in title leaked into output (md_link_title not applied)"
    )


def test_openai_sources_block_blocks_javascript_scheme_in_url() -> None:
    """OpenAI's ## Sources block must use md_link_url to neutralize javascript: URLs."""
    from types import SimpleNamespace

    from doxa_research.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="dummy", config={})
    fake_response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                status="completed",
                phase="final_answer",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="Answer.",
                        annotations=[
                            {"url": "javascript:alert(1)", "title": "Click me"},
                        ],
                    )
                ],
            )
        ],
    )
    provider.jobs["test"] = {"response": fake_response, "background": False, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    assert "javascript:" not in rendered, (
        "javascript: scheme not neutralized (md_link_url not applied)"
    )


def test_openai_invalid_key_doxaerror_has_exit_code_2() -> None:
    """OpenAI's invalid-key DoxaError must set exit_code=2 to match Perplexity.

    The shared `_invalid_key_doxaerror` helper sets exit_code=2 so that
    callers can distinguish 'configured but rejected' (rotate the key) from
    other DoxaError exits. OpenAI previously drifted by leaving the default
    exit_code=1; this test pins the parity contract.
    """
    # 'Incorrect API key' phrase triggers the invalid-key (vs missing-key) branch.
    exc = _fake_sdk_error(
        openai.AuthenticationError,
        message="Incorrect API key provided",
        status=401,
        body={"error": {"code": "invalid_api_key", "message": "Incorrect API key provided"}},
    )
    mapped = _map_openai_error(exc, model="gpt-4o", verbose=False)
    assert isinstance(mapped, DoxaError)
    assert not isinstance(mapped, APIKeyError), (
        "A configured-but-invalid key should not report 'not found'"
    )
    assert mapped.exit_code == 2, (
        f"expected exit_code=2 (Perplexity parity), got {mapped.exit_code}"
    )


# ---------------------------------------------------------------------------
# P24 Task 6.1: OpenAI Responses streaming events — four-kind parity.
#
# Audit (planning/p24-openai-stream-audit.v1.md, outcome SHIP) confirmed the
# Responses API SDK emits both `response.reasoning_summary_text.delta` and
# `response.output_text.annotation.added` events. These tests pin
# OpenAIProvider.stream() to translate them into StreamEvent reasoning/citation
# events, matching Perplexity and Gemini's four-kind stream contract.
# ---------------------------------------------------------------------------


class _FakeStreamEvent:
    """Minimal stand-in for an SDK ResponseStreamEvent (attribute access only)."""

    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


class _FakeURLAnnotation:
    """Stand-in for AnnotationURLCitation (pydantic-attr access)."""

    def __init__(self, *, url: str, title: str) -> None:
        self.type = "url_citation"
        self.url = url
        self.title = title


class _FakeFileAnnotation:
    """Stand-in for AnnotationFileCitation — no `url` attribute (must be skipped)."""

    def __init__(self, *, file_id: str, filename: str) -> None:
        self.type = "file_citation"
        self.file_id = file_id
        self.filename = filename


class _FakeAsyncStreamCM:
    """Async context manager whose iterator yields a fixed event sequence."""

    def __init__(self, events: list[_FakeStreamEvent]) -> None:
        self._events = events

    async def __aenter__(self) -> _FakeAsyncStreamCM:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def __aiter__(self) -> _FakeAsyncStreamCM:
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> _FakeStreamEvent:
        try:
            return next(self._iter)
        except StopIteration:  # noqa: B904 — translate sync→async sentinel
            raise StopAsyncIteration


def _consume_stream_with_events(
    provider: OpenAIProvider,
    fake_events: list[_FakeStreamEvent],
    prompt: str = "p",
) -> list[Any]:
    """Patch `responses.stream` to yield `fake_events`, drive `provider.stream()`."""

    def _fake_stream(**_: Any) -> _FakeAsyncStreamCM:
        return _FakeAsyncStreamCM(fake_events)

    async def _drive() -> list[Any]:
        return [event async for event in provider.stream(prompt, mode="default")]

    with patch.object(provider.client.responses, "stream", new=_fake_stream):
        return asyncio.run(_drive())


def test_openai_stream_emits_text_reasoning_citation_done_in_order() -> None:
    """SHIP-outcome regression lock for P24 Task 6.1.

    Wires `response.reasoning_summary_text.delta` and
    `response.output_text.annotation.added` SDK events through to
    StreamEvent(kind='reasoning'|'citation'). See
    planning/p24-openai-stream-audit.v1.md.
    """
    from doxa_research.providers.base import Citation, StreamEvent

    fake_events = [
        _FakeStreamEvent(type="response.created"),  # ignored
        _FakeStreamEvent(type="response.output_text.delta", delta="Hello "),
        _FakeStreamEvent(
            type="response.reasoning_summary_text.delta",
            delta="Thinking step 1.",
            summary_index=0,
        ),
        _FakeStreamEvent(type="response.output_text.delta", delta="world."),
        _FakeStreamEvent(
            type="response.output_text.annotation.added",
            annotation=_FakeURLAnnotation(url="https://example.com/a", title="Example A"),
            annotation_index=0,
        ),
        # Non-URL annotation MUST be filtered out — mirrors get_result()
        _FakeStreamEvent(
            type="response.output_text.annotation.added",
            annotation=_FakeFileAnnotation(file_id="file-x", filename="x.pdf"),
            annotation_index=1,
        ),
        # Empty-delta reasoning event MUST NOT yield (defensive)
        _FakeStreamEvent(type="response.reasoning_summary_text.delta", delta=""),
        _FakeStreamEvent(type="response.completed"),  # ignored; provider emits its own done
    ]

    provider = OpenAIProvider(api_key="k", config={"model": "gpt-4o-mini"})
    events = _consume_stream_with_events(provider, fake_events)

    # Filter to (kind, payload) tuples for readable assertions.
    summary = [
        (e.kind, e.text, getattr(e.citation, "url", None) if e.citation else None) for e in events
    ]
    assert summary == [
        ("text", "Hello ", None),
        ("reasoning", "Thinking step 1.", None),
        ("text", "world.", None),
        ("citation", "Example A", "https://example.com/a"),
        ("done", "", None),
    ], f"unexpected stream sequence: {summary}"

    citation_event = next(e for e in events if e.kind == "citation")
    assert isinstance(citation_event, StreamEvent)
    assert citation_event.citation == Citation(title="Example A", url="https://example.com/a")


def test_openai_stream_url_annotation_via_dict_shape() -> None:
    """Annotation may also arrive as a plain dict (untyped `object` payload).

    The SDK types `annotation` as bare `object`, so the provider must accept
    both pydantic-attr access AND dict-key access — same defensive style as
    `get_result()`'s parser.
    """
    fake_events = [
        _FakeStreamEvent(
            type="response.output_text.annotation.added",
            annotation={"type": "url_citation", "url": "https://example.com/b"},
            annotation_index=0,
        ),
    ]
    provider = OpenAIProvider(api_key="k", config={"model": "gpt-4o-mini"})
    events = _consume_stream_with_events(provider, fake_events)

    citation_events = [e for e in events if e.kind == "citation"]
    assert len(citation_events) == 1
    citation = citation_events[0].citation
    assert citation is not None
    assert citation.url == "https://example.com/b"
    # Title falls back to URL when missing.
    assert citation.title == "https://example.com/b"


def test_openai_stream_skips_typed_non_url_annotation_even_with_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Known non-url annotation types are skipped even if they carry a URL field."""
    caplog.set_level(logging.WARNING, logger="doxa_research.providers.openai")
    fake_events = [
        _FakeStreamEvent(
            type="response.output_text.annotation.added",
            annotation={
                "type": "file_citation",
                "url": "https://should-not-render.example",
                "title": "File citation",
            },
            annotation_index=0,
        ),
    ]
    provider = OpenAIProvider(api_key="k", config={"model": "gpt-4o-mini"})
    events = _consume_stream_with_events(provider, fake_events)

    assert [e for e in events if e.kind == "citation"] == []
    assert "file_citation" in caplog.text
    assert "https://should-not-render.example" in caplog.text


def test_openai_stream_accepts_missing_type_url_annotation_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy annotations with url/title but no type still render and warn."""
    caplog.set_level(logging.WARNING, logger="doxa_research.providers.openai")
    fake_events = [
        _FakeStreamEvent(
            type="response.output_text.annotation.added",
            annotation={
                "url": "https://legacy.example",
                "title": "Legacy URL",
            },
            annotation_index=0,
        ),
    ]
    provider = OpenAIProvider(api_key="k", config={"model": "gpt-4o-mini"})
    events = _consume_stream_with_events(provider, fake_events)

    citation_events = [e for e in events if e.kind == "citation"]
    assert len(citation_events) == 1
    assert citation_events[0].citation is not None
    assert citation_events[0].citation.url == "https://legacy.example"
    assert "missing" in caplog.text.lower()
    assert "https://legacy.example" in caplog.text
