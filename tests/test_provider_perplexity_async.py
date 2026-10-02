"""P27 — Perplexity async (background deep-research) provider tests.

Covers the asynchronous lifecycle on top of `/v1/async/sonar`: error
mapping for raw httpx2 exceptions and Perplexity HTTP status codes,
plus future submit / check_status / get_result / reconnect / cancel
coverage as those land. Mirrors the structure of `test_oai_background.py`
but against the Perplexity async API instead of OpenAI Responses.

Test slices:
- P27-T04: `_map_perplexity_error_async` covers 401/402/422/429/5xx and
  httpx2.TimeoutException / ConnectError → DoxaError taxonomy.
- P27-TS01..TS05: lifecycle coverage (submit body shape, status mapping,
  get_result extraction, reconnect, cancel) — added as those lifecycle
  methods land.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from doxa_research.errors import (
    APIKeyError,
    APIQuotaError,
    APIRateLimitError,
    DoxaError,
    ProviderError,
)
from doxa_research.providers.perplexity import (
    PerplexityProvider,
    _map_perplexity_error_async,
)


def _make_http_status_error(status: int, body: str = "{}") -> httpx2.HTTPStatusError:
    """Construct an httpx2.HTTPStatusError as the SDK would raise from raise_for_status()."""
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/job-x")
    response = httpx2.Response(status_code=status, content=body.encode(), request=request)
    return httpx2.HTTPStatusError(f"HTTP {status}", request=request, response=response)


# ---------------------------------------------------------------------------
# P27-T04 — _map_perplexity_error_async
# ---------------------------------------------------------------------------


def test_async_map_401_returns_api_key_error() -> None:
    """T04: HTTP 401 maps to APIKeyError('perplexity')."""
    exc = _make_http_status_error(401)
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, APIKeyError)


def test_async_map_401_with_invalid_key_body_returns_friendly_doxa_error() -> None:
    """T04: 401 + invalid-key phrase in body → friendly invalid-key DoxaError.

    Mirrors the sync path defense (perplexity.py:142–151): "key not found"
    and "key invalid" are different user actions, so the async mapper
    distinguishes them too.
    """
    exc = _make_http_status_error(
        401, body='{"error": {"code": "invalid_api_key", "message": "API key is invalid"}}'
    )
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, DoxaError)
    assert "invalid" in str(result).lower()
    # Specifically NOT the missing-key error (different remediation).
    assert not isinstance(result, APIKeyError) or "invalid" in str(result).lower()


def test_async_map_402_returns_api_quota_error() -> None:
    """T04: HTTP 402 (insufficient credits) maps to APIQuotaError."""
    exc = _make_http_status_error(402, body='{"error": {"message": "Insufficient credits"}}')
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, APIQuotaError)


def test_async_map_422_returns_provider_error_with_model_hint() -> None:
    """T04: HTTP 422 (incompatible model) → ProviderError mentioning the model."""
    exc = _make_http_status_error(422, body='{"error": {"message": "Invalid request"}}')
    result = _map_perplexity_error_async(exc, model="sonar-pro")
    assert isinstance(result, ProviderError)
    assert "sonar-pro" in str(result)
    assert "/v1/async/sonar" in str(result) or "async" in str(result).lower()


def test_async_map_404_returns_model_not_found_error_with_models_hint() -> None:
    """HTTP 404 on async maps like the sync NotFoundError branch."""
    exc = _make_http_status_error(404, body='{"error": {"message": "model not found"}}')
    result = _map_perplexity_error_async(exc, model="sonar-deep-research")
    assert isinstance(result, ProviderError)
    assert "sonar-deep-research" in str(result)
    assert "doxa providers models --provider perplexity" in str(result)


def test_async_map_422_unsupported_parameter_uses_specific_hint() -> None:
    """HTTP 422 unsupported-parameter bodies use the shared regex extractor."""
    exc = _make_http_status_error(
        422,
        body='{"error": {"message": "Unsupported parameter: \'temperature\' for this model"}}',
    )
    result = _map_perplexity_error_async(exc, model="sonar-deep-research")
    assert isinstance(result, ProviderError)
    assert "does not support parameter 'temperature'" in str(result)
    assert "provider namespace" in str(result)


def test_async_map_429_returns_rate_limit_error() -> None:
    """T04: HTTP 429 → APIRateLimitError (rate limit, NOT quota).

    Distinguishes from 402 (which is the credit/billing code per Perplexity
    spec §8). 429 is purely throttle.
    """
    exc = _make_http_status_error(429)
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, APIRateLimitError)


def test_async_map_429_with_quota_body_upgrades_to_api_quota_error() -> None:
    """A1 (factor-dedup): 429 + quota markers in body → APIQuotaError, not rate-limit.

    Sync uses _rate_limit_error_is_quota body inspection to upgrade
    RateLimitError → APIQuotaError. Async should do the same when 429 carries
    quota markers in the body — otherwise the two mappers classify the same
    upstream error differently. Markers come from the same vocabulary
    (insufficient_quota, billing, credit, exhausted, no credits, etc.).
    """
    exc = _make_http_status_error(
        429,
        body='{"error": {"code": "insufficient_quota", "message": "Monthly spend limit exceeded"}}',
    )
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, APIQuotaError), (
        f"expected APIQuotaError on 429-with-quota-body, got {type(result).__name__}"
    )


def test_async_map_403_returns_permission_denied_provider_error() -> None:
    """A2 (factor-dedup): HTTP 403 → ProviderError with tier/model-access hint.

    Both sync mappers emit 'Permission denied (check tier / model access).' for
    PermissionDeniedError; async previously fell into the generic HTTP-{status}
    bucket with no hint. This test pins parity.
    """
    exc = _make_http_status_error(403, body='{"error": {"message": "forbidden"}}')
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, ProviderError)
    msg = str(result).lower()
    assert "permission denied" in msg
    assert "tier" in msg or "model access" in msg


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_async_map_5xx_returns_transient_provider_error(status: int) -> None:
    """T04: 5xx → ProviderError with retry hint."""
    exc = _make_http_status_error(status)
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, ProviderError)
    assert "server error" in str(result).lower() or "5xx" in str(result)


def test_async_map_httpx_timeout_returns_provider_error() -> None:
    """T04: httpx2.TimeoutException → ProviderError with timeout language."""
    exc = httpx2.TimeoutException("request timed out")
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, ProviderError)
    assert "timed out" in str(result).lower()


def test_async_map_httpx_connect_error_returns_provider_error() -> None:
    """T04: httpx2.ConnectError → ProviderError with network language."""
    exc = httpx2.ConnectError("DNS resolution failed")
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, ProviderError)
    assert "network" in str(result).lower() or "connection" in str(result).lower()


def test_async_map_unknown_exception_returns_generic_provider_error() -> None:
    """T04: any other exception type → generic ProviderError, not silently swallowed."""
    exc = RuntimeError("something unexpected")
    result = _map_perplexity_error_async(exc)
    assert isinstance(result, ProviderError)
    assert "something unexpected" in str(result).lower() or "unexpected" in str(result).lower()


def test_async_map_verbose_includes_raw_error_text() -> None:
    """T04: verbose=True populates raw_error on ProviderError for diagnostics."""
    exc = _make_http_status_error(500, body='{"error": {"message": "internal"}}')
    result = _map_perplexity_error_async(exc, verbose=True)
    assert isinstance(result, ProviderError)
    assert result.raw_error is not None
    assert result.raw_error  # non-empty


# ---------------------------------------------------------------------------
# P27-TS01 — submit() POST body shape + idempotency
# ---------------------------------------------------------------------------
#
# All tests below patch `provider._async_http` (the raw httpx2 client
# scheduled to be added in __init__ alongside the existing AsyncOpenAI
# `provider.client`). Calls to `submit()` with kind="background" route
# through this httpx2 client; calls with kind="immediate" continue to use
# the existing OpenAI-SDK client and are out of scope here.


def _async_response(
    status_code: int = 202,
    payload: dict[str, Any] | None = None,
) -> httpx2.Response:
    """Build an httpx2.Response with a JSON body and a synthetic Request."""
    request = httpx2.Request("POST", "https://api.perplexity.ai/v1/async/sonar")
    response = httpx2.Response(
        status_code=status_code,
        json=payload or {"id": "req-async-123", "status": "CREATED"},
        request=request,
    )
    return response


def _make_background_provider(
    response: httpx2.Response | None = None,
    extra_config: dict[str, Any] | None = None,
) -> tuple[PerplexityProvider, AsyncMock]:
    """Provider wired with an AsyncMock httpx2 client; returns (provider, post_mock)."""
    config: dict[str, Any] = {
        "model": "sonar-deep-research",
        "kind": "background",
    }
    if extra_config:
        config.update(extra_config)
    provider = PerplexityProvider(api_key="pplx-test", config=config)
    post_mock = AsyncMock(return_value=response or _async_response())
    fake_client = AsyncMock()
    fake_client.post = post_mock
    fake_client.get = AsyncMock(return_value=_async_response())
    provider._async_http = fake_client  # type: ignore[attr-defined]
    return provider, post_mock


def test_async_submit_posts_to_v1_async_sonar() -> None:
    """TS01: submit() with kind=background POSTs to /v1/async/sonar."""
    provider, post = _make_background_provider()
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert post.await_count == 1
    assert post.await_args is not None
    url = post.await_args.args[0] if post.await_args.args else post.await_args.kwargs.get("url", "")
    assert url == "/v1/async/sonar"


def test_async_submit_uses_request_wrapper_shape() -> None:
    """TS01: body matches Perplexity's `{"request": {...}, "idempotency_key": ...}` shape.

    NOT OpenAI's flat shape — the request part is wrapped explicitly.
    """
    provider, post = _make_background_provider()
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert post.await_args is not None
    body = post.await_args.kwargs["json"]
    assert "request" in body, f"missing 'request' wrapper in body: {body}"
    assert "idempotency_key" in body
    assert body["request"]["model"] == "sonar-deep-research"
    assert body["request"]["messages"] == [{"role": "user", "content": "hello"}]


def test_async_submit_idempotency_key_is_uuid4_hex() -> None:
    """TS01: idempotency_key is a 32-char hex string (uuid4().hex)."""
    provider, post = _make_background_provider()
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert post.await_args is not None
    body = post.await_args.kwargs["json"]
    key = body["idempotency_key"]
    assert isinstance(key, str)
    assert len(key) == 32
    int(key, 16)  # raises if not hex


def test_async_submit_idempotency_key_stable_across_retries() -> None:
    """TS01: tenacity retries reuse the SAME idempotency_key (advisor #2).

    Generating a new UUID inside the retried inner would defeat the whole
    point of idempotency. The key must be minted in `submit()` (or any
    outer caller) once, then passed into the retried inner.
    """
    provider, post = _make_background_provider()
    seen_keys: list[str] = []

    async def flaky_post(*args: Any, **kwargs: Any) -> httpx2.Response:
        seen_keys.append(kwargs["json"]["idempotency_key"])
        if len(seen_keys) < 3:
            raise httpx2.ConnectError("transient network failure")
        return _async_response()

    post.side_effect = flaky_post
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert len(seen_keys) == 3, f"expected 3 retry attempts, got {len(seen_keys)}"
    assert len(set(seen_keys)) == 1, f"idempotency_key changed across retries: {seen_keys}"


def test_async_submit_includes_reasoning_effort_from_mode_config() -> None:
    """TS01: config['perplexity']['reasoning_effort'] passes through to body['request']."""
    provider, post = _make_background_provider(
        extra_config={"perplexity": {"reasoning_effort": "high"}}
    )
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert post.await_args is not None
    body = post.await_args.kwargs["json"]
    assert body["request"].get("reasoning_effort") == "high"


def test_async_submit_forwards_provider_namespace_request_options() -> None:
    """P27: background async path forwards Perplexity request options.

    Mirrors the immediate path's arbitrary provider-namespace passthrough so
    custom background modes can constrain search/cost without each key being
    copied by hand.
    """
    provider, post = _make_background_provider(
        extra_config={
            "perplexity": {
                "reasoning_effort": "high",
                "web_search_options": {"search_context_size": "low"},
                "search_domain_filter": ["perplexity.ai"],
                "return_related_questions": True,
            }
        }
    )
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert post.await_args is not None
    body = post.await_args.kwargs["json"]
    request = body["request"]
    assert request["reasoning_effort"] == "high"
    assert request["web_search_options"] == {"search_context_size": "low"}
    assert request["search_domain_filter"] == ["perplexity.ai"]
    assert request["return_related_questions"] is True


def test_async_submit_omits_reasoning_effort_when_unset() -> None:
    """TS01: omitting reasoning_effort from config also omits it from the request body.

    Lets Perplexity's server-side default apply rather than forcing one client-side.
    """
    provider, post = _make_background_provider()  # no perplexity.reasoning_effort
    asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert post.await_args is not None
    body = post.await_args.kwargs["json"]
    assert "reasoning_effort" not in body["request"]


def test_async_submit_with_system_prompt_yields_two_messages() -> None:
    """TS01: system_prompt -> first system message; user prompt -> user message."""
    provider, post = _make_background_provider()
    asyncio.run(
        provider.submit("user content", mode="perplexity_deep_research", system_prompt="be brief")
    )
    assert post.await_args is not None
    body = post.await_args.kwargs["json"]
    assert body["request"]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "user content"},
    ]


def test_async_submit_returns_request_id_as_job_id() -> None:
    """TS01: response.json()['id'] is captured as the job_id and stored in self.jobs."""
    provider, post = _make_background_provider(
        response=_async_response(payload={"id": "req-xyz-789", "status": "CREATED"})
    )
    job_id = asyncio.run(provider.submit("hello", mode="perplexity_deep_research"))
    assert job_id == "req-xyz-789"
    assert "req-xyz-789" in provider.jobs


def test_async_submit_does_not_route_immediate_kind_through_async_path() -> None:
    """Regression: kind='immediate' MUST continue to use the existing P23 sync path.

    P27 must not change the immediate-path lifecycle. Verifies that the
    new kind=background dispatch is the ONLY path that touches the new
    `_async_http` client.
    """
    provider, post = _make_background_provider(
        extra_config={"model": "sonar-pro", "kind": "immediate"}
    )
    # Patch the SDK client too so we can detect which path executes.
    sync_called = {"hit": False}

    async def fake_sync_create(**kwargs: Any) -> Any:
        sync_called["hit"] = True
        # Return a SimpleNamespace that mimics the SDK response shape.
        from types import SimpleNamespace

        return SimpleNamespace(id="sync-id", choices=[], search_results=[])

    from types import SimpleNamespace

    provider.client = SimpleNamespace(  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_sync_create))
    )
    asyncio.run(provider.submit("hello", mode="perplexity_pro"))
    assert sync_called["hit"], "immediate kind should call the SDK client, not _async_http"
    assert post.await_count == 0, "_async_http MUST NOT be called for kind=immediate"


def test_async_submit_provider_init_creates_async_http_client() -> None:
    """T03 part 2: __init__ wires self._async_http as an httpx2.AsyncClient.

    Verified by attribute presence and base_url. Tests below patch this
    attribute, but the real construction must happen in __init__.
    """
    provider = PerplexityProvider(
        api_key="pplx-test", config={"model": "sonar-deep-research", "kind": "background"}
    )
    assert hasattr(provider, "_async_http"), "expected _async_http attribute in __init__"
    assert isinstance(provider._async_http, httpx2.AsyncClient)
    assert str(provider._async_http.base_url).rstrip("/") == "https://api.perplexity.ai"
    asyncio.run(provider._async_http.aclose())


# ---------------------------------------------------------------------------
# P27-TS02 — check_status() async-API status mapping + stale-cache fallback
# ---------------------------------------------------------------------------
#
# Mirrors OpenAI background's check_status contract (status, progress)
# while mapping Perplexity's status enum (CREATED/IN_PROGRESS/COMPLETED/
# FAILED) to Doxa Research's internal enum. Includes the OAI-BG-06/07 stale-cache
# cases so a transient poll error doesn't masquerade as completion (or
# fail to honor a genuinely-completed cached result).


def _status_response(
    status: str = "IN_PROGRESS",
    extra: dict[str, Any] | None = None,
) -> httpx2.Response:
    payload: dict[str, Any] = {"id": "req-async-123", "status": status}
    if extra:
        payload.update(extra)
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    return httpx2.Response(status_code=200, json=payload, request=request)


def _attach_get_response(
    provider: PerplexityProvider,
    response: httpx2.Response | Exception,
) -> AsyncMock:
    """Replace provider._async_http with an AsyncMock whose .get yields `response`."""
    fake_client = AsyncMock()
    if isinstance(response, Exception):
        fake_client.get = AsyncMock(side_effect=response)
    else:
        fake_client.get = AsyncMock(return_value=response)
    provider._async_http = fake_client  # type: ignore[attr-defined]
    return fake_client.get


def _seed_background_job(
    provider: PerplexityProvider,
    job_id: str = "req-async-123",
    cached_status: str = "IN_PROGRESS",
) -> None:
    """Populate provider.jobs as if _submit_async had run."""
    provider.jobs[job_id] = {
        "response_data": {"id": job_id, "status": cached_status},
        "background": True,
        "created_at": __import__("datetime").datetime.now(),
    }


def test_check_status_created_maps_to_queued() -> None:
    """TS02: Perplexity CREATED → {'status': 'queued', 'progress': 0.0}."""
    provider, _ = _make_background_provider()
    _seed_background_job(provider, cached_status="CREATED")
    _attach_get_response(provider, _status_response("CREATED"))
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "queued"
    assert result["progress"] == 0.0


def test_check_status_in_progress_maps_to_running() -> None:
    """TS02: Perplexity IN_PROGRESS → {'status': 'running', 'progress': float}."""
    provider, _ = _make_background_provider()
    _seed_background_job(provider, cached_status="IN_PROGRESS")
    _attach_get_response(provider, _status_response("IN_PROGRESS"))
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "running"
    assert isinstance(result["progress"], float)
    assert 0.0 <= result["progress"] <= 1.0


def test_check_status_completed_caches_response_and_returns_completed() -> None:
    """TS02: Perplexity COMPLETED → cache the response payload AND return progress 1.0.

    Caching is load-bearing — get_result() can avoid a second GET.
    """
    provider, _ = _make_background_provider()
    _seed_background_job(provider)
    full_payload = {
        "id": "req-async-123",
        "status": "COMPLETED",
        "response": {"choices": [{"message": {"content": "answer"}}]},
    }
    _attach_get_response(provider, _status_response("COMPLETED", extra=full_payload))
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "completed"
    assert result["progress"] == 1.0
    cached = provider.jobs["req-async-123"].get("response_data")
    assert cached is not None
    assert cached.get("status") == "COMPLETED"
    assert cached.get("response", {}).get("choices") is not None


def test_check_status_failed_maps_to_permanent_error_with_message() -> None:
    """TS02: Perplexity FAILED → {'status': 'permanent_error', 'error': error_message}."""
    provider, _ = _make_background_provider()
    _seed_background_job(provider)
    _attach_get_response(
        provider,
        _status_response("FAILED", extra={"error_message": "Search service unavailable"}),
    )
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "permanent_error"
    assert "Search service unavailable" in str(result.get("error", ""))


def test_check_status_404_maps_to_permanent_error_with_ttl_hint() -> None:
    """TS02: HTTP 404 on poll → permanent_error mentioning the 7-day TTL.

    Async results expire 7 days after submission per Perplexity spec §5.
    Surfacing the TTL helps users understand "missing job" vs "job failed".
    """
    provider, _ = _make_background_provider()
    _seed_background_job(provider)
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    response = httpx2.Response(status_code=404, content=b"{}", request=request)
    err = httpx2.HTTPStatusError("404", request=request, response=response)
    _attach_get_response(provider, err)
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "permanent_error"
    error_text = str(result.get("error", "")).lower()
    assert (
        "expired" in error_text
        or "ttl" in error_text
        or "7-day" in error_text
        or "7 day" in error_text
    )


@pytest.mark.parametrize("status", [401, 402, 403, 422])
def test_check_status_non_retryable_http_error_maps_to_permanent_error(status: int) -> None:
    """P27 review: non-retryable poll HTTP errors must not burn transient retries."""
    provider, _ = _make_background_provider()
    _seed_background_job(provider)
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    response = httpx2.Response(status_code=status, content=b"{}", request=request)
    err = httpx2.HTTPStatusError(str(status), request=request, response=response)
    _attach_get_response(provider, err)
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "permanent_error"
    assert result["status"] != "transient_error"
    assert result["error_class"] in {
        "APIKeyError",
        "APIQuotaError",
        "ProviderError",
    }


@pytest.mark.parametrize("status", [429, 503])
def test_check_status_retryable_http_error_stays_transient(status: int) -> None:
    """P27 review: rate limits and server errors remain retryable while job is running."""
    provider, _ = _make_background_provider()
    _seed_background_job(provider, cached_status="IN_PROGRESS")
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    response = httpx2.Response(status_code=status, content=b"{}", request=request)
    err = httpx2.HTTPStatusError(str(status), request=request, response=response)
    _attach_get_response(provider, err)
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "transient_error"
    assert result["error_class"] == "HTTPStatusError"


def test_check_status_transient_error_with_stale_in_progress_cache_does_not_complete() -> None:
    """TS02: ConnectError on poll + cached IN_PROGRESS → transient_error.

    Mirrors OAI-BG-06. Critical safety property: a network blip during a
    long-running poll MUST NOT cause us to declare a job done when the
    cached state was IN_PROGRESS. Loss of liveness is preferable to false
    completion.
    """
    provider, _ = _make_background_provider()
    _seed_background_job(provider, cached_status="IN_PROGRESS")
    _attach_get_response(provider, httpx2.ConnectError("network blip"))
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "transient_error"
    assert result["status"] != "completed", (
        "stale IN_PROGRESS cache must not be reported as completed"
    )


def test_check_status_transient_error_with_stale_completed_cache_returns_completed() -> None:
    """TS02: ConnectError on poll + cached COMPLETED → completed (safe fallback).

    Mirrors OAI-BG-07. Once we've already seen COMPLETED for this job in a
    prior poll, a transient error on a later (redundant) poll should not
    flip the state back to error. The cached completion is authoritative.
    """
    provider, _ = _make_background_provider()
    _seed_background_job(provider, cached_status="COMPLETED")
    _attach_get_response(provider, httpx2.ConnectError("network blip"))
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "completed"
    assert result["progress"] == 1.0


def test_check_status_http_5xx_with_stale_completed_cache_returns_completed() -> None:
    """B1 (TS02): HTTPStatusError 5xx + cached COMPLETED → completed (stale-cache fallback).

    Mirrors test_check_status_transient_error_with_stale_completed_cache_returns_completed
    but for the HTTPStatusError branch — a 5xx blip after a previously-cached
    COMPLETED state must not regress the runner's polling loop. Per OAI-BG-07
    parity (OpenAI fires the stale-cache fallback in its transient-SDK-error
    branch; Perplexity must do the same in its HTTPStatusError 5xx branch).
    """
    provider, _ = _make_background_provider()
    _seed_background_job(provider, cached_status="COMPLETED")
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    response = httpx2.Response(status_code=503, content=b"{}", request=request)
    err = httpx2.HTTPStatusError("503", request=request, response=response)
    _attach_get_response(provider, err)
    result = asyncio.run(provider.check_status("req-async-123"))
    assert result["status"] == "completed"
    assert result["progress"] == 1.0


def test_check_status_unknown_job_id_returns_not_found() -> None:
    """TS02: job_id absent from self.jobs → not_found (no HTTP attempted)."""
    provider, post = _make_background_provider()  # post mock here is for submit, unused
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=_status_response("COMPLETED"))
    provider._async_http = fake_client  # type: ignore[attr-defined]
    result = asyncio.run(provider.check_status("never-seen"))
    assert result["status"] == "not_found"
    assert fake_client.get.await_count == 0, "no GET should fire for unknown job_id"


def test_check_status_immediate_path_returns_completed_without_polling() -> None:
    """Regression: P23 sync immediate path keeps its 'already completed' behavior.

    For kind=immediate jobs (where `submit()` already ran chat.completions
    synchronously), `self.jobs[job_id]` lacks `background=True`. check_status
    must return {completed, 1.0} without GETting the async API.
    """
    provider = PerplexityProvider(
        api_key="pplx-test", config={"model": "sonar", "kind": "immediate"}
    )
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=_status_response("COMPLETED"))
    provider._async_http = fake_client  # type: ignore[attr-defined]
    provider.jobs["sync-job-x"] = {
        "response": object(),  # opaque sync SDK response (irrelevant for status)
        "created_at": __import__("datetime").datetime.now(),
        # no "background" key — that's how the sync path stores jobs
    }
    result = asyncio.run(provider.check_status("sync-job-x"))
    assert result["status"] == "completed"
    assert result["progress"] == 1.0
    assert fake_client.get.await_count == 0, "sync jobs must NOT trigger an async-API GET"


# ---------------------------------------------------------------------------
# P27-TS03 / TS03b / TS03c — get_result() extraction
# ---------------------------------------------------------------------------
#
# Async-API responses are dict-shaped (no SDK typedefs) so extraction uses
# dict access throughout. Output composition appends, in order:
#   1. main content from response.choices[0].message.content
#   2. truncation warning (if heuristic fires) — placed near the truncation
#      site so users see it before the metadata blocks
#   3. ## Sources block from response.search_results, URL-deduped
#   4. ## Cost block from response.usage.cost.total_cost (4 decimals)
#
# Per Open-Question resolution at P27 kickoff, ## Cost is always shown
# (no --show-cost flag). Per audit gap, truncation gets explicit tests.


def _completed_payload(
    content: str = "Final answer.",
    finish_reason: str = "stop",
    search_results: list[dict[str, Any]] | None = None,
    total_cost: float | None = 0.4137,
) -> dict[str, Any]:
    """Build a fully-formed COMPLETED payload from the async API."""
    payload: dict[str, Any] = {
        "id": "req-async-123",
        "status": "COMPLETED",
        "response": {
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "search_results": search_results or [],
            "usage": ({"cost": {"total_cost": total_cost}} if total_cost is not None else {}),
        },
    }
    return payload


def _seed_completed_job(
    provider: PerplexityProvider,
    job_id: str = "req-async-123",
    payload: dict[str, Any] | None = None,
) -> None:
    provider.jobs[job_id] = {
        "response_data": payload or _completed_payload(),
        "background": True,
        "created_at": __import__("datetime").datetime.now(),
    }


def test_get_result_extracts_content_via_dict_access() -> None:
    """TS03: content comes from response.choices[0].message.content via dict access."""
    provider, _ = _make_background_provider()
    _seed_completed_job(provider, payload=_completed_payload(content="Researched answer."))
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "Researched answer." in text


def test_get_result_appends_sources_block_with_url_dedup() -> None:
    """TS03: ## Sources lists each unique URL exactly once."""
    provider, _ = _make_background_provider()
    payload = _completed_payload(
        content="Body.",
        search_results=[
            {"url": "https://a.example/x", "title": "A"},
            {"url": "https://b.example/y", "title": "B"},
            {"url": "https://a.example/x", "title": "A duplicate"},  # same URL
        ],
    )
    _seed_completed_job(provider, payload=payload)
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "## Sources" in text
    assert text.count("https://a.example/x") == 1, "duplicate URL should be deduped"
    assert "https://b.example/y" in text


def test_get_result_omits_sources_block_when_no_search_results() -> None:
    """TS03: empty search_results → no `## Sources` section emitted."""
    provider, _ = _make_background_provider()
    _seed_completed_job(provider, payload=_completed_payload(search_results=[]))
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "## Sources" not in text


def test_get_result_appends_cost_footer_with_four_decimals() -> None:
    """TS03b: ## Cost\\n\\nTotal: $X.XXXX with 4-decimal precision."""
    provider, _ = _make_background_provider()
    _seed_completed_job(provider, payload=_completed_payload(total_cost=1.23456))
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "## Cost" in text
    # Per spec: 4 decimals after the dollar sign.
    assert "Total: $1.2346" in text or "Total: $1.2345" in text  # rounding tolerance


def test_get_result_omits_cost_footer_when_usage_missing() -> None:
    """TS03b: missing usage.cost.total_cost → no Cost section.

    Defensive: if Perplexity ever stops returning cost data we render the
    answer without an empty `Total: $0.0000` line.
    """
    provider, _ = _make_background_provider()
    _seed_completed_job(provider, payload=_completed_payload(total_cost=None))
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "## Cost" not in text


def test_get_result_warns_on_truncation_heuristic_match() -> None:
    """TS03c: finish_reason='stop' AND tail lacks terminal punctuation → warning.

    Heuristic for the documented 25–50% truncation rate (research §17).
    Conservative (false-positive-tolerant) per Open Question at P27 kickoff.
    """
    provider, _ = _make_background_provider()
    payload = _completed_payload(
        content="The conclusion is that we should investigate further when",
        finish_reason="stop",
    )
    _seed_completed_job(provider, payload=payload)
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "Possible truncation" in text or "truncation" in text.lower()


def test_get_result_does_not_warn_when_content_ends_with_terminal_punctuation() -> None:
    """TS03c: finish_reason='stop' + tail with terminal punctuation → no warning."""
    provider, _ = _make_background_provider()
    payload = _completed_payload(
        content="The conclusion is clear: investigate further.",
        finish_reason="stop",
    )
    _seed_completed_job(provider, payload=payload)
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "truncation" not in text.lower()


def test_get_result_does_not_warn_when_finish_reason_is_not_stop() -> None:
    """TS03c: finish_reason != 'stop' → no truncation warning regardless of punctuation.

    Other finish_reasons (length, content_filter, etc.) carry their own
    semantics; the truncation heuristic is specifically about the documented
    'stop with no terminal punctuation' Perplexity bug.
    """
    provider, _ = _make_background_provider()
    payload = _completed_payload(content="Cut off abruptly", finish_reason="length")
    _seed_completed_job(provider, payload=payload)
    text = asyncio.run(provider.get_result("req-async-123"))
    assert "truncation" not in text.lower()


def test_get_result_uses_cached_completed_response_without_refetch() -> None:
    """TS03: a cached COMPLETED response is authoritative; no second GET needed."""
    provider, _ = _make_background_provider()
    _seed_completed_job(provider)
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=_status_response("COMPLETED"))
    provider._async_http = fake_client  # type: ignore[attr-defined]
    asyncio.run(provider.get_result("req-async-123"))
    assert fake_client.get.await_count == 0, "cached COMPLETED must not trigger refetch"


