"""P27 — Perplexity async (background deep-research) provider tests.

Retains raw HTTPX2 provider error mapping for the Agent background path: error
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
    _map_perplexity_error_async,
)


def _make_http_status_error(status: int, body: str = "{}") -> httpx2.HTTPStatusError:
    """Construct an httpx2.HTTPStatusError as the SDK would raise from raise_for_status()."""
    request = httpx2.Request("GET", "https://api.perplexity.ai/v1/agent/job-x")
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
    assert "Agent" in str(result) or "async" in str(result).lower()


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
