"""Unit tests for the Gemini synchronous chat provider (P24)."""

from __future__ import annotations


def test_gemini_module_exists() -> None:
    """The Gemini provider module is importable."""
    from doxa_research.providers import gemini  # noqa: F401


def test_gemini_constants_use_suffix_naming() -> None:
    """Gemini module-level constants follow the cross-provider suffix convention."""
    from doxa_research.providers import gemini

    assert hasattr(gemini, "_DIRECT_SDK_KEYS_GEMINI")
    assert hasattr(gemini, "_PROVIDER_NAME_GEMINI")
    assert gemini._PROVIDER_NAME_GEMINI == "gemini"
    # Sample membership for some load-bearing keys
    assert "temperature" in gemini._DIRECT_SDK_KEYS_GEMINI
    assert "thinking_budget" in gemini._DIRECT_SDK_KEYS_GEMINI


def test_gemini_provider_class_exists_and_extends_research_provider() -> None:
    """GeminiProvider class extends ResearchProvider."""
    from doxa_research.providers.base import ResearchProvider
    from doxa_research.providers.gemini import GeminiProvider

    assert issubclass(GeminiProvider, ResearchProvider)


def test_gemini_provider_default_model_is_flash_lite() -> None:
    """GeminiProvider defaults to gemini-2.5-flash-lite when no model configured."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    assert provider.model == "gemini-2.5-flash-lite"


def test_gemini_client_receives_timeout_as_http_options() -> None:
    from unittest.mock import patch

    from google.genai import types

    from doxa_research.providers.gemini import GeminiProvider

    with patch("google.genai.Client") as client_cls:
        GeminiProvider(api_key="dummy", config={"timeout": 12.5})

    client_cls.assert_called_once()
    kwargs = client_cls.call_args.kwargs
    assert kwargs["api_key"] == "dummy"
    http_options = kwargs["http_options"]
    assert isinstance(http_options, types.HttpOptions)
    assert http_options.timeout == 12500


def test_gemini_provider_is_implemented() -> None:
    """is_implemented() returns True (explicit, not inherited)."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    assert provider.is_implemented() is True
    assert provider.implementation_status() is None


def test_gemini_built_in_modes_registered() -> None:
    """BUILTIN_MODES contains the three Gemini modes."""
    from doxa_research.config import BUILTIN_MODES

    assert "gemini_quick" in BUILTIN_MODES
    assert "gemini_pro" in BUILTIN_MODES
    assert "gemini_reasoning" in BUILTIN_MODES

    quick = BUILTIN_MODES["gemini_quick"]
    assert quick["provider"] == "gemini"
    assert quick["model"] == "gemini-2.5-flash-lite"
    assert quick["kind"] == "immediate"
    quick_cfg = quick.get("gemini")
    assert isinstance(quick_cfg, dict)
    assert quick_cfg.get("tools") == ["google_search"]
    assert quick_cfg.get("thinking_budget") == 0

    reasoning = BUILTIN_MODES["gemini_reasoning"]
    assert reasoning["model"] == "gemini-2.5-pro"
    reasoning_cfg = reasoning.get("gemini")
    assert isinstance(reasoning_cfg, dict)
    assert reasoning_cfg.get("thinking_budget") == -1
    assert reasoning_cfg.get("include_thoughts") is True


def test_gemini_build_messages_renders_user_prompt() -> None:
    """_build_messages_and_system creates contents=[Content(role='user', parts=[Part(text=prompt)])]."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    contents, system = provider._build_messages_and_system("What is 2+2?", None)
    assert len(contents) == 1
    content = contents[0]
    assert getattr(content, "role", None) == "user"
    parts = getattr(content, "parts", None) or []
    assert len(parts) == 1
    assert getattr(parts[0], "text", "") == "What is 2+2?"
    assert system is None


def test_gemini_build_messages_passes_through_system_prompt() -> None:
    """system_prompt becomes the system_instruction return value."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    contents, system = provider._build_messages_and_system("Q?", "You are a math tutor.")
    assert system == "You are a math tutor."


def test_gemini_build_tools_translates_google_search() -> None:
    """_build_tools(['google_search']) returns [Tool(google_search=GoogleSearch())]."""
    from google.genai import types as genai_types  # type: ignore[import-not-found]

    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    tools = provider._build_tools(["google_search"])
    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, genai_types.Tool)
    assert tool.google_search is not None


def test_gemini_build_tools_skips_unknown_names() -> None:
    """Unknown tool names are skipped (forward-compat for future tool families)."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    tools = provider._build_tools(["google_search", "future_unknown_tool"])
    assert len(tools) == 1


def test_gemini_build_generate_content_config_quick_mode() -> None:
    """gemini_quick mode produces tools=[GoogleSearch()], thinking_config.thinking_budget=0."""
    from doxa_research.config import BUILTIN_MODES
    from doxa_research.providers.gemini import GeminiProvider

    config = {**BUILTIN_MODES["gemini_quick"], "kind": "immediate"}
    provider = GeminiProvider(api_key="dummy", config=config)
    gen_config = provider._build_generate_content_config()

    assert gen_config is not None
    tools = getattr(gen_config, "tools", None) or []
    assert len(tools) == 1

    thinking_config = getattr(gen_config, "thinking_config", None)
    assert thinking_config is not None
    assert thinking_config.thinking_budget == 0


def test_gemini_build_generate_content_config_reasoning_mode() -> None:
    """gemini_reasoning mode sets include_thoughts=True."""
    from doxa_research.config import BUILTIN_MODES
    from doxa_research.providers.gemini import GeminiProvider

    config = {**BUILTIN_MODES["gemini_reasoning"], "kind": "immediate"}
    provider = GeminiProvider(api_key="dummy", config=config)
    gen_config = provider._build_generate_content_config()

    thinking_config = getattr(gen_config, "thinking_config", None)
    assert thinking_config is not None
    assert thinking_config.thinking_budget == -1
    assert thinking_config.include_thoughts is True


def test_gemini_build_generate_content_config_passthrough_temperature() -> None:
    """Direct SDK key (e.g. temperature) under [modes.X.gemini] passes through to GenerateContentConfig.temperature."""
    from doxa_research.providers.gemini import GeminiProvider

    config = {"gemini": {"temperature": 0.42}, "kind": "immediate"}
    provider = GeminiProvider(api_key="dummy", config=config)
    gen_config = provider._build_generate_content_config()

    assert gen_config is not None
    assert gen_config.temperature == 0.42


def test_gemini_build_generate_content_config_returns_none_when_empty() -> None:
    """No [modes.X.gemini] keys -> returns None (caller falls back to no config kwarg)."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    gen_config = provider._build_generate_content_config()
    assert gen_config is None


def test_gemini_build_generate_content_config_include_thoughts_only_defaults_thinking_budget_to_dynamic() -> (
    None
):
    """When only include_thoughts is set (no explicit thinking_budget), helper defaults to thinking_budget=-1 ('dynamic').

    This is a deliberate policy: setting include_thoughts=True is opting INTO
    thinking-related output, so 'dynamic' (-1) is the most useful default.
    Locked by this test; change requires changing the policy and the test together.
    """
    from doxa_research.providers.gemini import GeminiProvider

    config = {"gemini": {"include_thoughts": True}, "kind": "immediate"}
    provider = GeminiProvider(api_key="dummy", config=config)
    gen_config = provider._build_generate_content_config()

    assert gen_config is not None
    thinking_config = getattr(gen_config, "thinking_config", None)
    assert thinking_config is not None
    assert thinking_config.thinking_budget == -1, (
        "include_thoughts=True without explicit thinking_budget must default to -1 (dynamic)"
    )
    assert thinking_config.include_thoughts is True


