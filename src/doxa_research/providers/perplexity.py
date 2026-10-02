"""Perplexity Sonar provider — synchronous immediate-path implementation.

Uses the OpenAI Python SDK in compatibility mode against
`https://api.perplexity.ai`. Per-request Perplexity-specific options live
under the `perplexity` mode-config namespace and are forwarded via
`extra_body` (any other shape raises TypeError per the SDK).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx2
import openai
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from doxa_research.config import is_background_model
from doxa_research.errors import (
    APIKeyError,
    APIQuotaError,
    APIRateLimitError,
    DoxaError,
    ModeKindMismatchError,
    ProviderError,
)
from doxa_research.providers._helpers import (
    _extract_unsupported_param,
    _invalid_key_doxaerror,
    debug_print_empty_response,
    render_sources_block,
)
from doxa_research.providers._status import _translate_provider_status
from doxa_research.providers.base import Citation, ResearchProvider, StreamEvent

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _split_partial_tag_suffix(text: str, tag: str) -> tuple[str, str]:
    """Split off the longest suffix that could become `tag` in a later chunk."""
    max_len = min(len(text), len(tag) - 1)
    for size in range(max_len, 0, -1):
        suffix = text[-size:]
        if tag.startswith(suffix):
            return text[:-size], suffix
    return text, ""


class _ThinkStreamParser:
    """Stateful parser for Perplexity reasoning tags split across chunks."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_reasoning = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buffer += text
        segments: list[tuple[str, str]] = []

        while self._buffer:
            if self._in_reasoning:
                end = self._buffer.find(_THINK_CLOSE)
                if end == -1:
                    break
                if end:
                    segments.append(("reasoning", self._buffer[:end]))
                self._buffer = self._buffer[end + len(_THINK_CLOSE) :]
                self._in_reasoning = False
                continue

            start = self._buffer.find(_THINK_OPEN)
            if start != -1:
                if start:
                    segments.append(("text", self._buffer[:start]))
                self._buffer = self._buffer[start + len(_THINK_OPEN) :]
                self._in_reasoning = True
                continue

            ready, pending = _split_partial_tag_suffix(self._buffer, _THINK_OPEN)
            if ready:
                segments.append(("text", ready))
            self._buffer = pending
            break

        return segments

    def finish(self) -> list[tuple[str, str]]:
        if not self._buffer:
            return []
        if self._in_reasoning:
            text = f"{_THINK_OPEN}{self._buffer}"
        else:
            text = self._buffer
        self._buffer = ""
        self._in_reasoning = False
        return [("text", text)]


_PROVIDER_NAME_PERPLEXITY = "perplexity"
_INVALID_KEY_PHRASES = ("invalid api key", "incorrect api key", "invalid_api_key")

# Provider-status → Doxa Research-status template for the async API. Caller fills in
# `error` for the FAILED branch from payload["error_message"]. Unknown
# statuses fall through to the helper's default permanent_error.
_PERPLEXITY_STATUS_TABLE: dict[str, dict[str, Any]] = {
    "CREATED": {"status": "queued", "progress": 0.0},
    "IN_PROGRESS": {"status": "running", "progress": 0.5},
    "COMPLETED": {"status": "completed", "progress": 1.0},
    "FAILED": {"status": "permanent_error"},
}


def _rate_limit_error_is_quota(exc: BaseException) -> bool:
    """Return True when a rate-limit-shaped Perplexity error signals exhausted credits."""
    body = getattr(exc, "body", None) or {}
    parts = [str(body), str(exc)]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            for key in ("code", "type", "message"):
                value = err.get(key)
                if value is not None:
                    parts.append(str(value))
    text = " ".join(parts).lower()
    quota_markers = (
        "insufficient_quota",
        "quota",
        "billing",
        "credit",
        "credits",
        "monthly spend",
        "exhausted",
        "no credits",
        "blocked",
    )
    return any(marker in text for marker in quota_markers)


