"""Perplexity provider — synchronous Sonar and durable Agent background research.

Uses the OpenAI Python SDK in compatibility mode against
`https://api.perplexity.ai`. Per-request Perplexity-specific options live
under the `perplexity` mode-config namespace and are forwarded via
`extra_body` (any other shape raises TypeError per the SDK).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import quote
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

# Agent status translation. Failed/incomplete details come from the nested error.
# Unknown statuses are rejected before entering this table.
_PERPLEXITY_STATUS_TABLE: dict[str, dict[str, Any]] = {
    "queued": {"status": "queued", "progress": 0.0},
    "in_progress": {"status": "running", "progress": 0.5},
    "completed": {"status": "completed", "progress": 1.0},
    "failed": {"status": "permanent_error"},
    "incomplete": {"status": "permanent_error"},
    "cancelled": {"status": "cancelled"},
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
    """Map an httpx2-raised exception or HTTP status code from `/v1/agent` to a DoxaError.

    Counterpart to `_map_perplexity_error` for the async path: the OpenAI
    SDK doesn't know about `/v1/agent`, so the async submit/poll uses
    raw httpx2 and surfaces httpx2 exceptions plus Perplexity's documented
    HTTP status codes. Translates them into the same Doxa Research error taxonomy
    (APIKeyError / APIQuotaError / APIRateLimitError / ProviderError) the
    runner already understands.

    Status code mapping (retained provider error taxonomy):
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
                f"Invalid async request{hint}. Check the Agent preset and request fields.",
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
        # Other non-retryable request errors retain their exact HTTP status.
        return ProviderError(
            _PROVIDER_NAME_PERPLEXITY,
            f"HTTP {status} from Perplexity Agent API: {body_text[:200]}",
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
    """Synchronous Sonar compatibility and raw HTTPX2 Agent background research."""

    def __init__(self, api_key: str, config: dict[str, Any] | None = None):
        self.api_key = api_key
        self.config = config or {}
        self.model = self.config.get("model", "sonar")
        self._routing_model = self.model
        self.jobs: dict[str, dict[str, Any]] = {}

        timeout = self.config.get("timeout", 30.0)
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.config.get("base_url") or PERPLEXITY_BASE_URL,
            timeout=httpx2.Timeout(timeout, connect=5.0),
        )
        # Raw HTTPX2 client for Agent background research: /v1/agent lives
        # outside the OpenAI SDK's surface, so the background lifecycle uses
        # this client instead of self.client. Tests patch this attribute via
        # AsyncMock; production code constructs a real AsyncClient here.
        self._async_http = httpx2.AsyncClient(
            base_url=self.config.get("base_url") or PERPLEXITY_BASE_URL,
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
           The compatibility alias selects the Agent background API.
        2. `kind="background"` + non-DR model (e.g., `sonar-pro`) — only DR
           compatibility alias selects Agent research; immediate Sonar stays separate.
           P27's TS06 covers this. Perplexity is stricter than OpenAI here:
           OpenAI lets you force-background any model, so OpenAIProvider only
           checks direction (1).
        """
        declared = self.config.get("kind")
        model_is_background = is_background_model(self._routing_model)
        if declared == "immediate" and model_is_background:
            raise ModeKindMismatchError(
                mode_name=mode,
                model=self._routing_model,
                declared_kind="immediate",
                required_kind="background",
            )
        if declared == "background" and not model_is_background:
            raise ModeKindMismatchError(
                mode_name=mode,
                model=self._routing_model,
                declared_kind="background",
                required_kind="immediate",
            )

        if declared == "background" and self._routing_model != "sonar-deep-research":
            raise _agent_error(
                "Agent background research supports the sonar-deep-research compatibility alias; choose that mode with an Agent preset"
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
          (POST /v1/agent; persists an agent:-marked response ID).
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

    def _build_agent_request_body(self, prompt: str, system_prompt: str | None) -> dict[str, Any]:
        """Translate supported legacy options into the flat Agent API request."""
        if any(self.config.get(key) is not None for key in ("stop", "stop_sequences")):
            raise _agent_error("Agent API does not support legacy stop/stop_sequences")
        options = dict(self.config.get("perplexity") or {})
        routing_model = options.pop("model", None)
        if routing_model is not None and routing_model != "sonar-deep-research":
            raise _agent_error(
                "Agent background mode requires the sonar-deep-research compatibility alias"
            )
        extra = options.pop("extra_body", {})
        if not isinstance(extra, dict):
            raise _agent_error("Agent extra_body must be a mapping")
        options = {**extra, **options}
        for name in ("temperature", "top_p", "max_output_tokens", "max_tokens", "response_format"):
            if name not in options and self.config.get(name) is not None:
                options[name] = self.config[name]
        direct = {
            "preset",
            "max_steps",
            "max_output_tokens",
            "temperature",
            "top_p",
            "response_format",
            "tools",
            "reasoning",
        }
        filters = {
            "search_domain_filter",
            "search_recency_filter",
            "search_after_date_filter",
            "search_before_date_filter",
            "last_updated_after_filter",
            "last_updated_before_filter",
        }
        known = (
            direct
            | filters
            | {"max_tokens", "reasoning_effort", "web_search_options", "num_search_results"}
        )
        unsupported = set(options) - known
        if unsupported:
            raise _agent_error(
                "Unsupported Agent API options: " + ", ".join(sorted(unsupported)),
                suggestion="Use preset/max_steps/max_output_tokens or Agent tools/reasoning; legacy Sonar-only options cannot be forwarded.",
            )
        body: dict[str, Any] = {
            "input": prompt,
            "background": True,
            "store": True,
            "stream": False,
            "preset": "high",
        }
        if system_prompt:
            body["instructions"] = system_prompt
        body.update({key: value for key, value in options.items() if key in direct})
        if "max_tokens" in options:
            if (
                "max_output_tokens" in options
                and options["max_tokens"] != options["max_output_tokens"]
            ):
                raise _agent_error("Conflicting max_tokens and max_output_tokens")
            body["max_output_tokens"] = options["max_tokens"]
        if "reasoning_effort" in options:
            effort = options["reasoning_effort"]
            reasoning = body.get("reasoning") or {}
            if not isinstance(reasoning, dict) or (
                "effort" in reasoning and reasoning["effort"] != effort
            ):
                raise _agent_error("Conflicting Agent reasoning settings")
            body["reasoning"] = {**reasoning, "effort": effort}
        if "response_format" in body:
            fmt = body["response_format"]
            if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
                raise _agent_error(
                    "Agent response_format supports json_schema only; legacy regex/json_object formats cannot be forwarded"
                )
        reasoning = body.get("reasoning")
        # OpenAPI lists six efforts; the official fast preset also sends "none".
        # Accept that documented value without removing schema-supported efforts.
        if reasoning is not None and (
            not isinstance(reasoning, dict)
            or set(reasoning) - {"effort"}
            or reasoning.get("effort")
            not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        ):
            raise _agent_error(
                "Agent reasoning.effort must be none/minimal/low/medium/high/xhigh/max"
            )
        if body["preset"] not in {"fast", "low", "medium", "high", "xhigh"}:
            raise _agent_error("Unknown Agent preset")
        for key, maximum in (("max_steps", 100), ("max_output_tokens", None)):
            if key in body and (
                type(body[key]) is not int
                or body[key] < 1
                or (maximum is not None and body[key] > maximum)
            ):
                raise _agent_error(
                    f"Invalid Agent {key}; expected a positive integer"
                    + (" at most 100" if maximum else "")
                )
        search = dict(options.get("web_search_options") or {})
        if set(search) - {"search_context_size", "user_location"}:
            raise _agent_error("Unsupported legacy web_search_options; use Agent tools")
        if filters & options.keys():
            search["filters"] = {key: options[key] for key in filters if key in options}
        if "num_search_results" in options:
            search["max_results"] = options["num_search_results"]
        if search:
            if "tools" in body:
                raise _agent_error(
                    "Configure search through tools or legacy search options, not both"
                )
            # Per-tool overrides merge with the preset; do not add unrelated tools.
            body["tools"] = [{"type": "web_search", **search}]
        return body

    async def _submit_async(
        self, prompt: str, mode: str, system_prompt: str | None, verbose: bool
    ) -> str:
        """Create one durable Agent request; ambiguous failures must never resubmit."""
        body = self._build_agent_request_body(prompt, system_prompt)
        try:
            response = await self._submit_agent_once(body)
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
                raise _agent_error(
                    "Agent creation outcome has no usable ID; no retry was made",
                    suggestion="Inspect your Perplexity dashboard before submitting again.",
                )
            try:
                _agent_response_id("agent:" + payload["id"])
            except ProviderError as exc:
                raise _agent_error(
                    "Agent creation outcome has no usable ID; no retry was made",
                    suggestion="Inspect your Perplexity dashboard before submitting again.",
                ) from exc
        except ProviderError:
            raise
        except Exception as exc:
            mapped = _map_perplexity_error_async(exc, model=self.model, verbose=verbose)
            if isinstance(
                exc,
                (
                    httpx2.TimeoutException,
                    httpx2.NetworkError,
                    httpx2.RemoteProtocolError,
                    ValueError,
                ),
            ) or (isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code >= 500):
                raise _agent_error(
                    "Agent submission outcome is unknown; no retry was made.",
                    suggestion="Inspect your Perplexity dashboard before submitting again to avoid a duplicate paid job.",
                    raw_error=str(exc) if verbose else None,
                ) from exc
            raise mapped from exc
        job_id = "agent:" + payload["id"]
        self.jobs[job_id] = {
            "response_data": payload,
            "background": True,
            "validated": False,
            "created_at": datetime.now(),
        }
        return job_id

    async def _submit_agent_once(self, body: dict[str, Any]) -> httpx2.Response:
        """Raw HTTPX2 POST; the Agent API does not document idempotent creation."""
        response = await self._async_http.post("/v1/agent", json=body)
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
        GET /v1/agent/{id} and translate the Agent status enum.

        Stale-cache fallback on transient errors mirrors OAI-BG-06/07: a poll
        ConnectError/Timeout that finds a cached completed state should still
        report completed (the cached completion is authoritative); a transient
        error with a cached in_progress/queued state must NOT report completed.
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
        """Poll a validated Agent identity, preserving authoritative cached completion."""
        try:
            upstream_id = _agent_response_id(job_id)
            response = await self._async_http.get(f"/v1/agent/{quote(upstream_id, safe='')}")
            response.raise_for_status()
            payload = _decode_agent_response(response, upstream_id)
        except ProviderError as exc:
            return {"status": "permanent_error", "error": str(exc)}
        except Exception as exc:
            if isinstance(exc, httpx2.HTTPStatusError):
                status = exc.response.status_code
                if status == 404:
                    return {
                        "status": "permanent_error",
                        "error": "Agent response not found; check the account and stored response ID",
                    }
                mapped = _map_perplexity_error_async(exc, model=self.model)
                transient = 500 <= status < 600 or isinstance(mapped, APIRateLimitError)
            else:
                mapped = exc
                transient = isinstance(
                    exc, (httpx2.TimeoutException, httpx2.NetworkError, httpx2.RemoteProtocolError)
                )
            if (
                transient
                and job_info.get("validated")
                and (job_info.get("response_data") or {}).get("status") == "completed"
            ):
                return {"status": "completed", "progress": 1.0}
            return {
                "status": "transient_error" if transient else "permanent_error",
                "error": str(mapped),
                "error_class": type(mapped).__name__,
            }
        job_info["response_data"] = payload
        job_info["validated"] = True
        self.model = payload.get("model") or self._routing_model
        translated = _translate_provider_status(payload["status"], _PERPLEXITY_STATUS_TABLE)
        if payload["status"] in {"failed", "incomplete"}:
            error = payload.get("error") or {}
            translated["error"] = error.get("message") if isinstance(error, dict) else None
            translated["error"] = translated["error"] or f"Agent response {payload['status']}"
        return translated

    async def reconnect(self, job_id: str) -> None:
        """Restore an Agent checkpoint without creating a new upstream request."""
        upstream_id = _agent_response_id(job_id)
        try:
            response = await self._async_http.get(f"/v1/agent/{quote(upstream_id, safe='')}")
            response.raise_for_status()
            payload = _decode_agent_response(response, upstream_id)
        except ProviderError:
            raise
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise _agent_error(
                    "Agent response not found; check the account and stored response ID"
                ) from exc
            raise _map_perplexity_error_async(exc, model=self.model) from exc
        except Exception as exc:
            raise _map_perplexity_error_async(exc, model=self.model) from exc
        self.jobs[job_id] = {
            "response_data": payload,
            "background": True,
            "validated": True,
            "created_at": datetime.now(),
        }
        self.model = payload.get("model") or self._routing_model

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Request upstream cancellation; acknowledgment does not prove completion."""
        upstream_id = _agent_response_id(job_id)
        path = f"/v1/agent/{quote(upstream_id, safe='')}"
        try:
            response = await self._async_http.post(path + "/cancel")
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise _agent_error(
                    "Agent response not found; check the account and stored response ID"
                ) from exc
            if exc.response.status_code == 400:
                await self.reconnect(job_id)
                status = self.jobs[job_id]["response_data"]["status"]
                if status in {"completed", "cancelled"}:
                    return {"status": status}
                raise _agent_error(f"Cancellation rejected; Agent response is {status}") from exc
            raise _map_perplexity_error_async(exc, model=self.model) from exc
        except Exception as exc:
            raise _map_perplexity_error_async(exc, model=self.model) from exc
        try:
            acknowledgment = response.json()
        except ValueError as exc:
            raise _agent_error("Malformed Agent cancellation acknowledgment") from exc
        if (
            not isinstance(acknowledgment, dict)
            or acknowledgment.get("response_id") != upstream_id
            or acknowledgment.get("status") != "cancelling"
        ):
            raise _agent_error(
                "Unconfirmed Agent cancellation acknowledgment; poll the stored response ID"
            )
        return {"status": "cancelling"}

    async def get_result(self, job_id: str, verbose: bool = False) -> str:
        """Render only typed assistant output from a completed Agent response."""
        if job_id not in self.jobs:
            raise _agent_error(f"Unknown job_id: {job_id}")
        job_info = self.jobs[job_id]
        if not job_info.get("background", False):
            return _render_answer_with_sources(job_info["response"], verbose=verbose)
        payload = job_info.get("response_data") or {}
        if not job_info.get("validated") or payload.get("status") != "completed":
            await self.reconnect(job_id)
            payload = self.jobs[job_id]["response_data"]
        if payload["status"] != "completed":
            raise _agent_error(f"Agent answer is not completed: {payload['status']}")
        return _format_agent_response(payload)


def _agent_error(
    message: str, suggestion: str | None = None, raw_error: str | None = None
) -> ProviderError:
    error = ProviderError("perplexity", message, raw_error=raw_error)
    if suggestion is not None:
        error.suggestion = suggestion
    return error


def _agent_response_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not job_id.startswith("agent:"):
        raise _agent_error(
            "Legacy Sonar checkpoint cannot reconnect or cancel through Agent API.",
            suggestion="Async Sonar was retired. No new request was submitted; start a new research operation only if you intend a new paid job.",
        )
    response_id = job_id[len("agent:") :]
    if (
        not response_id
        or response_id in {".", ".."}
        or any(ord(char) < 33 or ord(char) == 127 for char in response_id)
    ):
        raise _agent_error("Malformed Agent checkpoint response ID")
    return response_id


def _decode_agent_response(
    response: httpx2.Response, expected_id: str | None = None
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise _agent_error(
            "Malformed Agent JSON response; no new submission was attempted"
        ) from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("id"), str)
        or not payload["id"]
        or payload.get("status") not in _PERPLEXITY_STATUS_TABLE
        or not isinstance(payload.get("output"), list)
    ):
        raise _agent_error(
            "Malformed or unknown Agent response; inspect the upstream response before submitting again"
        )
    if expected_id is not None and payload["id"] != expected_id:
        raise _agent_error("Agent response ID does not match the checkpoint")
    _agent_response_id("agent:" + payload["id"])
    if not isinstance(payload.get("model"), str) or not payload["model"]:
        raise _agent_error("Agent response is missing the selected model")
    if payload["status"] == "completed":
        _format_agent_response(payload)
    return payload