# ---------------------------------------------------------------------------
# Task 4.3: error mapping + retry policy
# ---------------------------------------------------------------------------

import importlib  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402


def _sdk_interactions_errors():
    """Resolve the SDK's private Interactions error module across versions.

    These exception types do not inherit from ``google.genai.errors.APIError``,
    so the tests need the real classes. google-genai 1.x exposed them at
    ``google.genai._interactions``; 2.0 moved the same names to
    ``google.genai._gaos.lib.compat_errors``. Resolve newest-first rather than
    importing one path directly, so a future move fails one skip instead of
    every test in this class.
    """
    for name in ("google.genai._gaos.lib.compat_errors", "google.genai._interactions"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    pytest.skip("SDK private Interactions error module not found")


def _make_gemini_client_error(code: int, status: str, message: str, details: list | None = None):
    """Construct a google.genai.errors.ClientError for testing.

    The SDK's ClientError takes (code, response_json, response). We build a
    minimal response_json dict with the error.status / error.message / error.details
    that _map_gemini_error inspects.
    """
    from google.genai import errors as genai_errors

    error_obj: dict[str, object] = {"code": code, "status": status, "message": message}
    if details is not None:
        error_obj["details"] = details
    response_json: dict[str, object] = {"error": error_obj}
    fake_response = MagicMock()
    fake_response.status_code = code
    return genai_errors.ClientError(code=code, response_json=response_json, response=fake_response)


def _make_gemini_server_error(code: int, status: str = "INTERNAL", message: str = "server error"):
    from google.genai import errors as genai_errors

    response_json = {"error": {"code": code, "status": status, "message": message}}
    fake_response = MagicMock()
    fake_response.status_code = code
    return genai_errors.ServerError(code=code, response_json=response_json, response=fake_response)


@pytest.mark.parametrize(
    "code,status,message,expected_cls,expected_substr",
    [
        # Auth-invalid (matched by phrase in message)
        (
            401,
            "UNAUTHENTICATED",
            "API key not valid. Please pass a valid API key.",
            "DoxaError",
            "key is invalid",
        ),
        # Auth-missing (different phrasing)
        (401, "UNAUTHENTICATED", "Missing authentication credential.", "APIKeyError", "gemini"),
        # Rate-limit (per-minute, no quota markers)
        (
            429,
            "RESOURCE_EXHAUSTED",
            "Quota exceeded for quota metric 'Generate content requests per minute'",
            "APIRateLimitError",
            "rate",
        ),
        # Bad request (no offending param)
        (400, "INVALID_ARGUMENT", "Some bad request error", "ProviderError", "Bad request"),
        # NotFound
        (404, "NOT_FOUND", "Model not found", "ProviderError", "not found"),
        # Permission denied
        (
            403,
            "PERMISSION_DENIED",
            "API key does not have permission",
            "ProviderError",
            "Permission",
        ),
    ],
)
def test_gemini_error_mapping_table(code, status, message, expected_cls, expected_substr) -> None:
    from doxa_research.errors import (
        APIKeyError,
        APIQuotaError,
        APIRateLimitError,
        DoxaError,
        ProviderError,
    )
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = _make_gemini_client_error(code, status, message)
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-flash-lite", verbose=False)
    expected = {
        "DoxaError": DoxaError,
        "APIKeyError": APIKeyError,
        "APIRateLimitError": APIRateLimitError,
        "APIQuotaError": APIQuotaError,
        "ProviderError": ProviderError,
    }[expected_cls]
    assert isinstance(mapped, expected), (
        f"expected {expected.__name__}, got {type(mapped).__name__}: {mapped}"
    )
    assert expected_substr.lower() in str(mapped).lower()


def test_gemini_quota_per_day_maps_to_apiquotaerror() -> None:
    """429 with 'per day' in message maps to APIQuotaError, not APIRateLimitError."""
    from doxa_research.errors import APIQuotaError
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = _make_gemini_client_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Quota exceeded for quota metric 'Generate content requests per day'",
    )
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, APIQuotaError)


def test_gemini_quota_free_tier_maps_to_apiquotaerror() -> None:
    """429 with FREE_TIER_LIMIT_EXCEEDED reason maps to APIQuotaError."""
    from doxa_research.errors import APIQuotaError
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = _make_gemini_client_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Quota exceeded",
        details=[{"reason": "FREE_TIER_LIMIT_EXCEEDED"}],
    )
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, APIQuotaError)


def test_gemini_invalid_key_doxaerror_has_exit_code_2() -> None:
    """The shared _invalid_key_doxaerror helper guarantees exit_code=2 with brand-correct casing."""
    from doxa_research.errors import DoxaError
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = _make_gemini_client_error(401, "UNAUTHENTICATED", "API key not valid")
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, DoxaError)
    assert mapped.exit_code == 2
    assert "Gemini" in str(mapped)  # capitalized brand name


def test_gemini_invalid_argument_extracts_offending_param() -> None:
    """400 INVALID_ARGUMENT with 'parameter X' extracts X via the shared helper."""
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = _make_gemini_client_error(
        400,
        "INVALID_ARGUMENT",
        "Unsupported parameter 'frequency_penalty' for gemini-2.5-pro",
    )
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert "frequency_penalty" in str(mapped)


def test_gemini_server_error_5xx_maps_to_provider_error() -> None:
    from doxa_research.errors import ProviderError
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = _make_gemini_server_error(500)
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, ProviderError)
    assert "server error" in str(mapped).lower()


def test_gemini_httpx_timeout_maps_to_provider_error() -> None:
    import httpx

    from doxa_research.errors import ProviderError
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = httpx.TimeoutException("Request timed out")
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, ProviderError)
    assert "timed out" in str(mapped).lower() or "timeout" in str(mapped).lower()