def _map_perplexity_error(
    exc: BaseException, model: str | None = None, verbose: bool = False
) -> DoxaError:
    """Map an openai-SDK exception (or other) raised against Perplexity to a DoxaError.

    Mirrors `_map_openai_error` shape but uses provider name "perplexity" and
    keeps the suggestion text Perplexity-specific.
    """
    raw = str(exc) if verbose else None

    if isinstance(exc, openai.AuthenticationError):
        body = getattr(exc, "body", None) or {}
        combined = (str(exc) + " " + str(body)).lower()
        if any(phrase in combined for phrase in _INVALID_KEY_PHRASES):
            return _invalid_key_doxaerror("Perplexity", "https://www.perplexity.ai/settings/api")
        return APIKeyError(_PROVIDER_NAME_PERPLEXITY)

    if isinstance(exc, openai.RateLimitError):
        if _rate_limit_error_is_quota(exc):
            return APIQuotaError(_PROVIDER_NAME_PERPLEXITY)
        return APIRateLimitError(_PROVIDER_NAME_PERPLEXITY)

    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            "Permission denied (check tier / model access).",
            raw_error=raw,
        )

    if isinstance(exc, openai.NotFoundError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            f"Model '{model}' not found. Please check available models with "
            f"'doxa providers models --provider perplexity'",
            raw_error=raw,
        )

    # A1 belt-and-suspenders: any APIStatusError with status_code == 402
    # routes to APIQuotaError. The openai SDK doesn't ship a PaymentRequired
    # exception subclass, so a 402 from Perplexity (their credit-exhaustion
    # code per docs §8) may surface here as a bare APIStatusError or as
    # BadRequestError depending on SDK version. Checked BEFORE BadRequestError
    # so a 402 surfacing as BadRequestError still routes to APIQuotaError.
    if getattr(exc, "status_code", None) == 402:
        return APIQuotaError(_PROVIDER_NAME_PERPLEXITY)

    if isinstance(exc, openai.BadRequestError):
        param = _extract_unsupported_param(str(exc))
        if param:
            return ProviderError(
                _PROVIDER_NAME_PERPLEXITY,
                f"Perplexity does not support parameter '{param}' for this model. "
                "Remove it from the mode config or its provider namespace.",
                raw_error=raw,
            )
        # A4: use {model!r} for parity with _map_perplexity_error_async — repr
        # quoting is more correct for free-form upstream model strings.
        hint = f" (model: {model!r})" if model else ""
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            f"Bad request{hint}. Check model name and request shape.",
            raw_error=raw,
        )

    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            "Request timed out. Try again, or raise --timeout.",
            raw_error=raw,
        )

    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            "Network connection error reaching api.perplexity.ai.",
            raw_error=raw,
        )

    if isinstance(exc, openai.InternalServerError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            "Perplexity server error (5xx). Retry shortly.",
            raw_error=raw,
        )

    if isinstance(exc, openai.APIError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            f"Perplexity API error: {exc}",
            raw_error=raw,
        )

    return ProviderError(
        _PROVIDER_NAME_PERPLEXITY,
        f"Unexpected error: {exc}",
        raw_error=raw,
    )