def test_get_result_fetches_when_cached_state_is_not_completed() -> None:
    """TS03: cached IN_PROGRESS forces a fresh fetch (even though normally check_status

    is called first; defensive against direct callers).
    """
    provider, _ = _make_background_provider()
    # Seed with IN_PROGRESS — get_result must refetch to find the actual completion.
    provider.jobs["req-async-123"] = {
        "response_data": {"id": "req-async-123", "status": "IN_PROGRESS"},
        "background": True,
        "created_at": __import__("datetime").datetime.now(),
    }
    completed_payload = _completed_payload(content="Fresh answer.")
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    response = httpx2.Response(status_code=200, json=completed_payload, request=request)
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=response)
    provider._async_http = fake_client  # type: ignore[attr-defined]
    text = asyncio.run(provider.get_result("req-async-123"))
    assert fake_client.get.await_count == 1
    assert "Fresh answer." in text


def test_get_result_unknown_job_raises_provider_error() -> None:
    """TS03: get_result for an unknown job_id raises ProviderError (not silent)."""
    provider, _ = _make_background_provider()
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_result("never-seen"))


def test_get_result_immediate_path_unchanged() -> None:
    """Regression: P23 sync immediate path uses the existing _render_answer_with_sources.

    For kind=immediate jobs the cached SDK response is already the answer;
    P27 must not change that flow.
    """
    from types import SimpleNamespace

    provider = PerplexityProvider(
        api_key="pplx-test", config={"model": "sonar", "kind": "immediate"}
    )
    sdk_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Sync answer."))],
        search_results=[],
    )
    provider.jobs["sync-job"] = {
        "response": sdk_response,
        "created_at": __import__("datetime").datetime.now(),
        # no "background" key
    }
    text = asyncio.run(provider.get_result("sync-job"))
    assert "Sync answer." in text