def test_gemini_httpx_connect_error_maps_to_provider_error() -> None:
    import httpx

    from doxa_research.errors import ProviderError
    from doxa_research.providers.gemini import _map_gemini_error

    fake_exc = httpx.ConnectError("Connection refused")
    mapped = _map_gemini_error(fake_exc, "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, ProviderError)


def test_gemini_unknown_exception_maps_to_provider_error() -> None:
    from doxa_research.errors import ProviderError
    from doxa_research.providers.gemini import _map_gemini_error

    mapped = _map_gemini_error(RuntimeError("???"), "gemini-2.5-pro", verbose=False)
    assert isinstance(mapped, ProviderError)
    assert "Unexpected" in str(mapped) or "???" in str(mapped)


def test_gemini_retry_classifier_retries_transient_raw_exceptions_and_429() -> None:
    """The Gemini retry classifier matches raw transport/server errors and raw 429.

    Wrapping into APIRateLimitError happens AFTER retry classification, so the
    classifier must inspect the raw genai_errors.ClientError(code=429) — not
    the post-mapping DoxaError subclass.
    """
    import httpx
    from google.genai import errors as genai_errors

    from doxa_research.providers.gemini import (
        _GEMINI_RETRY_CLASSES,
        _is_retryable_gemini_exception,
    )

    # Raw transient classes still listed for transport/server-side failures.
    assert httpx.TimeoutException in _GEMINI_RETRY_CLASSES
    assert httpx.ConnectError in _GEMINI_RETRY_CLASSES
    assert genai_errors.ServerError in _GEMINI_RETRY_CLASSES

    # 429 is recognized at the predicate level on the raw SDK exception.
    rate_limit = genai_errors.ClientError.__new__(genai_errors.ClientError)
    rate_limit.code = 429
    rate_limit.message = "rate limit"
    rate_limit.status = "RESOURCE_EXHAUSTED"
    assert _is_retryable_gemini_exception(rate_limit) is True

    # Non-429 4xx (e.g. 400) is NOT retried.
    bad_request = genai_errors.ClientError.__new__(genai_errors.ClientError)
    bad_request.code = 400
    bad_request.message = "bad request"
    bad_request.status = "INVALID_ARGUMENT"
    assert _is_retryable_gemini_exception(bad_request) is False

    # Raw transient exceptions remain retryable via the predicate too.
    assert _is_retryable_gemini_exception(httpx.TimeoutException("t")) is True


def test_gemini_retry_classes_excludes_quota_error() -> None:
    """APIQuotaError is NOT in the retry set (quota is permanent until reset)."""
    from doxa_research.errors import APIQuotaError
    from doxa_research.providers.gemini import _GEMINI_RETRY_CLASSES

    assert APIQuotaError not in _GEMINI_RETRY_CLASSES


# ---------------------------------------------------------------------------
# Task 4.4: stream() translation
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402


def _make_chunk(parts: list[dict] | None = None, grounding: dict | None = None) -> SimpleNamespace:
    """Build a fake GenerateContentResponse chunk for stream tests."""
    candidate_parts = []
    for p in parts or []:
        candidate_parts.append(
            SimpleNamespace(text=p.get("text", ""), thought=p.get("thought", False))
        )
    grounding_obj = SimpleNamespace(**grounding) if grounding else None
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=candidate_parts),
        grounding_metadata=grounding_obj,
    )
    return SimpleNamespace(
        candidates=[candidate],
        text=" ".join(p.get("text", "") for p in (parts or []) if not p.get("thought")),
    )


async def _consume_events(stream_iter):
    return [event async for event in stream_iter]


def _make_gemini_provider_with_chunks(chunks: list):
    """Construct a GeminiProvider whose client.aio.models.generate_content_stream
    yields the given fake chunks. Uses unittest.mock.patch over google.genai.Client.

    The real google-genai SDK exposes generate_content_stream as a coroutine
    function returning an AsyncIterator (i.e. `async for chunk in await
    client.aio.models.generate_content_stream(...)`). We mirror that shape
    here: the outer function is async (returns a coroutine) that resolves
    to an async generator over the fake chunks.
    """
    from doxa_research.providers.gemini import GeminiProvider

    async def _async_iter():
        for c in chunks:
            yield c

    async def fake_stream(**kw):
        return _async_iter()

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content_stream = fake_stream
    mock_client.aio.models.generate_content = lambda **kw: None  # not exercised here

    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(api_key="dummy", config={"kind": "immediate"})
    return provider


def test_gemini_stream_emits_text_for_non_thought_parts() -> None:
    """A chunk with non-thought parts emits StreamEvent('text', text) per part."""
    chunks = [
        _make_chunk(
            parts=[
                {"text": "Hello "},
                {"text": "world.", "thought": False},
            ]
        ),
    ]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    text_events = [e for e in events if e.kind == "text"]
    assert len(text_events) == 2
    assert text_events[0].text == "Hello "
    assert text_events[1].text == "world."


def test_gemini_stream_emits_reasoning_for_thought_parts() -> None:
    """A part with thought=True emits StreamEvent('reasoning', text)."""
    chunks = [
        _make_chunk(
            parts=[
                {"text": "Let me think: ", "thought": True},
                {"text": "Answer is 42.", "thought": False},
            ]
        ),
    ]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    reasoning = [e for e in events if e.kind == "reasoning"]
    text = [e for e in events if e.kind == "text"]
    assert len(reasoning) == 1
    assert reasoning[0].text == "Let me think: "
    assert len(text) == 1
    assert text[0].text == "Answer is 42."


def test_gemini_stream_emits_citations_from_terminal_grounding_chunks() -> None:
    """grounding_metadata.grounding_chunks emit deduped StreamEvent('citation', Citation(...))."""
    grounding = {
        "grounding_chunks": [
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA",
                    title="example.com",
                )
            ),
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB",
                    title="other.com",
                )
            ),
            # duplicate URL — should dedupe
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA",
                    title="example.com",
                )
            ),
        ]
    }

    chunks = [
        _make_chunk(parts=[{"text": "Per source A and B."}]),
        _make_chunk(parts=[], grounding=grounding),  # terminal chunk
    ]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    citations = [e for e in events if e.kind == "citation"]
    assert len(citations) == 2  # deduped
    from doxa_research.providers.base import Citation

    assert all(isinstance(e.citation, Citation) for e in citations)
    assert citations[0].citation.url.startswith("https://vertexaisearch")


def test_gemini_stream_terminal_done_event() -> None:
    """Stream always ends with StreamEvent('done', '')."""
    chunks = [_make_chunk(parts=[{"text": "Hi."}])]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    assert events[-1].kind == "done"
    assert events[-1].text == ""


def test_gemini_stream_title_falls_back_to_netloc_when_empty() -> None:
    """Citation.title = urlparse(uri).netloc when web.title is missing/empty."""
    grounding = {
        "grounding_chunks": [
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://example.com/path",
                    title="",
                )
            ),
        ]
    }
    chunks = [_make_chunk(parts=[], grounding=grounding)]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    citations = [e for e in events if e.kind == "citation"]
    assert len(citations) == 1
    assert citations[0].citation.title == "example.com"


def test_gemini_stream_skips_empty_text_parts() -> None:
    """Empty-text parts (e.g., placeholder/empty) are skipped, not emitted."""
    chunks = [
        _make_chunk(
            parts=[
                {"text": ""},  # empty — should be skipped
                {"text": "Real text."},
            ]
        ),
    ]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    text_events = [e for e in events if e.kind == "text"]
    assert len(text_events) == 1
    assert text_events[0].text == "Real text."


def test_gemini_stream_handles_chunk_without_candidates() -> None:
    """Chunks with no candidates (or empty candidates list) are tolerated, no events emitted."""
    chunks = [
        SimpleNamespace(candidates=[], text=""),  # empty candidates list
        _make_chunk(parts=[{"text": "Real."}]),
    ]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    text_events = [e for e in events if e.kind == "text"]
    assert len(text_events) == 1
    assert events[-1].kind == "done"


def test_gemini_stream_skips_grounding_chunks_without_web_field() -> None:
    """grounding_chunks of type image/maps/retrievedContext (no .web) are skipped."""
    grounding = {
        "grounding_chunks": [
            # Has web — should produce a citation
            SimpleNamespace(web=SimpleNamespace(uri="https://example.com", title="example.com")),
            # No web (e.g. image variant) — should skip
            SimpleNamespace(web=None),
        ]
    }
    chunks = [_make_chunk(parts=[], grounding=grounding)]
    provider = _make_gemini_provider_with_chunks(chunks)
    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    citations = [e for e in events if e.kind == "citation"]
    assert len(citations) == 1