def _map_perplexity_error_async(
    exc: BaseException, model: str | None = None, verbose: bool = False
) -> DoxaError:
    """Map an httpx2-raised exception or HTTP status code from `/v1/async/sonar` to a DoxaError.

    Counterpart to `_map_perplexity_error` for the async path: the OpenAI
    SDK doesn't know about `/v1/async/sonar`, so the async submit/poll uses
    raw httpx2 and surfaces httpx2 exceptions plus Perplexity's documented
    HTTP status codes. Translates them into the same Doxa Research error taxonomy
    (APIKeyError / APIQuotaError / APIRateLimitError / ProviderError) the
    runner already understands.

    Status code mapping (per `research/perplexity-deep-research-api.v1.md` §8
    and the live llms.txt verified at P27-T01):
      * 401 -> APIKeyError, or a friendly invalid-key DoxaError if the
        body identifies the key as rejected (vs. simply missing).
      * 402 -> APIQuotaError (Perplexity uses 402 for credit exhaustion).
      * 422 -> ProviderError with a model hint, since the most common cause
        is using a non-deep-research model on the async endpoint.
      * 429 -> APIRateLimitError (purely throttle; quota lives at 402 here).
      * 5xx -> transient ProviderError with a retry suggestion.
      * Other status -> generic ProviderError.

    httpx2 exception mapping:
      * TimeoutException -> ProviderError("Request timed out...").
      * ConnectError    -> ProviderError("Network connection error...").
      * Anything else   -> generic ProviderError; never silently swallowed.
    """
    raw = str(exc) if verbose else None

    if isinstance(exc, httpx2.HTTPStatusError):
        status = exc.response.status_code
        body_text = ""
        try:
            body_text = exc.response.text
        except Exception:  # pragma: no cover - defensive: response text unavailable
            body_text = ""
        body_lower = body_text.lower()

        # A5 (P27 factor-dedup): async inspects exc.response.text only; the
        # sync mapper inspects exc.body + str(exc) because openai SDK exceptions
        # carry different surfaces. Both inspections are correct for their
        # respective contexts; do not try to unify the two.
        if status == 401:
            if any(phrase in body_lower for phrase in _INVALID_KEY_PHRASES):
                return _invalid_key_doxaerror(
                    "Perplexity", "https://www.perplexity.ai/settings/api"
                )
            return APIKeyError(_PROVIDER_NAME_PERPLEXITY)
        if status == 402:
            return APIQuotaError(_PROVIDER_NAME_PERPLEXITY)
        if status == 403:
            # A2: parity with _map_perplexity_error's PermissionDeniedError
            # handler — emit the same hint so users see the tier/model-access
            # diagnostic on both sync and async paths.
            return ProviderError(
                _PROVIDER_NAME_PERPLEXITY,
                "Permission denied (check tier / model access).",
                raw_error=raw,
            )
        if status == 404:
            return ProviderError(
                _PROVIDER_NAME_PERPLEXITY,
                f"Model '{model}' not found. Please check available models with "
                f"'doxa providers models --provider perplexity'",
                raw_error=raw,
            )
        if status == 422:
            param = _extract_unsupported_param(body_text)
            if param:
                return ProviderError(
                    _PROVIDER_NAME_PERPLEXITY,
                    f"Perplexity does not support parameter '{param}' for this model. "
                    "Remove it from the mode config or its provider namespace.",
                    raw_error=raw,
                )
            hint = f" (model: {model!r})" if model else ""
            return ProviderError(
                _PROVIDER_NAME_PERPLEXITY,
                f"Invalid async request{hint}. Model may not support /v1/async/sonar.",
                raw_error=raw,
            )
        if status == 429:
            # A1: upgrade to APIQuotaError when the body carries quota
            # markers (parity with _rate_limit_error_is_quota in the sync
            # path). Without this, Perplexity returning 429 + insufficient_quota
            # would be classified as a rate limit while the sync mapper would
            # call it a quota error — same upstream, different taxonomy.
            quota_markers = (
                "insufficient_quota",
                "quota",
                "billing",
                "credit",
                "credits",
                "monthly spend",
                "exhausted",
                "no credits",
                "blocked",
            )
            if any(marker in body_lower for marker in quota_markers):
                return APIQuotaError(_PROVIDER_NAME_PERPLEXITY)
            return APIRateLimitError(_PROVIDER_NAME_PERPLEXITY)
        if 500 <= status < 600:
            return ProviderError(
                _PROVIDER_NAME_PERPLEXITY,
                "Perplexity server error (5xx). Retry shortly.",
                raw_error=raw,
            )
        # A3 (P27 factor-dedup): no explicit 400 BadRequest branch — Perplexity's
        # async API documents 422 (not 400) for invalid requests, so a 400 falls
        # through to the generic HTTP-{status} bucket on purpose. Keeping it
        # explicit so future maintainers don't add a redundant 400 branch.
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            f"HTTP {status} from Perplexity async API: {body_text[:200]}",
            raw_error=raw,
        )

    if isinstance(exc, httpx2.TimeoutException):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            "Request timed out. Try again, or raise --timeout.",
            raw_error=raw,
        )

    if isinstance(exc, httpx2.ConnectError):
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            "Network connection error reaching api.perplexity.ai.",
            raw_error=raw,
        )

    return ProviderError(
        _PROVIDER_NAME_PERPLEXITY,
        f"Unexpected error: {exc}",
        raw_error=raw,
    )


PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