# ---------------------------------------------------------------------------
# P27-TS04 — reconnect()
# ---------------------------------------------------------------------------
#
# reconnect() re-attaches the in-process job table to a server-side
# request_id after a fresh process start. The runner calls this in
# `doxa resume <op_id>` before re-entering the polling loop.


def test_reconnect_happy_path_repopulates_jobs() -> None:
    """TS04: GET success populates self.jobs[job_id] with background=True."""
    provider, _ = _make_background_provider()
    payload = {"id": "req-async-123", "status": "IN_PROGRESS"}
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-async-123")
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(
        return_value=httpx2.Response(status_code=200, json=payload, request=request)
    )
    provider._async_http = fake_client  # type: ignore[attr-defined]
    asyncio.run(provider.reconnect("req-async-123"))
    assert "req-async-123" in provider.jobs
    job_info = provider.jobs["req-async-123"]
    assert job_info.get("background") is True
    assert job_info.get("response_data", {}).get("status") == "IN_PROGRESS"


def test_reconnect_404_raises_provider_error_with_ttl_hint() -> None:
    """TS04: HTTP 404 → ProviderError mentioning the 7-day TTL.

    Async jobs expire 7 days after submission per Perplexity spec §5;
    the user-visible error must explain that.
    """
    provider, _ = _make_background_provider()
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/expired")
    response = httpx2.Response(status_code=404, content=b"{}", request=request)
    err = httpx2.HTTPStatusError("404", request=request, response=response)
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=err)
    provider._async_http = fake_client  # type: ignore[attr-defined]
    with pytest.raises(ProviderError) as info:
        asyncio.run(provider.reconnect("expired"))
    msg = str(info.value).lower()
    assert "7" in msg and ("ttl" in msg or "expir" in msg or "day" in msg)