def test_gemini_stream_mid_iteration_error_maps_to_provider_error() -> None:
    """A ClientError raised mid-iteration is mapped via _map_gemini_error."""
    from google.genai import errors as genai_errors

    from doxa_research.errors import ProviderError

    fake_response = MagicMock()
    fake_response.status_code = 400

    async def _iter_raises_mid():
        yield _make_chunk(parts=[{"text": "Started"}])
        raise genai_errors.ClientError(
            code=400,
            response_json={
                "error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Bad mid-stream"}
            },
            response=fake_response,
        )

    async def fake_stream_raises_mid_iter(**kw):
        return _iter_raises_mid()

    from doxa_research.providers.gemini import GeminiProvider

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content_stream = fake_stream_raises_mid_iter
    mock_client.aio.models.generate_content = lambda **kw: None

    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(api_key="dummy", config={"kind": "immediate"})
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))
    assert "Bad mid-stream" in str(excinfo.value) or "bad request" in str(excinfo.value).lower()


def test_gemini_stream_retries_transient_server_error_before_first_event() -> None:
    """A transient stream startup 503 is retried before any output is emitted."""
    from doxa_research.providers.gemini import GeminiProvider

    attempts = {"n": 0}
    chunks = [_make_chunk(parts=[{"text": "Recovered."}])]

    async def _async_iter():
        for chunk in chunks:
            yield chunk

    async def fake_stream(**kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _make_gemini_server_error(503, status="UNAVAILABLE", message="try again")
        return _async_iter()

    async def fake_sleep(delay: float) -> None:
        return None

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content_stream = fake_stream
    mock_client.aio.models.generate_content = lambda **kw: None

    with (
        patch("google.genai.Client", return_value=mock_client),
        patch("doxa_research.providers.gemini.asyncio.sleep", fake_sleep),
    ):
        provider = GeminiProvider(api_key="dummy", config={"kind": "immediate"})

    events = asyncio.run(_consume_events(provider.stream("Q?", "test_mode")))

    assert attempts["n"] == 2
    assert [event.kind for event in events] == ["text", "done"]
    assert events[0].text == "Recovered."


# ---------------------------------------------------------------------------
# Task 4.5: submit / check_status / get_result + tenacity retry
# ---------------------------------------------------------------------------


def test_gemini_submit_returns_job_id_and_stashes_response() -> None:
    """submit() runs generate_content once and stashes response under a job_id starting with 'gemini-'."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    from doxa_research.providers.gemini import GeminiProvider

    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text="Answer.", thought=False)]),
                grounding_metadata=None,
            )
        ],
        text="Answer.",
    )

    async def fake_generate_content(**kw):
        return fake_response

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content = fake_generate_content
    mock_client.aio.models.generate_content_stream = lambda **kw: None  # not exercised

    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(api_key="dummy", config={"kind": "immediate"})
    job_id = asyncio.run(provider.submit("Q?", "test_mode"))

    assert job_id.startswith("gemini-")
    assert job_id in provider.jobs
    assert provider.jobs[job_id]["response"] is fake_response


def test_gemini_check_status_returns_completed_for_known_job() -> None:
    """check_status returns {'status': 'completed', 'progress': 1.0} for known immediate jobs."""
    import asyncio

    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test-job"] = {"response": object(), "created_at": 0}
    status = asyncio.run(provider.check_status("test-job"))

    assert status["status"] == "completed"
    assert status["progress"] == 1.0


def test_gemini_check_status_returns_not_found_for_unknown_job() -> None:
    """check_status returns 'not_found' for unknown job_ids."""
    import asyncio

    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    status = asyncio.run(provider.check_status("nonexistent"))

    assert status["status"] == "not_found"


def test_gemini_get_result_renders_text_only_when_no_reasoning_or_sources() -> None:
    """get_result returns just the answer text when no thoughts and no grounding."""
    import asyncio
    from types import SimpleNamespace

    from doxa_research.providers.gemini import GeminiProvider

    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[SimpleNamespace(text="Final answer.", thought=False)]
                ),
                grounding_metadata=None,
            )
        ],
        text="Final answer.",
    )
    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test"] = {"response": fake_response, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    assert rendered == "Final answer."  # no extra sections


def test_gemini_get_result_renders_reasoning_section_when_thoughts_present() -> None:
    """Thought parts collected into a ## Reasoning section above the answer."""
    import asyncio
    from types import SimpleNamespace

    from doxa_research.providers.gemini import GeminiProvider

    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text="Reasoning bit. ", thought=True),
                        SimpleNamespace(text="Final answer.", thought=False),
                    ]
                ),
                grounding_metadata=None,
            )
        ],
        text="Final answer.",
    )
    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test"] = {"response": fake_response, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    assert "## Reasoning" in rendered
    assert "Reasoning bit." in rendered
    assert "Final answer." in rendered


def test_gemini_get_result_renders_sources_section_with_sanitized_links() -> None:
    """Grounding chunks become a ## Sources section using md_link_title/md_link_url."""
    import asyncio
    from types import SimpleNamespace

    from doxa_research.providers.gemini import GeminiProvider

    grounding = SimpleNamespace(
        grounding_chunks=[
            SimpleNamespace(web=SimpleNamespace(uri="https://example.com", title="example.com")),
        ]
    )
    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text="Body.", thought=False)]),
                grounding_metadata=grounding,
            )
        ],
        text="Body.",
    )
    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test"] = {"response": fake_response, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    assert "## Sources" in rendered
    assert "[example.com](https://example.com)" in rendered


def test_gemini_get_result_sanitizes_adversarial_citation() -> None:
    """Adversarial titles (HTML) and URLs (javascript:) are neutralized via md_link_*."""
    import asyncio
    from types import SimpleNamespace

    from doxa_research.providers.gemini import GeminiProvider

    grounding = SimpleNamespace(
        grounding_chunks=[
            SimpleNamespace(
                web=SimpleNamespace(uri="javascript:alert(1)", title="<script>x</script>")
            ),
        ]
    )
    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text="Body.", thought=False)]),
                grounding_metadata=grounding,
            )
        ],
        text="Body.",
    )
    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test"] = {"response": fake_response, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    assert "<script>" not in rendered
    assert "javascript:" not in rendered


def test_gemini_get_result_unknown_job_raises() -> None:
    """get_result for unknown job_id raises ProviderError."""
    import asyncio

    import pytest

    from doxa_research.errors import ProviderError
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="dummy", config={})
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.get_result("nonexistent"))
    assert "nonexistent" in str(excinfo.value) or "not found" in str(excinfo.value).lower()


def test_gemini_get_result_dedupes_sources_by_url() -> None:
    """Duplicate URLs in grounding_chunks are deduped in the Sources block."""
    import asyncio
    from types import SimpleNamespace

    from doxa_research.providers.gemini import GeminiProvider

    grounding = SimpleNamespace(
        grounding_chunks=[
            SimpleNamespace(web=SimpleNamespace(uri="https://a.com", title="A")),
            SimpleNamespace(web=SimpleNamespace(uri="https://a.com", title="A again")),  # dup
            SimpleNamespace(web=SimpleNamespace(uri="https://b.com", title="B")),
        ]
    )
    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text="Body.", thought=False)]),
                grounding_metadata=grounding,
            )
        ],
        text="Body.",
    )
    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test"] = {"response": fake_response, "created_at": 0}

    rendered = asyncio.run(provider.get_result("test"))
    # Should have only 2 source lines (a.com once, b.com once)
    sources_block = rendered.split("## Sources")[1] if "## Sources" in rendered else ""
    a_count = sources_block.count("a.com")
    assert a_count == 1, f"expected 1 occurrence of a.com in sources, got {a_count}"