def _format_agent_response(payload: dict[str, Any]) -> str:
    text: list[str] = []
    sources: list[dict[str, Any]] = []
    for item in payload["output"]:
        if not isinstance(item, dict):
            raise _agent_error("Malformed Agent output item")
        if item.get("type") == "message" and item.get("role") == "assistant":
            if (
                item.get("status") != "completed"
                or not isinstance(item.get("id"), str)
                or not item["id"]
            ):
                raise _agent_error("Agent assistant message is incomplete or malformed")
            if item.get("phase") == "commentary":
                continue
            # Earlier assistant messages may be commentary; render the final one.
            text = []
            if not isinstance(item.get("content"), list):
                raise _agent_error("Malformed Agent assistant content")
            for content in item["content"]:
                if (
                    not isinstance(content, dict)
                    or content.get("type") != "output_text"
                    or not isinstance(content.get("text"), str)
                ):
                    raise _agent_error("Unsupported Agent assistant content type")
                text.append(content["text"])
                for citation in content.get("annotations") or []:
                    if isinstance(citation, dict) and citation.get("type") == "url_citation":
                        sources.append(citation)
        elif item.get("type") == "search_results":
            if not isinstance(item.get("results"), list):
                raise _agent_error("Malformed Agent search results")
            sources.extend(item["results"])
    answer = "\n\n".join(text).strip()
    if not answer:
        raise _agent_error(
            "Completed Agent response has no assistant answer",
            suggestion="Inspect the stored response ID; no new request was submitted.",
        )
    source_block = _format_async_sources_block(sources)
    if source_block:
        answer += "\n\n" + source_block
    usage = payload.get("usage") or {}
    if isinstance(usage, dict):
        answer += _format_async_cost_block(usage)
    return answer


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