_DIRECT_SDK_KEYS_PERPLEXITY: tuple[str, ...] = (
    "max_tokens",
    "temperature",
    "top_p",
    "stop",
    "response_format",
)


class PerplexityProvider(ResearchProvider):
    """Perplexity research implementation (synchronous Sonar)."""

    def __init__(self, api_key: str, config: dict[str, Any] | None = None):
        self.api_key = api_key
        self.config = config or {}
        self.model = self.config.get("model", "sonar")
        self.jobs: dict[str, dict[str, Any]] = {}

        timeout = self.config.get("timeout", 30.0)
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=PERPLEXITY_BASE_URL,
            timeout=httpx2.Timeout(timeout, connect=5.0),
        )
        # Raw httpx2 client for the async API (P27): /v1/async/sonar lives
        # outside the OpenAI SDK's surface, so the background lifecycle uses
        # this client instead of self.client. Tests patch this attribute via
        # AsyncMock; production code constructs a real AsyncClient here.
        self._async_http = httpx2.AsyncClient(
            base_url=PERPLEXITY_BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx2.Timeout(timeout, connect=5.0),
        )

    def is_implemented(self) -> bool:
        return True

    def implementation_status(self) -> str | None:
        return None

    async def list_models(self) -> list[dict[str, Any]]:
        return [
            {"id": "sonar", "created": 1700000000, "owned_by": "perplexity"},
            {"id": "sonar-pro", "created": 1700000000, "owned_by": "perplexity"},
            {
                "id": "sonar-reasoning-pro",
                "created": 1700000000,
                "owned_by": "perplexity",
            },
            {
                "id": "sonar-deep-research",
                "created": 1700000000,
                "owned_by": "perplexity",
            },
        ]

    def _validate_kind_for_model(self, mode: str) -> None:
        """Refuse runs whose declared `kind` contradicts the model's required kind.

        Two directions, both raised BEFORE any HTTP call:

        1. `kind="immediate"` + DR model (e.g., `sonar-deep-research`) —
           DR models require Perplexity's async API. P23's TS07 covers this.
        2. `kind="background"` + non-DR model (e.g., `sonar-pro`) — only DR
           models accept `/v1/async/sonar`; the upstream HTTP-422s otherwise.
           P27's TS06 covers this. Perplexity is stricter than OpenAI here:
           OpenAI lets you force-background any model, so OpenAIProvider only
           checks direction (1).
        """
        declared = self.config.get("kind")
        model_is_background = is_background_model(self.model)
        if declared == "immediate" and model_is_background:
            raise ModeKindMismatchError(
                mode_name=mode,
                model=self.model,
                declared_kind="immediate",
                required_kind="background",
            )
        if declared == "background" and not model_is_background:
            raise ModeKindMismatchError(
                mode_name=mode,
                model=self.model,
                declared_kind="background",
                required_kind="immediate",
            )

    def _build_messages(self, prompt: str, system_prompt: str | None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_extra_body(self) -> dict[str, Any]:
        """Forward `config['perplexity'].*` keys through to extra_body.

        Direct OpenAI-SDK kwargs also live under `config['perplexity']`, but
        are consumed by `_build_request_params()` and intentionally excluded
        from extra_body.

        Defaults applied when not configured:
        - web_search_options.search_context_size = "medium"
        - stream_mode = "concise"
        """
        perplexity_cfg: dict[str, Any] = dict(self.config.get("perplexity") or {})
        for key in _DIRECT_SDK_KEYS_PERPLEXITY:
            perplexity_cfg.pop(key, None)

        explicit_extra_body = perplexity_cfg.pop("extra_body", {}) or {}
        if not isinstance(explicit_extra_body, dict):
            explicit_extra_body = {}

        web_search_options = dict(perplexity_cfg.pop("web_search_options", {}) or {})
        web_search_options.setdefault("search_context_size", "medium")

        stream_mode = perplexity_cfg.pop("stream_mode", None) or "concise"

        extra_body: dict[str, Any] = {
            "web_search_options": web_search_options,
            "stream_mode": stream_mode,
        }
        extra_body.update(explicit_extra_body)
        extra_body.update(perplexity_cfg)
        return extra_body

    def _build_request_params(self, prompt: str, system_prompt: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(prompt, system_prompt),
            "extra_body": self._build_extra_body(),
        }
        perplexity_cfg = self.config.get("perplexity") or {}
        if not isinstance(perplexity_cfg, dict):
            perplexity_cfg = {}
        # Resolution order (P24 follow-up #3 + P33 root-providers-level passthrough):
        #   1. self.config["perplexity"][key]  -- mode-level [modes.X.perplexity] namespace
        #   2. self.config[key]                -- root [providers.perplexity] level (flat)
        for key in _DIRECT_SDK_KEYS_PERPLEXITY:
            if key in perplexity_cfg:
                params[key] = perplexity_cfg[key]
            elif key in self.config:
                params[key] = self.config[key]
        return params

    async def submit(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ) -> str:
        """Submit a research request; routes by declared `kind`.

        - `kind="background"` (P27, sonar-deep-research) -> `_submit_async`
          (POST /v1/async/sonar; returns the upstream request_id as job_id).
        - Anything else (P23 immediate path) -> the existing one-shot
          /chat/completions submit, unchanged.
        """
        self._validate_kind_for_model(mode)
        if self.config.get("kind") == "background":
            return await self._submit_async(prompt, mode, system_prompt, verbose)
        try:
            response = await self._submit_with_retry(prompt, system_prompt)
        except ModeKindMismatchError:
            raise
        except (openai.APIError, Exception) as exc:
            raise _map_perplexity_error(exc, model=self.model, verbose=verbose) from exc

        job_id = (
            getattr(response, "id", None)
            or f"perplexity-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        self.jobs[job_id] = {"response": response, "created_at": datetime.now()}
        return job_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((openai.APITimeoutError, openai.APIConnectionError)),
        reraise=True,
    )
    async def _submit_with_retry(self, prompt: str, system_prompt: str | None) -> Any:
        params = self._build_request_params(prompt, system_prompt)
        return await self.client.chat.completions.create(**params)

    def _build_async_request_body(
        self, prompt: str, system_prompt: str | None, idempotency_key: str
    ) -> dict[str, Any]:
        """Build the /v1/async/sonar POST body with the request wrapper.

        Wrapper shape is Perplexity-specific (NOT OpenAI's flat shape) per
        https://docs.perplexity.ai/api-reference/async-chat-completions.
        Forwards Perplexity request options from the `perplexity` config
        namespace into the request part. `model` and `messages` stay owned by
        Doxa Research so provider-namespace options cannot rewrite the structural
        request.
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        request_part: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        perp_cfg = dict(self.config.get("perplexity") or {})
        for key, value in perp_cfg.items():
            if key in {"model", "messages"}:
                continue
            request_part[key] = value
        return {"request": request_part, "idempotency_key": idempotency_key}

    async def _submit_async(
        self, prompt: str, mode: str, system_prompt: str | None, verbose: bool
    ) -> str:
        """POST /v1/async/sonar; capture upstream request_id; map errors.

        idempotency_key is generated ONCE here and reused across tenacity
        retries — minting a fresh key per attempt would defeat idempotency.
        """
        idempotency_key = uuid4().hex
        body = self._build_async_request_body(prompt, system_prompt, idempotency_key)
        try:
            response = await self._submit_async_with_retry(body)
        except (httpx2.HTTPStatusError, httpx2.HTTPError, Exception) as exc:
            raise _map_perplexity_error_async(exc, model=self.model, verbose=verbose) from exc

        payload = response.json()
        request_id = payload.get("id")
        if not request_id:
            raise ProviderError(
                _PROVIDER_NAME_PERPLEXITY,
                "Async submit response missing 'id' field",
                raw_error=str(payload) if verbose else None,
            )
        self.jobs[request_id] = {
            "response_data": payload,
            "background": True,
            "created_at": datetime.now(),
        }
        return request_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((httpx2.ConnectError, httpx2.TimeoutException)),
        reraise=True,
    )
    async def _submit_async_with_retry(self, body: dict[str, Any]) -> httpx2.Response:
        """Inner retryable POST. Raises raw httpx2 exceptions; outer maps."""
        response = await self._async_http.post("/v1/async/sonar", json=body)
        response.raise_for_status()
        return response

    async def stream(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Translate Perplexity's streaming chunks into StreamEvent."""
        self._validate_kind_for_model(mode)
        params = self._build_request_params(prompt, system_prompt)
        params["stream"] = True

        try:
            stream = await self.client.chat.completions.create(**params)
        except ModeKindMismatchError:
            raise
        except (openai.APIError, Exception) as exc:
            raise _map_perplexity_error(exc, model=self.model, verbose=verbose) from exc

        accumulated = ""
        is_reasoning_model = "reasoning" in self.model
        last_search_results: list[Any] = []

        think_parser = _ThinkStreamParser() if is_reasoning_model else None

        try:
            async for chunk in stream:
                sr = getattr(chunk, "search_results", None)
                if sr:
                    last_search_results = list(sr)

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if not content:
                    continue

                # Cumulative-content guard: if `content` starts with what we've
                # already seen, the API is sending cumulative state per chunk;
                # peel off only the new tail.
                if accumulated and content.startswith(accumulated):
                    new_text = content[len(accumulated) :]
                    accumulated = content
                else:
                    new_text = content
                    accumulated += content

                if not new_text:
                    continue

                if think_parser is not None:
                    for kind, body in think_parser.feed(new_text):
                        if not body:
                            continue
                        if kind == "reasoning":
                            yield StreamEvent(kind="reasoning", text=body)
                        else:
                            yield StreamEvent(kind="text", text=body)
                else:
                    yield StreamEvent(kind="text", text=new_text)

            if think_parser is not None:
                for kind, body in think_parser.finish():
                    if body:
                        if kind == "reasoning":
                            yield StreamEvent(kind="reasoning", text=body)
                        else:
                            yield StreamEvent(kind="text", text=body)
        except (openai.APIError, Exception) as exc:
            raise _map_perplexity_error(exc, model=self.model, verbose=verbose) from exc

        seen_urls: set[str] = set()
        for entry in last_search_results:
            url = _entry_get(entry, "url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = _entry_get(entry, "title") or url
            yield StreamEvent(
                kind="citation",
                text=str(title),
                citation=Citation(title=str(title), url=str(url)),
            )

        yield StreamEvent(kind="done", text="")

    async def check_status(self, job_id: str) -> dict[str, Any]:
        """Status of an in-flight job. Routes by job_info['background'].

        Sync (P23 immediate) jobs were already complete when submit() returned;
        report `completed` with no upstream call. Background (P27 async) jobs
        GET /v1/async/sonar/{job_id} and translate Perplexity's status enum.

        Stale-cache fallback on transient errors mirrors OAI-BG-06/07: a poll
        ConnectError/Timeout that finds a cached COMPLETED state should still
        report completed (the cached completion is authoritative); a transient
        error with a cached IN_PROGRESS/CREATED state must NOT report completed.
        """
        if job_id not in self.jobs:
            return {"status": "not_found", "error": "Job not found"}
        job_info = self.jobs[job_id]
        # B4 (P27 factor-dedup): P18 non-background shortcut — kept symmetric
        # with OpenAIProvider for defense-in-depth. TODO(P19): remove both
        # shortcuts when the immediate-kind path no longer transits
        # check_status at all.
        if not job_info.get("background", False):
            # P23 immediate path — submit() already returned the full response.
            return {"status": "completed", "progress": 1.0}
        return await self._poll_async_job(job_id, job_info)

    async def _poll_async_job(self, job_id: str, job_info: dict[str, Any]) -> dict[str, Any]:
        """Single poll attempt against /v1/async/sonar/{job_id} with translation.

        Stale-cache fallback fires on transient errors AND on HTTPStatusError 5xx
        (per OAI-BG-07 parity, P27 factor-dedup B1): a network or server blip
        immediately after a previously-cached COMPLETED state must not regress
        the runner's polling loop back to transient_error.
        """
        try:
            response = await self._async_http.get(f"/v1/async/sonar/{job_id}")
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                return {
                    "status": "permanent_error",
                    "error": "Job expired (7-day TTL) or not found server-side",
                }
            mapped = _map_perplexity_error_async(exc, model=self.model)
            if 500 <= status < 600 or isinstance(mapped, APIRateLimitError):
                # B1: stale-cache fallback on retryable HTTP errors — a
                # previously-cached COMPLETED is authoritative even when a
                # later poll hits a server blip or ordinary rate limit.
                cached = job_info.get("response_data") or {}
                if cached.get("status") == "COMPLETED":
                    return {"status": "completed", "progress": 1.0}
                return {
                    "status": "transient_error",
                    "error": f"HTTP {status}",
                    # B2: derive class name from type(exc) instead of hardcoding
                    # the literal string, matching the convention used by the
                    # other except branches and OpenAIProvider.check_status.
                    "error_class": type(exc).__name__,
                }
            return {
                "status": "permanent_error",
                "error": str(mapped),
                "error_class": type(mapped).__name__,
            }
        except (httpx2.ConnectError, httpx2.TimeoutException) as exc:
            cached = job_info.get("response_data") or {}
            if cached.get("status") == "COMPLETED":
                return {"status": "completed", "progress": 1.0}
            return {
                "status": "transient_error",
                "error": str(exc),
                "error_class": type(exc).__name__,
            }
        except Exception as exc:  # noqa: BLE001 - never silently swallow novel errors
            # Intentional: named exception branches use bare str(exc); the
            # catch-all prepends `({type(exc).__name__})` so users can
            # distinguish a known exception class from an unexpected one in
            # error logs. (P27 factor-dedup B3 — kept as intentional divergence.)
            cached = job_info.get("response_data") or {}
            if cached.get("status") == "COMPLETED":
                return {"status": "completed", "progress": 1.0}
            return {
                "status": "transient_error",
                "error": f"Unexpected error ({type(exc).__name__}): {exc}",
                "error_class": type(exc).__name__,
            }

        payload = response.json()
        status_str = payload.get("status", "")
        # Always cache the latest payload so get_result() and the stale-cache
        # fallback have an authoritative reference.
        job_info["response_data"] = payload

        translated = _translate_provider_status(status_str, _PERPLEXITY_STATUS_TABLE)
        if status_str == "FAILED":
            translated["error"] = payload.get("error_message") or "Perplexity job FAILED"
        return translated

    async def reconnect(self, job_id: str) -> None:
        """Re-attach to an existing async job after a process restart.

        Called by `doxa resume <op_id>` before the runner re-enters the
        polling loop. Repopulates self.jobs[job_id] from a fresh GET; a 404
        means the 7-day TTL elapsed (or the id is wrong) and we surface
        that specifically.
        """
        try:
            response = await self._async_http.get(f"/v1/async/sonar/{job_id}")
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProviderError(
                    _PROVIDER_NAME_PERPLEXITY,
                    f"Job {job_id!r} not found. Async results expire 7 days after submission.",
                ) from exc
            raise _map_perplexity_error_async(exc, model=self.model) from exc
        except (httpx2.ConnectError, httpx2.TimeoutException, Exception) as exc:
            raise _map_perplexity_error_async(exc, model=self.model) from exc

        payload = response.json()
        self.jobs[job_id] = {
            "response_data": payload,
            "background": True,
            "created_at": datetime.now(),
        }

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Best-effort cancel — Perplexity has no upstream cancel API.

        T01 verified against the live llms.txt and research §5: the only
        documented endpoints are POST /v1/async/sonar (submit), GET
        /v1/async/sonar (list), GET /v1/async/sonar/{id} (retrieve). No
        DELETE, no /cancel, no CANCELLED status. We return the sentinel
        consumed by cancel.py:126 so the runner marks the local checkpoint
        cancelled and prints "upstream cancel not supported".
        """
        return {"status": "upstream_unsupported"}

    async def get_result(self, job_id: str, verbose: bool = False) -> str:
        """Final answer text for a completed job. Routes by job_info['background'].

        Sync (P23 immediate) jobs delegate to _render_answer_with_sources which
        operates on the OpenAI-SDK response object. Background (P27 async)
        jobs use the dict-shaped payload cached by check_status, fetching
        fresh if the cached state isn't COMPLETED yet.
        """
        if job_id not in self.jobs:
            raise ProviderError(_PROVIDER_NAME_PERPLEXITY, f"Unknown job_id: {job_id}")
        job_info = self.jobs[job_id]
        if not job_info.get("background", False):
            return _render_answer_with_sources(job_info["response"], verbose=verbose)
        return await self._get_async_result(job_id, job_info, verbose)

    async def _get_async_result(self, job_id: str, job_info: dict[str, Any], verbose: bool) -> str:
        """Compose user-facing output from a completed async-API payload.

        Order: content -> truncation warning (placed near the truncation site)
        -> ## Sources -> ## Cost. Sources and Cost are conditional; truncation
        warning is conservative-by-design (false positives are user-ignorable).
        """
        payload = job_info.get("response_data") or {}
        if payload.get("status") != "COMPLETED":
            try:
                response = await self._async_http.get(f"/v1/async/sonar/{job_id}")
                response.raise_for_status()
            except (httpx2.HTTPStatusError, httpx2.HTTPError, Exception) as exc:
                raise _map_perplexity_error_async(exc, model=self.model, verbose=verbose) from exc
            payload = response.json()
            job_info["response_data"] = payload

        response_part = payload.get("response") or {}
        return _format_async_response(response_part)


def _format_async_response(response: dict[str, Any]) -> str:
    """Build the user-facing string for a completed async response."""
    choices = response.get("choices") or []
    content = ""
    finish_reason = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict):
            content = message.get("content") or ""
        finish_reason = choices[0].get("finish_reason") or ""

    parts: list[str] = [content]

    if _is_likely_truncated(content, finish_reason):
        parts.append("\n\n> ⚠ Possible truncation: response may be incomplete.")

    sources = _format_async_sources_block(response.get("search_results") or [])
    if sources:
        parts.append(f"\n\n{sources}")

    cost = _format_async_cost_block(response.get("usage") or {})
    if cost:
        parts.append(cost)

    return "".join(parts)


def _is_likely_truncated(content: str, finish_reason: str) -> bool:
    """Conservative truncation heuristic: stop with no terminal punctuation.

    The documented Perplexity bug (research §17) is that ~25-50% of responses
    finish with finish_reason='stop' but mid-sentence. We treat the absence
    of terminal punctuation at the rstripped tail as the signal. Conservative
    (false-positives tolerated) per Open Question resolution at P27 kickoff.
    """
    if finish_reason != "stop":
        return False
    stripped = content.rstrip()
    if not stripped:
        return False
    last_char = stripped[-1]
    return last_char not in ".!?\")]>}*`'"


def _format_async_sources_block(search_results: list[Any]) -> str:
    """Markdown `## Sources` list, URL-deduped. Empty input -> empty string."""
    citations: list[Citation] = []
    for entry in search_results:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or ""
        if not url:
            continue
        title = entry.get("title") or url
        citations.append(Citation(title=str(title), url=str(url)))
    return render_sources_block(citations)


def _format_async_cost_block(usage: dict[str, Any]) -> str:
    """`## Cost\\n\\nTotal: $X.XXXX` from usage.cost.total_cost (4 decimals)."""
    cost_obj = usage.get("cost")
    if not isinstance(cost_obj, dict):
        return ""
    total = cost_obj.get("total_cost")
    if total is None:
        return ""
    try:
        amount = float(total)
    except (TypeError, ValueError):
        return ""
    return f"\n\n## Cost\n\nTotal: ${amount:.4f}"


def _render_answer_with_sources(response: Any, verbose: bool = False) -> str:
    """Extract content + append a deduped `## Sources` block from search_results.

    When ``verbose`` is True and the response carries no content, emit a debug
    ladder to stderr (model_dump_json -> __dict__ -> repr). Mirrors openai.py's
    pattern so an empty Perplexity response is not silently swallowed.
    """
    choices = getattr(response, "choices", None) or []
    content = ""
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "") or ""

    if verbose and not content and response is not None:
        debug_print_empty_response(response, provider_label="Perplexity")

    search_results = getattr(response, "search_results", None) or []
    citations: list[Citation] = []
    for entry in search_results:
        url = _entry_get(entry, "url") or ""
        if not url:
            continue
        title = _entry_get(entry, "title") or url
        citations.append(Citation(title=str(title), url=str(url)))

    sources = render_sources_block(citations)
    if not sources:
        return content
    return f"{content}\n\n{sources}" if content else sources


def _entry_get(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)