def test_gemini_get_result_empty_content_with_verbose_emits_debug(capsys) -> None:
    """verbose=True + empty content emits debug ladder to stderr."""
    import asyncio
    from types import SimpleNamespace

    from doxa_research.providers.gemini import GeminiProvider

    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[]),  # empty
                grounding_metadata=None,
            )
        ],
        text="",
    )
    fake_response.model_dump_json = lambda **kw: '{"empty": true}'
    provider = GeminiProvider(api_key="dummy", config={})
    provider.jobs["test"] = {"response": fake_response, "created_at": 0}

    asyncio.run(provider.get_result("test", verbose=True))
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert "empty" in combined or "debug" in combined or "no content" in combined


def test_gemini_submit_retry_decorator_retries_transient_errors() -> None:
    """tenacity retry on httpx.TimeoutException — succeeds after 2 transient failures."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    import httpx

    from doxa_research.providers.gemini import GeminiProvider

    call_count = {"n": 0}
    fake_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text="OK.", thought=False)]),
                grounding_metadata=None,
            )
        ],
        text="OK.",
    )

    async def fake_generate_content(**kw):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.TimeoutException("transient")
        return fake_response

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content = fake_generate_content

    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(api_key="dummy", config={"kind": "immediate"})

    # Force tenacity to wait zero seconds between retries. Patching the
    # module-level `wait_exponential` AFTER class definition is a no-op
    # because the decorator already captured its return value; swap the
    # bound wait strategy on the retrying object directly.
    from typing import Any, cast

    import tenacity

    retrying = cast(Any, provider._submit_with_retry).retry
    retrying.wait = tenacity.wait_none()
    job_id = asyncio.run(provider.submit("Q?", "test_mode"))

    assert call_count["n"] == 3  # 2 fails + 1 success
    assert job_id in provider.jobs


def test_gemini_kind_mismatch_rejects_deep_research_in_immediate_submit() -> None:
    """deep-research-pro-preview-12-2025 with kind=immediate raises ModeKindMismatchError on submit()."""
    import asyncio

    import pytest

    from doxa_research.errors import ModeKindMismatchError
    from doxa_research.providers.gemini import GeminiProvider

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content = lambda **kw: None  # should NOT be called
    mock_client.aio.models.generate_content_stream = lambda **kw: None

    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(
            api_key="dummy",
            config={"kind": "immediate", "model": "deep-research-pro-preview-12-2025"},
        )
    with pytest.raises(ModeKindMismatchError):
        asyncio.run(provider.submit("Q?", "test_mode"))


def test_gemini_kind_mismatch_rejects_deep_research_in_immediate_stream() -> None:
    """deep-research-pro-preview-12-2025 with kind=immediate raises ModeKindMismatchError on stream() entry."""
    import asyncio

    import pytest

    from doxa_research.errors import ModeKindMismatchError
    from doxa_research.providers.gemini import GeminiProvider

    captured = {"called": False}

    async def fake_stream(**kw):
        captured["called"] = True
        if False:
            yield  # pragma: no cover

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    mock_client.aio.models.generate_content_stream = fake_stream
    mock_client.aio.models.generate_content = lambda **kw: None

    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(
            api_key="dummy",
            config={"kind": "immediate", "model": "deep-research-pro-preview-12-2025"},
        )

    async def consume():
        async for _ in provider.stream("Q?", "test_mode"):
            pass

    with pytest.raises(ModeKindMismatchError):
        asyncio.run(consume())

    # CRITICAL: the SDK call MUST NOT have been made — guard fires before HTTP.
    assert captured["called"] is False, "stream API was called despite kind-mismatch"


def test_gemini_kind_mismatch_allows_regular_models() -> None:
    """gemini-2.5-pro / gemini-2.5-flash-lite with kind=immediate are allowed (validate is no-op)."""
    from doxa_research.providers.gemini import GeminiProvider

    for model in ("gemini-2.5-pro", "gemini-2.5-flash-lite"):
        provider = GeminiProvider(
            api_key="dummy",
            config={"kind": "immediate", "model": model},
        )
        # Direct call to the validator should not raise
        provider._validate_kind_for_model("test_mode")  # no exception


def test_gemini_kind_mismatch_allows_when_kind_is_background() -> None:
    """deep-research model with kind=background is allowed (this is P28's territory)."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(
        api_key="dummy",
        config={"kind": "background", "model": "deep-research-pro-preview-12-2025"},
    )
    # Direct call to the validator should not raise
    provider._validate_kind_for_model("test_mode")


def test_gemini_kind_mismatch_no_kind_in_config_no_op() -> None:
    """When kind is missing from config, the guard is a no-op (silent passthrough)."""
    from doxa_research.providers.gemini import GeminiProvider

    provider = GeminiProvider(
        api_key="dummy",
        config={"model": "deep-research-pro-preview-12-2025"},  # no kind
    )
    # No exception (matches OpenAI/Perplexity pattern)
    provider._validate_kind_for_model("test_mode")