# ---------------------------------------------------------------------------
# P27-TS05 — cancel()
# ---------------------------------------------------------------------------
#
# T01 verified Perplexity has no upstream cancel endpoint. cancel() returns
# {"status": "upstream_unsupported"} per cancel.py:126 contract — that
# exact dict-key spelling is consumed by the renderer.


def test_cancel_returns_upstream_unsupported_with_no_http_call() -> None:
    """TS05: cancel() returns {status: upstream_unsupported}; no HTTP fired.

    Perplexity has no DELETE endpoint and no CANCELLED status (T01); the
    runner uses this return shape to mark the local checkpoint cancelled
    via cancel.py:126's "upstream cancel not supported" rendering.
    """
    provider, post = _make_background_provider()
    fake_client = AsyncMock()
    fake_client.post = post
    fake_client.get = AsyncMock(return_value=_async_response())
    fake_client.delete = AsyncMock(return_value=_async_response())
    provider._async_http = fake_client  # type: ignore[attr-defined]
    result = asyncio.run(provider.cancel("req-async-123"))
    assert result == {"status": "upstream_unsupported"}, (
        "cancel.py:126 keys exactly on this dict shape"
    )
    assert fake_client.post.await_count == 0
    assert fake_client.get.await_count == 0
    assert fake_client.delete.await_count == 0