class TestMapGeminiErrorInteractionsSpecific:
    """Task 2: _map_gemini_error catches the SDK's private Interactions exceptions."""

    def _make_interactions_request(self):  # type: ignore[return]
        import httpx

        return httpx.Request("POST", "https://ai.googleapis.com/v1/interactions")

    def test_interactions_404_produces_interaction_expired_message(self):
        """interactions.get(bad-id) raises NotFoundError with status_code=404."""
        import httpx

        NotFoundError = _sdk_interactions_errors().NotFoundError

        from doxa_research.providers.gemini import _map_gemini_error

        req = self._make_interactions_request()
        resp = httpx.Response(404, content=b"", request=req)
        exc = NotFoundError(
            "Interaction not found: interactions/does-not-exist-spike",
            response=resp,
            body=None,
        )
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        msg = str(result)
        assert "interaction" in msg.lower()
        assert "Model 'deep-research-preview-04-2026' not found" not in msg

    def test_interactions_400_invalid_key_produces_api_key_error(self):
        """interactions.create with bad key raises BadRequestError with status_code=400."""
        import httpx

        BadRequestError = _sdk_interactions_errors().BadRequestError

        from doxa_research.errors import APIKeyError, DoxaError
        from doxa_research.providers.gemini import _map_gemini_error

        req = self._make_interactions_request()
        resp = httpx.Response(400, content=b"", request=req)
        exc = BadRequestError("API key not valid", response=resp, body=None)
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, (APIKeyError, DoxaError))
        if not isinstance(result, APIKeyError):
            # DoxaError stores the URL in .suggestion, not in str() / message
            suggestion = getattr(result, "suggestion", "") or ""
            assert "aistudio.google.com" in suggestion

    def test_interactions_500_produces_provider_error(self):
        """interactions.{create,get,cancel} 5xx raises InternalServerError."""
        import httpx

        InternalServerError = _sdk_interactions_errors().InternalServerError

        from doxa_research.errors import ProviderError
        from doxa_research.providers.gemini import _map_gemini_error

        req = self._make_interactions_request()
        resp = httpx.Response(500, content=b"", request=req)
        exc = InternalServerError(
            "Internal server error processing interaction",
            response=resp,
            body=None,
        )
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, ProviderError)
        assert "server" in str(result).lower()

    def test_interactions_429_produces_rate_limit_error(self):
        """interactions.* 429 maps to APIRateLimitError."""
        import httpx

        RateLimitError = _sdk_interactions_errors().RateLimitError

        from doxa_research.errors import APIRateLimitError
        from doxa_research.providers.gemini import _map_gemini_error

        req = httpx.Request("POST", "https://example.com")
        resp = httpx.Response(429, request=req)
        exc = RateLimitError("rate limited", response=resp, body=None)
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, APIRateLimitError)

    def test_interactions_403_dr_tier_gives_pricing_hint(self):
        """403 with tier/paid wording AND a deep-research model surfaces pricing URL."""
        import httpx

        PermissionDeniedError = _sdk_interactions_errors().PermissionDeniedError

        from doxa_research.errors import ProviderError
        from doxa_research.providers.gemini import _map_gemini_error

        req = httpx.Request("POST", "https://example.com")
        resp = httpx.Response(403, request=req)
        exc = PermissionDeniedError("Deep Research requires paid tier", response=resp, body=None)
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, ProviderError)
        assert "pricing" in str(result).lower() or "ai.google.dev" in str(result)

    def test_interactions_403_non_dr_model_no_pricing_hint(self):
        """403 on a non-DR model gives a plain permission-denied (no pricing URL)."""
        import httpx

        PermissionDeniedError = _sdk_interactions_errors().PermissionDeniedError

        from doxa_research.providers.gemini import _map_gemini_error

        req = httpx.Request("POST", "https://example.com")
        resp = httpx.Response(403, request=req)
        exc = PermissionDeniedError("forbidden", response=resp, body=None)
        result = _map_gemini_error(exc, model="gemini-2.5-flash-lite")
        assert "permission denied" in str(result).lower()
        assert "ai.google.dev/pricing" not in str(result)

    def test_interactions_401_invalid_key_produces_api_key_error(self):
        """401 with key-related message routes via _invalid_key_doxaerror or APIKeyError."""
        import httpx

        AuthenticationError = _sdk_interactions_errors().AuthenticationError

        from doxa_research.errors import APIKeyError, DoxaError
        from doxa_research.providers.gemini import _map_gemini_error

        req = httpx.Request("POST", "https://example.com")
        resp = httpx.Response(401, request=req)
        exc = AuthenticationError("api key not valid", response=resp, body=None)
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, (APIKeyError, DoxaError))
        if not isinstance(result, APIKeyError):
            assert "aistudio.google.com" in getattr(result, "suggestion", "")

    def test_interactions_401_generic_gives_api_key_error(self):
        """401 without key-phrase routes to APIKeyError."""
        import httpx

        AuthenticationError = _sdk_interactions_errors().AuthenticationError

        from doxa_research.errors import APIKeyError
        from doxa_research.providers.gemini import _map_gemini_error

        req = httpx.Request("POST", "https://example.com")
        resp = httpx.Response(401, request=req)
        exc = AuthenticationError("unauthorized", response=resp, body=None)
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, APIKeyError)

    def test_duck_type_fallback_when_isinstance_misses(self, monkeypatch):
        """If _InteractionsAPIError isinstance check fails, duck-type still catches."""
        from doxa_research.errors import ProviderError
        from doxa_research.providers.gemini import _map_gemini_error

        class _FakeInteractionsError(Exception):
            status_code: int

        _FakeInteractionsError.__module__ = "google.genai._interactions_renamed"
        exc = _FakeInteractionsError("transient outage")
        exc.status_code = 503
        result = _map_gemini_error(exc, model="deep-research-preview-04-2026")
        assert isinstance(result, ProviderError)


class TestGeminiProviderRouting:
    """Task 3: submit/check_status/get_result route on is_background_model."""

    def test_submit_routes_to_immediate_for_chat_model(self, monkeypatch):
        import asyncio

        from doxa_research.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="dummy", config={"model": "gemini-2.5-flash-lite"})
        called = {"immediate": False, "deep_research": False}

        async def fake_immediate(prompt, mode, system_prompt, verbose):
            called["immediate"] = True
            return "immediate-job-id"

        async def fake_dr(prompt, mode, system_prompt, verbose):
            called["deep_research"] = True
            return "dr-job-id"

        monkeypatch.setattr(provider, "_immediate_submit", fake_immediate)
        monkeypatch.setattr(provider, "_deep_research_submit", fake_dr)
        result = asyncio.run(provider.submit("x", "gemini_quick", None, False))
        assert result == "immediate-job-id"
        assert called == {"immediate": True, "deep_research": False}

    def test_submit_routes_to_dr_for_deep_research_model(self, monkeypatch):
        import asyncio

        from doxa_research.providers.gemini import GeminiProvider

        provider = GeminiProvider(
            api_key="dummy", config={"model": "deep-research-preview-04-2026"}
        )
        called = {"immediate": False, "deep_research": False}

        async def fake_immediate(prompt, mode, system_prompt, verbose):
            called["immediate"] = True
            return "immediate-job-id"

        async def fake_dr(prompt, mode, system_prompt, verbose):
            called["deep_research"] = True
            return "dr-job-id"

        monkeypatch.setattr(provider, "_immediate_submit", fake_immediate)
        monkeypatch.setattr(provider, "_deep_research_submit", fake_dr)
        result = asyncio.run(provider.submit("x", "gemini_deep_research", None, False))
        assert result == "dr-job-id"
        assert called == {"immediate": False, "deep_research": True}


class TestGeminiJobsSchema:
    """Task 4: self.jobs entries have a 'kind' discriminator."""

    def test_immediate_submit_stashes_kind_immediate(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from doxa_research.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="dummy", config={"model": "gemini-2.5-flash-lite"})
        fake_response = MagicMock(id="fake-resp-id")
        with patch.object(provider, "_submit_with_retry", new_callable=AsyncMock) as m:
            m.return_value = fake_response
            job_id = asyncio.run(provider._immediate_submit("x", "gemini_quick", None, False))
        assert provider.jobs[job_id]["kind"] == "immediate"
        assert "response" in provider.jobs[job_id]


class TestGeminiDeepResearchSubmit:
    """Task 5: _deep_research_submit calls client.aio.interactions.create correctly."""

    def test_submit_calls_interactions_create_with_agent_background_store(self):
        async def _run():
            import asyncio  # noqa: F401
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            fake_create = AsyncMock(return_value=MagicMock(id="interactions/xyz-123"))
            provider.client = MagicMock()
            provider.client.aio.interactions.create = fake_create
            job_id = await provider._deep_research_submit(
                "Research X", "gemini_deep_research", None, False
            )
            fake_create.assert_awaited_once()
            call_kwargs = fake_create.call_args.kwargs
            assert call_kwargs["agent"] == "deep-research-preview-04-2026"
            assert call_kwargs["input"] == "Research X"
            assert call_kwargs["background"] is True
            assert call_kwargs["store"] is True
            assert job_id == "interactions/xyz-123"
            assert provider.jobs[job_id]["kind"] == "deep_research"
            assert provider.jobs[job_id]["interaction_id"] == "interactions/xyz-123"

        asyncio.run(_run())

    def test_submit_does_not_cache_response_body(self):
        """DR submit returns immediately; the response body is not yet available.
        self.jobs entry must NOT contain a 'response' key (only metadata)."""

        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            provider.client = MagicMock()
            provider.client.aio.interactions.create = AsyncMock(
                return_value=MagicMock(id="interactions/abc")
            )
            job_id = await provider._deep_research_submit("x", "gemini_deep_research", None, False)
            assert "response" not in provider.jobs[job_id]

        asyncio.run(_run())


class TestGeminiDeepResearchCheckStatus:
    """Task 6: _deep_research_check_status polls interactions.get and maps status."""

    @pytest.mark.parametrize(
        "live_status,expected_doxa_status,expected_failure_type",
        [
            # Per P18 design spec §245, the canonical polling-status set for
            # background-kind providers is {"running", "queued", "completed"};
            # Gemini's raw "in_progress" SDK literal must translate to canonical
            # "running" (matching OpenAIProvider and PerplexityProvider).
            ("in_progress", "running", None),
            ("requires_action", "permanent_error", "requires_action"),
            ("completed", "completed", None),
            ("failed", "permanent_error", "permanent"),
            ("cancelled", "cancelled", None),
            ("incomplete", "permanent_error", "permanent"),
        ],
    )
    def test_status_mapping(self, live_status, expected_doxa_status, expected_failure_type):
        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
            }
            fake_get = AsyncMock(return_value=MagicMock(status=live_status))
            # interactions is read-only on AsyncClient — replace provider.client
            provider.client = MagicMock()
            provider.client.aio.interactions.get = fake_get
            result = await provider._deep_research_check_status("interactions/abc")
            assert result["status"] == expected_doxa_status
            if expected_failure_type is None:
                assert "failure_type" not in result or result["failure_type"] is None
            else:
                assert result["failure_type"] == expected_failure_type
            assert result["raw_status"] == live_status

        asyncio.run(_run())

    def test_requires_action_error_message_explains_v1_unsupported(self):
        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
            }
            fake_get = AsyncMock(return_value=MagicMock(status="requires_action"))
            provider.client = MagicMock()
            provider.client.aio.interactions.get = fake_get
            result = await provider._deep_research_check_status("interactions/abc")
            assert "requires_action" in result["failure_type"]
            assert "approval" in result["error"].lower() or "action" in result["error"].lower()

        asyncio.run(_run())

    def test_incomplete_error_message_documents_v1_limitation(self):
        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
            }
            fake_get = AsyncMock(return_value=MagicMock(status="incomplete"))
            provider.client = MagicMock()
            provider.client.aio.interactions.get = fake_get
            result = await provider._deep_research_check_status("interactions/abc")
            assert "incomplete" in result["error"].lower() or "truncated" in result["error"].lower()

        asyncio.run(_run())

    def test_unknown_job_id(self):
        async def _run():
            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            result = await provider._deep_research_check_status("interactions/unknown")
            assert result["status"] == "not_found"

        asyncio.run(_run())


class TestGeminiDeepResearchGetResult:
    """Task 7: _deep_research_get_result with layered citation rendering."""

    def test_renders_model_output_text(self):
        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            text_item = MagicMock(type="text", text="The three papers are...", annotations=[])
            model_output_step = MagicMock(type="model_output", content=[text_item])
            fake_interaction = MagicMock(status="completed", steps=[model_output_step])
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
                "last_interaction": fake_interaction,
            }
            result = await provider._deep_research_get_result("interactions/abc", False)
            assert "The three papers are..." in result

        asyncio.run(_run())

    def test_extracts_annotations_from_model_output_step(self, monkeypatch):
        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            ann = MagicMock(
                type="url_citation",
                title=None,
                url="https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ123",
                start_index=0,
                end_index=10,
            )
            text_item = MagicMock(type="text", text="Body", annotations=[ann])
            model_output_step = MagicMock(type="model_output", content=[text_item])
            sources_text = (
                "**Sources:**\n\n"
                "- [usenix.org](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ123)\n"
            )
            sources_item = MagicMock(type="text", text=sources_text, annotations=[])
            sources_step = MagicMock(type="model_output", content=[sources_item])
            fake_interaction = MagicMock(
                status="completed", steps=[model_output_step, sources_step]
            )
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
                "last_interaction": fake_interaction,
            }

            async def fake_resolve(urls, **kwargs):
                return {u: "https://www.usenix.org/paper.pdf" for u in urls}

            monkeypatch.setattr(
                "doxa_research.providers.gemini._resolve_dr_redirects", fake_resolve
            )

            result = await provider._deep_research_get_result("interactions/abc", False)
            assert "## Sources" in result
            assert "usenix.org" in result
            assert "https://www.usenix.org/paper.pdf" in result

        asyncio.run(_run())

    def test_falls_back_to_redirect_url_when_follow_fails(self, monkeypatch):
        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            redirect_url = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ123"
            ann = MagicMock(
                type="url_citation",
                title=None,
                url=redirect_url,
                start_index=0,
                end_index=10,
            )
            text_item = MagicMock(type="text", text="Body", annotations=[ann])
            model_output_step = MagicMock(type="model_output", content=[text_item])
            fake_interaction = MagicMock(status="completed", steps=[model_output_step])
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
                "last_interaction": fake_interaction,
            }

            async def fake_resolve(urls, **kwargs):
                return {u: None for u in urls}

            monkeypatch.setattr(
                "doxa_research.providers.gemini._resolve_dr_redirects", fake_resolve
            )

            result = await provider._deep_research_get_result("interactions/abc", False)
            assert "## Sources" in result
            assert redirect_url in result

        asyncio.run(_run())

    def test_no_sources_block_when_no_annotations(self):
        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            text_item = MagicMock(type="text", text="Body without sources", annotations=[])
            model_output_step = MagicMock(type="model_output", content=[text_item])
            fake_interaction = MagicMock(status="completed", steps=[model_output_step])
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
                "last_interaction": fake_interaction,
            }
            result = await provider._deep_research_get_result("interactions/abc", False)
            assert "## Sources" not in result

        asyncio.run(_run())

    def test_dedupes_by_resolved_url(self, monkeypatch):
        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            ann1 = MagicMock(
                type="url_citation",
                title=None,
                url="https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ123",
                start_index=0,
                end_index=10,
            )
            ann2 = MagicMock(
                type="url_citation",
                title=None,
                url="https://vertexaisearch.cloud.google.com/grounding-api-redirect/BXK456",
                start_index=20,
                end_index=30,
            )
            text_item = MagicMock(type="text", text="Body", annotations=[ann1, ann2])
            model_output_step = MagicMock(type="model_output", content=[text_item])
            fake_interaction = MagicMock(status="completed", steps=[model_output_step])
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
                "last_interaction": fake_interaction,
            }

            async def fake_resolve(urls, **kwargs):
                return {u: "https://www.usenix.org/paper.pdf" for u in urls}

            monkeypatch.setattr(
                "doxa_research.providers.gemini._resolve_dr_redirects", fake_resolve
            )

            result = await provider._deep_research_get_result("interactions/abc", False)
            assert result.count("https://www.usenix.org/paper.pdf") == 1

        asyncio.run(_run())

    def test_dedupes_redirect_urls_before_head_follow(self, monkeypatch):
        """Same redirect URL across many annotations should be HEAD-followed once."""

        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            same_url = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/SAME"
            anns = [
                MagicMock(
                    type="url_citation",
                    title=None,
                    url=same_url,
                    start_index=i * 10,
                    end_index=(i * 10) + 5,
                )
                for i in range(5)
            ]
            text_item = MagicMock(type="text", text="Body", annotations=anns)
            model_output_step = MagicMock(type="model_output", content=[text_item])
            fake_interaction = MagicMock(status="completed", steps=[model_output_step])
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
                "last_interaction": fake_interaction,
            }
            urls_received_by_resolver: list[list[str]] = []

            async def fake_resolve(urls, **kwargs):
                urls_received_by_resolver.append(list(urls))
                return {u: "https://www.usenix.org/paper.pdf" for u in urls}

            monkeypatch.setattr(
                "doxa_research.providers.gemini._resolve_dr_redirects", fake_resolve
            )
            await provider._deep_research_get_result("interactions/abc", False)
            # Even though 5 annotations have the same URL, the resolver was called with ONE URL
            assert len(urls_received_by_resolver) == 1
            assert urls_received_by_resolver[0] == [same_url]

        asyncio.run(_run())