def test_cancel_works_for_unknown_job_id() -> None:
    """TS05: cancel() doesn't require the job to be in self.jobs.

    A user can issue `doxa cancel <op_id>` after a process restart before
    reconnect runs; cancel() should still return the unsupported sentinel
    rather than blowing up on KeyError.
    """
    provider, _ = _make_background_provider()
    result = asyncio.run(provider.cancel("never-seen"))
    assert result == {"status": "upstream_unsupported"}


# ---------------------------------------------------------------------------
# P27-TS04b / T12 — request_id round-trip + end-to-end resume contract
# ---------------------------------------------------------------------------
#
# Combines T12 (Perplexity request_id round-trips through JSON checkpoint)
# with TS04b (after a simulated process restart, reconnect+check_status+
# get_result reaches a completed state). Done in-process — no subprocess —
# to avoid the cost and complexity of mocking httpx2 across a fork. The
# runner's polling-loop contract is exercised directly via the same
# methods _run_polling_loop calls in production.


def test_request_id_round_trips_through_json_checkpoint_shape() -> None:
    """T12: a Perplexity request_id survives JSON serialize/deserialize unchanged.

    The runner persists provider job_ids verbatim in the checkpoint format
    (`providers.<name>.job_id`). Perplexity's request_id is the upstream
    job identifier — typed as a free-form string; this test pins the
    invariant that JSON serialization doesn't mangle it.
    """
    import json as _json

    request_id = "req-d7e3a8b2-c1f4-4e92-a0f8-2b9d8c3f7e1a"
    checkpoint_dict = {
        "operation_id": "research-20260503-120000-aaaaaaaaaaaaaaaa",
        "status": "running",
        "providers": {
            "perplexity": {
                "status": "running",
                "job_id": request_id,
            },
        },
    }
    serialized = _json.dumps(checkpoint_dict)
    rehydrated = _json.loads(serialized)
    assert rehydrated["providers"]["perplexity"]["job_id"] == request_id


def test_full_resume_lifecycle_after_simulated_process_restart() -> None:
    """TS04b: submit -> simulated crash -> reconnect -> check_status -> get_result.

    Mirrors OpenAI's RES-01 pattern but at the provider-contract level (no
    subprocess). Verifies that after a process restart drops in-memory
    state, the runner-level resume flow can repopulate `self.jobs` from
    just the request_id and continue polling to completion.

    What this proves end-to-end:
      1. submit()      -> mock POST returns request_id; jobs[request_id] populated
      2. <crash>       -> drop the provider; persist only the request_id
      3. reconnect()   -> mock GET returns IN_PROGRESS; jobs repopulated
      4. check_status()-> mock GET returns COMPLETED with full payload
      5. get_result()  -> renders content + ## Sources + ## Cost
    """
    submit_payload = {"id": "req-resume-abc-123", "status": "CREATED"}
    request_id = submit_payload["id"]

    # --- Phase 1: submit ----------------------------------------------------
    submit_provider, post_mock = _make_background_provider(
        response=_async_response(payload=submit_payload),
        extra_config={"perplexity": {"reasoning_effort": "high"}},
    )
    returned_id = asyncio.run(
        submit_provider.submit(
            "Brief prompt", mode="perplexity_deep_research", system_prompt="be brief"
        )
    )
    assert returned_id == request_id
    assert post_mock.await_count == 1

    # Capture the persisted "checkpoint" — only the request_id needs to
    # survive across the simulated crash. (The runner persists more, but
    # request_id is the load-bearing field for reconnect.)
    persisted_request_id = returned_id

    # --- Phase 2: simulated process restart ---------------------------------
    # Drop the original provider entirely; emulate a fresh `doxa resume`.
    del submit_provider

    fresh_provider = PerplexityProvider(
        api_key="pplx-test",
        config={
            "model": "sonar-deep-research",
            "kind": "background",
            "perplexity": {"reasoning_effort": "high"},
        },
    )
    assert persisted_request_id not in fresh_provider.jobs, (
        "fresh provider must start with empty jobs"
    )

    # --- Phase 3: reconnect from request_id ---------------------------------
    reconnect_response = httpx2.Response(
        status_code=200,
        json={"id": persisted_request_id, "status": "IN_PROGRESS"},
        request=httpx2.Request(
            "GET", f"https://api.perplexity.ai/v1/async/sonar/{persisted_request_id}"
        ),
    )
    completed_payload = _completed_payload(
        content="Resumed answer with research findings.",
        search_results=[{"url": "https://x.example/1", "title": "Source One"}],
        total_cost=1.3201,
    )
    completed_response = httpx2.Response(
        status_code=200,
        json=completed_payload,
        request=httpx2.Request(
            "GET", f"https://api.perplexity.ai/v1/async/sonar/{persisted_request_id}"
        ),
    )

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=[reconnect_response, completed_response])
    fresh_provider._async_http = fake_client  # type: ignore[attr-defined]

    asyncio.run(fresh_provider.reconnect(persisted_request_id))
    assert persisted_request_id in fresh_provider.jobs
    assert fresh_provider.jobs[persisted_request_id].get("background") is True

    # --- Phase 4: check_status (simulates the runner's polling loop) -------
    status = asyncio.run(fresh_provider.check_status(persisted_request_id))
    assert status["status"] == "completed"
    assert status["progress"] == 1.0

    # --- Phase 5: get_result -----------------------------------------------
    text = asyncio.run(fresh_provider.get_result(persisted_request_id))
    assert "Resumed answer" in text
    assert "## Sources" in text
    assert "https://x.example/1" in text
    assert "## Cost" in text
    assert "Total: $1.3201" in text

    # Two GETs total: one for reconnect + one for check_status. get_result
    # used the cached completed payload (no third GET).
    assert fake_client.get.await_count == 2, (
        f"expected 2 GETs (reconnect + check_status); got {fake_client.get.await_count}"
    )


def test_resume_after_404_raises_provider_error_with_ttl_message() -> None:
    """TS04b: resume of an expired (>7-day) job surfaces a clear error.

    If the user attempts `doxa resume <op_id>` more than 7 days after the
    original submit, the upstream returns 404. The runner must surface a
    ProviderError naming the TTL — not silently fall through to a
    "running" status.
    """
    fresh_provider = PerplexityProvider(
        api_key="pplx-test",
        config={"model": "sonar-deep-research", "kind": "background"},
    )

    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/async/sonar/req-expired")
    response = httpx2.Response(status_code=404, content=b"{}", request=request)
    err = httpx2.HTTPStatusError("404", request=request, response=response)

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=err)
    fresh_provider._async_http = fake_client  # type: ignore[attr-defined]

    with pytest.raises(ProviderError) as info:
        asyncio.run(fresh_provider.reconnect("req-expired"))
    msg = str(info.value).lower()
    assert "7" in msg and ("expir" in msg or "day" in msg or "ttl" in msg)