class TestGeminiReconnect:
    """Task 9: reconnect() re-attaches DR state after process restart."""

    def test_reconnect_repopulates_jobs_entry(self):
        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            # Cold start: jobs dict empty
            assert "interactions/abc" not in provider.jobs
            fake_get = AsyncMock(
                return_value=MagicMock(status="in_progress", id="interactions/abc")
            )
            # interactions is read-only on AsyncClient — replace provider.client entirely
            provider.client = MagicMock()
            provider.client.aio.interactions.get = fake_get
            await provider.reconnect("interactions/abc")
            fake_get.assert_awaited_once_with(id="interactions/abc")
            assert "interactions/abc" in provider.jobs
            assert provider.jobs["interactions/abc"]["kind"] == "deep_research"
            assert provider.jobs["interactions/abc"]["interaction_id"] == "interactions/abc"
            assert provider.jobs["interactions/abc"]["last_status"] == "in_progress"
            assert provider.jobs["interactions/abc"]["last_interaction"] is not None

        asyncio.run(_run())

    def test_reconnect_propagates_mapped_error_on_failure(self):
        """A 404 from interactions.get is mapped via _map_gemini_error."""

        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            import httpx

            NotFoundError = _sdk_interactions_errors().NotFoundError

            from doxa_research.errors import ProviderError
            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            req = httpx.Request("GET", "https://example.com")
            resp = httpx.Response(404, request=req)
            exc = NotFoundError("Interaction not found", response=resp, body=None)
            fake_get = AsyncMock(side_effect=exc)
            provider.client = MagicMock()
            provider.client.aio.interactions.get = fake_get
            try:
                await provider.reconnect("interactions/expired")
                raise AssertionError("expected ProviderError")
            except ProviderError as e:
                assert "interaction" in str(e).lower()

        asyncio.run(_run())


class TestGeminiDeepResearchModes:
    """Task 10: 9 gemini_*_research modes are in KNOWN_MODELS."""

    EXPECTED = (
        "gemini_quick_research",
        "gemini_exploration",
        "gemini_deep_dive",
        "gemini_tutorial",
        "gemini_solution",
        "gemini_prd",
        "gemini_tdd",
        "gemini_deep_research",
        "gemini_comparison",
    )

    def test_all_modes_present(self):
        from doxa_research.config import BUILTIN_MODES as KNOWN_MODELS

        for mode in self.EXPECTED:
            assert mode in KNOWN_MODELS, f"Missing mode {mode!r}"

    def test_all_modes_use_dr_agent_and_background_kind(self):
        from doxa_research.config import BUILTIN_MODES as KNOWN_MODELS

        for mode in self.EXPECTED:
            entry = KNOWN_MODELS[mode]
            assert entry["provider"] == "gemini"
            assert entry["kind"] == "background"
            assert entry["model"] == "deep-research-preview-04-2026"


class TestGeminiCancel:
    """Task 8: cancel() invokes interactions.cancel for DR jobs (defensive)."""

    def test_cancel_calls_interactions_cancel(self):
        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
            }
            fake_cancel = AsyncMock(return_value=None)
            provider.client = MagicMock()
            provider.client.aio.interactions.cancel = fake_cancel
            result = await provider.cancel("interactions/abc")
            fake_cancel.assert_awaited_once_with(id="interactions/abc")
            assert result == {"status": "cancelled"}
            assert provider.jobs["interactions/abc"]["cancel_requested"] is True

        asyncio.run(_run())

    def test_cancel_5xx_returns_best_effort_cancelled(self):
        """Per Task 1 spike §6 + Task 8a deferred — cancel may return 5xx.
        Defensive impl: treat as best-effort, runtime SIGINT path still completes."""

        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            import httpx

            InternalServerError = _sdk_interactions_errors().InternalServerError

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            provider.jobs["interactions/abc"] = {
                "kind": "deep_research",
                "interaction_id": "interactions/abc",
            }
            req = httpx.Request("POST", "https://example.com")
            resp = httpx.Response(500, request=req)
            exc = InternalServerError("cancel failed server-side", response=resp, body=None)
            fake_cancel = AsyncMock(side_effect=exc)
            provider.client = MagicMock()
            provider.client.aio.interactions.cancel = fake_cancel
            result = await provider.cancel("interactions/abc")
            # Cancel reports cancelled (best-effort); runtime SIGINT path satisfied.
            # check_status will surface actual state on next poll.
            assert result["status"] == "cancelled"
            assert result.get("best_effort") is True
            assert provider.jobs["interactions/abc"]["cancel_requested"] is True

        asyncio.run(_run())

    def test_cancel_noop_for_immediate_jobs(self):
        """Immediate jobs (chat completion) don't support upstream cancel."""

        async def _run():
            from unittest.mock import MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(api_key="dummy", config={"model": "gemini-2.5-flash-lite"})
            provider.jobs["job-imm"] = {"kind": "immediate", "response": MagicMock()}
            result = await provider.cancel("job-imm")
            assert result["status"] == "cancelled"
            # No upstream call

        asyncio.run(_run())

    def test_cancel_unknown_job_id_upstream_404_returns_not_found(self):
        """cancel with upstream 404 returns not_found (truly nonexistent ID)."""

        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            import httpx

            NotFoundError = _sdk_interactions_errors().NotFoundError

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            req = httpx.Request("POST", "https://example.com")
            resp = httpx.Response(404, request=req)
            exc = NotFoundError("interaction not found", response=resp, body=None)
            fake_cancel = AsyncMock(side_effect=exc)
            provider.client = MagicMock()
            provider.client.aio.interactions.cancel = fake_cancel
            result = await provider.cancel("interactions/unknown")
            fake_cancel.assert_awaited_once_with(id="interactions/unknown")
            assert result["status"] == "not_found"

        asyncio.run(_run())

    def test_cancel_unknown_dr_job_attempts_upstream(self):
        """doxa cancel after process restart: jobs dict is empty but we still try."""

        async def _run():
            from unittest.mock import AsyncMock, MagicMock

            from doxa_research.providers.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key="dummy", config={"model": "deep-research-preview-04-2026"}
            )
            # No prior submit/reconnect — jobs dict empty (post-restart scenario).
            assert "interactions/restarted" not in provider.jobs
            fake_cancel = AsyncMock(return_value=None)
            provider.client = MagicMock()
            provider.client.aio.interactions.cancel = fake_cancel
            result = await provider.cancel("interactions/restarted")
            # The upstream call WAS attempted (not short-circuited to not_found)
            fake_cancel.assert_awaited_once_with(id="interactions/restarted")
            assert result["status"] == "cancelled"

        asyncio.run(_run())
