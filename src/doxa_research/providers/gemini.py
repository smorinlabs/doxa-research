"""Gemini synchronous chat provider (P24).

Mirrors the P23 Perplexity provider pattern: built-in modes with a
[modes.X.gemini] namespace, side-channel stream events for reasoning and
citation, error mapper with _map_gemini_error, tenacity retry on submit.

Uses the official google-genai SDK (>=1.74.0), NOT Gemini's OpenAI-compat
endpoint, because the compat layer omits grounding metadata, thought parts,
and the thinking-budget knob.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx
from google.genai import errors as genai_errors
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

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
from doxa_research.providers.base import Citation, ResearchProvider, StreamEvent

# DR-specific exceptions live in a PRIVATE SDK module that does NOT inherit
# from google.genai.errors.APIError. Its path moved in google-genai 2.0:
# 1.x exposed google.genai._interactions, 2.x exposes
# google.genai._gaos.lib.compat_errors. Resolve newest-first so we fail loudly
# today, and degrade gracefully if it moves again — _is_interactions_error()
# keeps a duck-type fallback for exactly that case.
_INTERACTIONS_ERROR_MODULES = (
    "google.genai._gaos.lib.compat_errors",
    "google.genai._interactions",
)
_InteractionsAPIError: Any = None
for _module_name in _INTERACTIONS_ERROR_MODULES:
    try:
        _InteractionsAPIError = importlib.import_module(_module_name).GeminiNextGenAPIClientError
        break
    except (ImportError, AttributeError):  # pragma: no cover
        continue
_HAS_INTERACTIONS_ERRORS = _InteractionsAPIError is not None

_PROVIDER_NAME_GEMINI = "gemini"


def _is_interactions_error(exc: BaseException) -> bool:
    """True for DR (Interactions API) exceptions, regardless of SDK module path.

    Primary: isinstance check against the imported _InteractionsAPIError. If the
    SDK has renamed the private module since pin-time, fall back to a duck-type
    check (module name + status_code attribute). See spike §7.
    """
    if _HAS_INTERACTIONS_ERRORS and _InteractionsAPIError is not None:
        if isinstance(exc, _InteractionsAPIError):
            return True
    module = type(exc).__module__ or ""
    return module.startswith("google.genai._") and hasattr(exc, "status_code")


_INVALID_KEY_PHRASES_GEMINI: tuple[str, ...] = (
    "api key not valid",
    "api_key_invalid",
    "invalid api key",
    "api key expired",
)

_QUOTA_MARKERS_GEMINI: tuple[str, ...] = (
    "per day",
    "you exceeded your current quota",
    "free tier",
    "billing",
    "credit",
)

_DIRECT_SDK_KEYS_GEMINI: tuple[str, ...] = (
    # Allowlist of [modes.X.gemini].* keys recognized by
    # _build_generate_content_config. NOTE: tools / thinking_budget /
    # include_thoughts are special-cased in the builder (translated to
    # types.Tool / types.ThinkingConfig respectively); the rest pass
    # through to GenerateContentConfig(**kwargs) directly.
    "temperature",
    "top_p",
    "top_k",
    "max_output_tokens",
    "stop_sequences",
    "response_mime_type",
    "response_schema",
    "response_json_schema",
    "tools",
    "safety_settings",
    "thinking_budget",
    "include_thoughts",
)

# Mapping from Gemini interaction status strings (spike §5) to Doxa Research provider-
# status dicts. The 6 SDK-documented values are enumerated here; unknown future
# values fall through to "running" (safe — caller will poll again). The canonical
# Doxa Research polling-status set is {"running", "queued", "completed"} per the
# P18 design spec, matching the convention OpenAIProvider and PerplexityProvider
# use to translate their own in-progress literals.
_DR_STATUS_MAPPING: dict[str, dict[str, Any]] = {
    "in_progress": {"status": "running", "progress": 0.5},
    "completed": {"status": "completed"},
    "cancelled": {"status": "cancelled"},
    "failed": {"status": "permanent_error", "failure_type": "permanent"},
    "requires_action": {
        "status": "permanent_error",
        "failure_type": "requires_action",
        "error": (
            "Gemini interaction is waiting on tool or human approval "
            "(status='requires_action'). This flow is not supported in "
            "P28 v1; the 9 gemini_*_research modes do not request tool "
            "approval. If you see this, please file a bug. See plan v2 "
            "Task 6a for the follow-up spike investigating trigger conditions."
        ),
    },
    "incomplete": {
        "status": "permanent_error",
        "failure_type": "permanent",
        "error": (
            "Gemini Deep Research returned status='incomplete' — the run "
            "finished but output may be truncated. P28 v1 treats this as "
            "a permanent failure conservatively. Re-run the prompt if the "
            "report is needed. Plan v2 Task 6b investigates whether refetch "
            "is possible (may flip this to recoverable in v1.1)."
        ),
    },
}


def _is_gemini_quota_exhaustion(message: str, details: list[dict[str, Any]] | None) -> bool:
    """Distinguish quota/credits exhaustion from ordinary rate-limiting on a 429.

    Inspect the message for known quota-exhaustion phrases AND structurally inspect
    error.details[].reason for FREE_TIER_LIMIT_EXCEEDED / BILLING_DISABLED / daily-metric markers.
    """
    msg_lower = message.lower()
    if any(marker in msg_lower for marker in _QUOTA_MARKERS_GEMINI):
        return True
    if details:
        for entry in details:
            if not isinstance(entry, dict):
                continue
            reason = (entry.get("reason") or "").upper()
            if reason in {"FREE_TIER_LIMIT_EXCEEDED", "BILLING_DISABLED"}:
                return True
            if reason == "RATE_LIMIT_EXCEEDED":
                metric = (entry.get("quotaMetric") or entry.get("metric") or "").lower()
                if "per day" in metric or "daily" in metric:
                    return True
    return False


def _map_gemini_error(exc: Exception, model: str | None, verbose: bool = False) -> DoxaError:
    """Translate google-genai SDK and httpx exceptions into DoxaError subclasses.

    Mirrors _map_openai_error / _map_perplexity_error's shape.
    Note: ModeKindMismatchError is propagated unmapped by the caller (provider's
    submit/stream methods); this function only handles SDK + httpx exceptions.
    """
    # NEW: DR (Interactions API) exceptions are a separate hierarchy.
    if _is_interactions_error(exc):
        status_code = getattr(exc, "status_code", None)
        message = getattr(exc, "message", None) or str(exc) or ""
        msg_lower = message.lower()

        if status_code is None:
            # Connection/timeout errors in the _interactions hierarchy have no status_code.
            return ProviderError(
                _PROVIDER_NAME_GEMINI,
                f"Network error reaching the Gemini Deep Research API: {message}",
            )

        if status_code == 400:
            if any(p in msg_lower for p in _INVALID_KEY_PHRASES_GEMINI):
                return _invalid_key_doxaerror(
                    "Gemini",
                    "https://aistudio.google.com/app/apikey",
                )
            return ProviderError(_PROVIDER_NAME_GEMINI, f"Bad request: {message}")

        if status_code == 401:
            if any(p in msg_lower for p in _INVALID_KEY_PHRASES_GEMINI):
                return _invalid_key_doxaerror(
                    "Gemini",
                    "https://aistudio.google.com/app/apikey",
                )
            return APIKeyError(_PROVIDER_NAME_GEMINI)

        if status_code == 404:
            return ProviderError(
                _PROVIDER_NAME_GEMINI,
                f"Gemini interaction not found or expired: {message}. "
                f"Paid-tier retention is 55 days; free-tier 1 day. Start a new "
                f"operation if the interaction has aged out.",
            )

        if status_code == 429:
            return APIRateLimitError(_PROVIDER_NAME_GEMINI)

        if status_code == 403:
            is_dr_model = model and "deep-research" in str(model).lower()
            if is_dr_model and (
                "tier" in msg_lower or "paid" in msg_lower or "billing" in msg_lower
            ):
                return ProviderError(
                    _PROVIDER_NAME_GEMINI,
                    f"Gemini Deep Research requires a paid tier (Tier 1+). "
                    f"See https://ai.google.dev/pricing. Original error: {message}",
                )
            return ProviderError(_PROVIDER_NAME_GEMINI, f"Permission denied: {message}")

        if status_code is not None and status_code >= 500:
            return ProviderError(
                _PROVIDER_NAME_GEMINI,
                f"Gemini interactions server error ({status_code}): {message}. Retry shortly.",
            )

        return ProviderError(
            _PROVIDER_NAME_GEMINI,
            f"Gemini interactions API error ({status_code}): {message}",
        )

    # Existing branches follow unchanged: ClientError, ServerError, httpx.*, APIError
    if isinstance(exc, genai_errors.ClientError):
        # ClientError stores response_json under .details (full body) and pre-extracts
        # .code / .status / .message. We dig into .details for the structured
        # error.details list (used by quota discrimination).
        body = getattr(exc, "details", None) or {}
        if not isinstance(body, dict):
            body = {}
        err_obj = body.get("error") or {}
        if not isinstance(err_obj, dict):
            err_obj = {}
        message = getattr(exc, "message", None) or err_obj.get("message") or str(exc) or ""
        status_raw = getattr(exc, "status", None) or err_obj.get("status") or ""
        status = status_raw.upper() if isinstance(status_raw, str) else ""
        details = err_obj.get("details")
        code = getattr(exc, "code", None)

        if code == 401 or status == "UNAUTHENTICATED":
            if any(p in message.lower() for p in _INVALID_KEY_PHRASES_GEMINI):
                return _invalid_key_doxaerror(
                    "Gemini",
                    "https://aistudio.google.com/app/apikey",
                )
            return APIKeyError(_PROVIDER_NAME_GEMINI)

        if code == 429 or status == "RESOURCE_EXHAUSTED":
            if _is_gemini_quota_exhaustion(message, details if isinstance(details, list) else None):
                return APIQuotaError(_PROVIDER_NAME_GEMINI)
            return APIRateLimitError(_PROVIDER_NAME_GEMINI)

        if code == 404 or status == "NOT_FOUND":
            model_str = repr(model) if model else "(unknown)"
            return ProviderError(
                _PROVIDER_NAME_GEMINI,
                f"Model {model_str} not found or unavailable. "
                f"Run `doxa providers --models --provider gemini` to list valid models.",
            )

        if code == 400 or status in {
            "INVALID_ARGUMENT",
            "FAILED_PRECONDITION",
            "OUT_OF_RANGE",
        }:
            param = _extract_unsupported_param(message)
            if param:
                return ProviderError(
                    _PROVIDER_NAME_GEMINI,
                    f"Gemini does not support parameter {param!r} for this model. "
                    f"Remove it from the mode config or its provider namespace.",
                )
            return ProviderError(_PROVIDER_NAME_GEMINI, f"Bad request: {message}")

        if code == 403 or status == "PERMISSION_DENIED":
            return ProviderError(
                _PROVIDER_NAME_GEMINI,
                f"Permission denied: {message}",
            )

        # Other ClientError (e.g. unrecognized 4xx)
        return ProviderError(_PROVIDER_NAME_GEMINI, f"Gemini API error ({code}): {message}")

    if isinstance(exc, genai_errors.ServerError):
        code = getattr(exc, "code", "5xx")
        return ProviderError(
            _PROVIDER_NAME_GEMINI,
            f"Gemini server error ({code}). Retry shortly.",
        )

    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            _PROVIDER_NAME_GEMINI,
            "Request timed out. Try again, or raise --timeout.",
        )

    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.RequestError)):
        return ProviderError(
            _PROVIDER_NAME_GEMINI,
            "Network connection error reaching the Gemini API.",
        )

    if isinstance(exc, genai_errors.APIError):
        return ProviderError(_PROVIDER_NAME_GEMINI, f"Gemini API error: {exc}")

    return ProviderError(_PROVIDER_NAME_GEMINI, f"Unexpected error: {exc}")


# Raw SDK exception types that are always retryable (transient transport /
# server-side failures). 429-rate-limit responses arrive as
# genai_errors.ClientError(code=429) — handled by the predicate below since
# we don't want to retry every 4xx, only 429.
_GEMINI_RETRY_CLASSES: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    genai_errors.ServerError,
)

_GEMINI_STREAM_MAX_ATTEMPTS = 3


def _gemini_stream_retry_delay(attempt: int) -> float:
    return float(min(4, 2 ** (attempt - 1)))


def _is_retryable_gemini_exception(exc: BaseException) -> bool:
    """True for transient transport/server errors and raw 429 rate-limit responses.

    The Gemini SDK surfaces 429 as ``genai_errors.ClientError(code=429)``; the
    wrapping into ``APIRateLimitError`` happens AFTER the retry classifier
    runs in ``submit()`` (and after the stream loop's classification), so we
    must inspect the raw SDK exception here, not the post-mapping
    ``DoxaError`` subclass.
    """
    if isinstance(exc, genai_errors.ClientError) and getattr(exc, "code", None) == 429:
        return True
    return isinstance(exc, _GEMINI_RETRY_CLASSES)


class GeminiProvider(ResearchProvider):
    """Synchronous Gemini chat provider (P24).

    Implementation lands across Tasks 4.2-4.6 of the P24 plan:
      - 4.2: built-in modes + request construction
      - 4.3: error mapping + retry policy
      - 4.4: stream() translation (Part.thought + grounding citations)
      - 4.5: submit/check_status/get_result + retry
      - 4.6: kind-mismatch guard
    """

    def __init__(self, api_key: str = "", config: dict[str, Any] | None = None) -> None:
        super().__init__(api_key=api_key, config=config)
        self.model = (self.config or {}).get("model") or "gemini-2.5-flash-lite"
        self.jobs: dict[str, dict[str, Any]] = {}
        # Lazy-import google-genai to avoid hard dep at module-load time;
        # also lets the test suite mock the client without paying the import.
        from google import genai
        from google.genai import types

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        timeout = (self.config or {}).get("timeout")
        if timeout is not None:
            client_kwargs["http_options"] = types.HttpOptions(timeout=int(float(timeout) * 1000))
        self.client = genai.Client(**client_kwargs)

    def is_implemented(self) -> bool:
        return True

    def implementation_status(self) -> str | None:
        return None

    def _validate_kind_for_model(self, mode: str) -> None:
        """Reject background-only models when kind=immediate.

        Raises ModeKindMismatchError BEFORE any HTTP attempt. Mirrors
        openai.py and perplexity.py's pattern. is_background_model() is the
        single source of truth — currently substring-matches "deep-research",
        which covers Gemini's `deep-research-pro-preview-12-2025`.
        """
        declared_kind = (self.config or {}).get("kind")
        if declared_kind == "immediate" and is_background_model(self.model):
            raise ModeKindMismatchError(
                mode_name=mode,
                model=self.model,
                declared_kind="immediate",
                required_kind="background",
            )

    def _build_messages_and_system(
        self, prompt: str, system_prompt: str | None
    ) -> tuple[list[Any], str | None]:
        """Build the contents list + system_instruction for GenerateContentConfig.

        Returns (contents, system_instruction). The caller composes them into
        the final request: `model=`, `contents=contents`,
        `config=GenerateContentConfig(system_instruction=system_instruction, ...)`.
        """
        from google.genai import types

        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        return contents, system_prompt or None

    def _build_tools(self, tool_names: list[str]) -> list[Any]:
        """Translate a list of tool names into a list of types.Tool instances.

        Currently supports: 'google_search' -> Tool(google_search=GoogleSearch()).
        Unknown names are silently skipped (forward-compat for future tool
        families).
        """
        from google.genai import types

        tools: list[Any] = []
        for name in tool_names:
            if name == "google_search":
                tools.append(types.Tool(google_search=types.GoogleSearch()))
            # Future tool names appended here.
        return tools

    def _build_generate_content_config(self) -> Any:
        """Translate [modes.X.gemini].* into a GenerateContentConfig instance.

        Returns None when no gemini-namespace keys are configured (caller will
        omit the config kwarg from the SDK call).

        Special-case keys (NOT direct passthrough):
          - tools: translated via _build_tools.
          - thinking_budget / include_thoughts: nested under
            config.thinking_config.

        All other keys in _DIRECT_SDK_KEYS_GEMINI pass through to
        GenerateContentConfig by name.
        """
        from google.genai import types

        gemini_cfg = (self.config or {}).get("gemini") or {}
        if not isinstance(gemini_cfg, dict) or not gemini_cfg:
            return None

        config_kwargs: dict[str, Any] = {}

        if "tools" in gemini_cfg:
            config_kwargs["tools"] = self._build_tools(gemini_cfg["tools"])

        thinking_budget = gemini_cfg.get("thinking_budget")
        include_thoughts = gemini_cfg.get("include_thoughts", False)
        if thinking_budget is not None or include_thoughts:
            # Policy: when the user opts INTO thinking-related output (either by
            # setting an explicit budget OR by enabling include_thoughts), default
            # the budget to -1 ("dynamic") rather than letting the SDK default
            # apply. Pinned by test_gemini_build_generate_content_config_include_thoughts_only_defaults_thinking_budget_to_dynamic.
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget if thinking_budget is not None else -1,
                include_thoughts=bool(include_thoughts),
            )

        # Pass through every other allowed key by name.
        for key in _DIRECT_SDK_KEYS_GEMINI:
            if key in {"tools", "thinking_budget", "include_thoughts"}:
                continue
            if key in gemini_cfg:
                config_kwargs[key] = gemini_cfg[key]

        return types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

    async def stream(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream Gemini chunks translated to StreamEvent instances.

        Emits:
          - StreamEvent("text", part.text) for each non-thought Part.
          - StreamEvent("reasoning", part.text) for each Part where thought=True.
          - StreamEvent("citation", Citation(title, url)) for each unique
            grounding_chunks[i].web entry, deduped by URL across the stream.
          - StreamEvent("done", "") on clean stream exit.

        Mid-iteration errors map through _map_gemini_error.
        ModeKindMismatchError (raised by _validate_kind_for_model in Task 4.6)
        propagates unmapped.
        """
        self._validate_kind_for_model(mode)  # implemented by Task 4.6; no-op until then

        # Build the request kwargs
        contents, system = self._build_messages_and_system(prompt, system_prompt)
        config = self._build_generate_content_config()
        if system:
            from google.genai import types

            if config is None:
                config = types.GenerateContentConfig(system_instruction=system)
            else:
                config.system_instruction = system

        kwargs: dict[str, Any] = {"model": self.model, "contents": contents}
        if config is not None:
            kwargs["config"] = config

        last_exc: Exception | None = None
        for attempt in range(1, _GEMINI_STREAM_MAX_ATTEMPTS + 1):
            emitted_any = False
            try:
                async for event in self._stream_once(kwargs, seen_citation_urls=set()):
                    emitted_any = True
                    yield event
                return
            except ModeKindMismatchError:
                raise  # propagate unmapped (per error-mapper convention)
            except Exception as e:
                last_exc = e
                if (
                    emitted_any
                    or attempt >= _GEMINI_STREAM_MAX_ATTEMPTS
                    or not _is_retryable_gemini_exception(e)
                ):
                    raise _map_gemini_error(e, self.model, verbose=verbose) from e
                await asyncio.sleep(_gemini_stream_retry_delay(attempt))

        if last_exc is not None:
            raise _map_gemini_error(last_exc, self.model, verbose=verbose) from last_exc

    async def _stream_once(
        self,
        kwargs: dict[str, Any],
        *,
        seen_citation_urls: set[str],
    ) -> AsyncIterator[StreamEvent]:
        stream_iter = await self.client.aio.models.generate_content_stream(**kwargs)
        async for chunk in stream_iter:
            candidates = getattr(chunk, "candidates", None) or []
            if not candidates:
                continue
            candidate = candidates[0]
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if parts:
                for part in parts:
                    text = getattr(part, "text", "") or ""
                    if not text:
                        continue
                    if getattr(part, "thought", False):
                        yield StreamEvent(kind="reasoning", text=text)
                    else:
                        yield StreamEvent(kind="text", text=text)

            grounding = getattr(candidate, "grounding_metadata", None)
            if grounding is not None:
                grounding_chunks = getattr(grounding, "grounding_chunks", None) or []
                for gc in grounding_chunks:
                    web = getattr(gc, "web", None)
                    if web is None:
                        continue
                    url = getattr(web, "uri", None)
                    if not url or url in seen_citation_urls:
                        continue
                    seen_citation_urls.add(url)
                    title = getattr(web, "title", "") or urlparse(url).netloc
                    yield StreamEvent(
                        kind="citation",
                        text=str(title),
                        citation=Citation(title=str(title), url=str(url)),
                    )

        yield StreamEvent(kind="done", text="")

    async def submit(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ) -> str:
        """Route to immediate (chat-completion) or deep-research (interactions) path."""
        self._validate_kind_for_model(mode)
        if is_background_model(self.model):
            return await self._deep_research_submit(prompt, mode, system_prompt, verbose)
        return await self._immediate_submit(prompt, mode, system_prompt, verbose)

    async def check_status(self, job_id: str) -> dict[str, Any]:
        if self._is_dr_job(job_id):
            return await self._deep_research_check_status(job_id)
        return await self._immediate_check_status(job_id)

    async def get_result(self, job_id: str, verbose: bool = False) -> str:
        if self._is_dr_job(job_id):
            return await self._deep_research_get_result(job_id, verbose)
        return await self._immediate_get_result(job_id, verbose)

    async def reconnect(self, job_id: str) -> None:
        """Re-attach to an existing DR interaction after process restart.

        Called by the runtime's resume_operation flow. Verifies the interaction
        still exists upstream and seeds self.jobs[job_id] so subsequent
        check_status / get_result calls work. Immediate jobs cannot be reconnected
        (their response was lost with the process); they would have been
        completed before the process ended.
        """
        try:
            interaction = await self.client.aio.interactions.get(id=job_id)
        except Exception as e:
            raise _map_gemini_error(e, self.model) from e
        self.jobs[job_id] = {
            "kind": "deep_research",
            "interaction_id": job_id,
            "model": self.model,
            "reconnected_at": time.time(),
            "last_interaction": interaction,
            "last_status": str(getattr(interaction, "status", "in_progress")),
        }

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a job. For DR, calls interactions.cancel; for immediate, no-op.

        Immediate (chat-completion) jobs are already complete by the time submit
        returns, so cancel is a no-op there. DR jobs run server-side and must
        be explicitly cancelled via the Interactions API.

        Post-restart resilience: `doxa cancel` instantiates a fresh provider
        with an empty self.jobs dict. If job_id is unknown (post-restart), seed a
        minimal DR entry and attempt the upstream cancel anyway. Let the upstream
        404 response discriminate truly-nonexistent IDs — do NOT short-circuit to
        not_found based on local state alone. Mirrors OpenAI's cancel() pattern
        (src/doxa_research/providers/openai.py: attempt reconnect first, then cancel).

        Defensive 5xx handling (Task 8a spike deferred to v1.1):
        if cancel itself returns a server error, mark cancel_requested
        locally and report cancelled best-effort. The runtime treats SIGINT
        as satisfied; check_status will surface the actual server-side
        state on the next poll.
        """
        if job_id not in self.jobs:
            # Seed a minimal DR entry so the upstream cancel can proceed.
            # This covers the post-process-restart case where self.jobs is empty.
            self.jobs[job_id] = {
                "kind": "deep_research",
                "interaction_id": job_id,
                "model": self.model,
                "cancel_requested": True,
            }
        job = self.jobs[job_id]
        if job.get("kind") != "deep_research":
            return {"status": "cancelled"}  # immediate path — already complete

        job["cancel_requested"] = True
        try:
            await self.client.aio.interactions.cancel(id=job_id)
            return {"status": "cancelled"}
        except Exception as e:
            if _is_interactions_error(e):
                sc = getattr(e, "status_code", None) or 0
                if sc == 404:
                    return {
                        "status": "not_found",
                        "error": f"Interaction not found or expired: {job_id}",
                    }
                if sc >= 500:
                    # 5xx / network — treat as best-effort. Runtime SIGINT path completes;
                    # check_status will surface actual server-side state on next poll.
                    return {
                        "status": "cancelled",
                        "best_effort": True,
                        "error": (
                            f"cancel returned server error "
                            f"({sc}); interaction may "
                            f"still be running. Next check_status will reflect."
                        ),
                    }
            raise _map_gemini_error(e, self.model) from e

    def _is_dr_job(self, job_id: str) -> bool:
        """Job-id discriminator. DR jobs are stored with kind='deep_research' in self.jobs."""
        job = self.jobs.get(job_id)
        return bool(job and job.get("kind") == "deep_research")

    async def _immediate_submit(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ) -> str:
        """One-shot non-stream generate_content. Stashes response under a job_id."""
        self._validate_kind_for_model(mode)

        try:
            response = await self._submit_with_retry(prompt, mode, system_prompt, verbose)
        except ModeKindMismatchError:
            raise
        except Exception as e:
            raise _map_gemini_error(e, self.model, verbose=verbose) from e

        job_id = (
            getattr(response, "id", None)
            or f"gemini-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        )
        self.jobs[job_id] = {
            "kind": "immediate",
            "response": response,
            "created_at": time.time(),
        }
        return job_id

    async def _deep_research_submit(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ) -> str:
        """Submit a Deep Research interaction; return the interaction id.

        Unlike the immediate path, the response body is NOT yet available — the
        actual research runs asynchronously and is polled via check_status. The
        self.jobs entry stores only metadata (kind, interaction_id, submitted_at,
        mode, model). The response is fetched lazily by _deep_research_get_result.

        Note: system_prompt is ignored on the DR path; the Interactions API for
        Deep Research does not document a system-instruction parameter, and the
        mode's system_prompt is implicitly part of the agent's behavior.
        """
        self._validate_kind_for_model(mode)
        try:
            response = await self._deep_research_submit_with_retry(prompt)
        except ModeKindMismatchError:
            raise
        except Exception as e:
            raise _map_gemini_error(e, self.model, verbose=verbose) from e

        interaction_id = getattr(response, "id", None)
        if not interaction_id:
            raise ProviderError(
                _PROVIDER_NAME_GEMINI,
                "interactions.create returned a response without an id",
            )
        self.jobs[interaction_id] = {
            "kind": "deep_research",
            "interaction_id": interaction_id,
            "mode": mode,
            "model": self.model,
            "submitted_at": time.time(),
        }
        return interaction_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception(_is_retryable_gemini_exception),
        reraise=True,
    )
    async def _deep_research_submit_with_retry(self, prompt: str) -> Any:
        return await self.client.aio.interactions.create(
            agent=self.model,
            input=prompt,
            background=True,
            store=True,
        )

    async def _deep_research_check_status(self, job_id: str) -> dict[str, Any]:
        """Poll interactions.get; map Gemini status to Doxa Research status + failure_type.

        SDK declares 6 statuses (spike §5). v1 mapping is conservative for
        requires_action and incomplete; Tasks 6a/6b spike + 6c revise.
        """
        if job_id not in self.jobs:
            return {"status": "not_found", "error": f"Unknown job_id: {job_id}"}
        try:
            interaction = await self.client.aio.interactions.get(id=job_id)
        except Exception as e:
            mapped = _map_gemini_error(e, self.model)
            if _is_retryable_gemini_exception(e):
                return {"status": "transient_error", "error": str(mapped)}
            return {
                "status": "permanent_error",
                "failure_type": "permanent",
                "error": str(mapped),
            }

        live = str(getattr(interaction, "status", "in_progress"))
        self.jobs[job_id]["last_status"] = live
        self.jobs[job_id]["last_interaction"] = interaction

        # Unknown future SDK status values default to "running" (safe — runtime polls again).
        mapped = _DR_STATUS_MAPPING.get(live, {"status": "running", "progress": 0.5})
        return {**mapped, "raw_status": live}

    async def _deep_research_get_result(self, job_id: str, verbose: bool = False) -> str:
        """Render the completed DR interaction as markdown text + ## Sources.

        Citation extraction per spike §4 (layered strategy, user-locked 2026-05-12):
          1. Walk interaction.steps[] looking for step.type='model_output'.
          2. For each, walk content[].annotations[] to collect URLCitation entries.
          3. Parse the LAST step's text for the SDK-rendered Sources block (gives
             domain titles for the Vertex AI redirect URLs).
          4. Bounded-concurrency HEAD-follow each redirect to extract source URL.
          5. Render via render_sources_block; link target = source URL when resolved,
             redirect URL otherwise.
        """
        if job_id not in self.jobs:
            raise ProviderError(_PROVIDER_NAME_GEMINI, f"Unknown job_id: {job_id}")
        job = self.jobs[job_id]
        interaction = job.get("last_interaction")
        if interaction is None:
            try:
                interaction = await self.client.aio.interactions.get(id=job_id)
                job["last_interaction"] = interaction
            except Exception as e:
                raise _map_gemini_error(e, self.model, verbose=verbose) from e

        steps = getattr(interaction, "steps", None) or []

        # Pass 1: collect text from all model_output steps + annotations from each.
        text_parts: list[str] = []
        raw_annotations: list[Any] = []
        last_step_text: str | None = None
        for step in steps:
            if str(getattr(step, "type", "")) != "model_output":
                continue
            content = getattr(step, "content", None) or []
            for item in content:
                text = getattr(item, "text", None) or ""
                if text:
                    text_parts.append(text)
                    last_step_text = text  # tracks most-recent rendered text
                anns = getattr(item, "annotations", None) or []
                raw_annotations.extend(anns)

        # Pass 2: derive title-lookup map from the SDK-rendered Sources block.
        parsed_sources = _parse_sdk_sources_block(last_step_text)

        # Pass 3: resolve redirect URLs in bounded-concurrency parallel.
        # Dedupe before the HEAD-follow loop — the same grounding redirect URL
        # can appear many times across annotations. dict.fromkeys preserves
        # order while removing duplicates; the resolved dict is keyed by URL
        # anyway so downstream citation assembly already handles per-URL lookup.
        redirect_urls: list[str] = list(
            dict.fromkeys(u for a in raw_annotations if (u := getattr(a, "url", None)) is not None)
        )
        resolved = await _resolve_dr_redirects(redirect_urls) if redirect_urls else {}

        # Pass 4: assemble Citation list with title derivation + final URL + dedupe.
        citations: list[Citation] = []
        seen_final_urls: set[str] = set()
        for ann in raw_annotations:
            redirect_url = getattr(ann, "url", None)
            if not redirect_url:
                continue
            resolved_url = resolved.get(redirect_url)
            final_url = resolved_url or redirect_url
            if final_url in seen_final_urls:
                continue
            seen_final_urls.add(final_url)
            title = parsed_sources.get(redirect_url)
            if not title and resolved_url:
                title = urlparse(resolved_url).netloc or None
            if not title:
                title = urlparse(redirect_url).netloc or redirect_url
            citations.append(Citation(title=str(title), url=str(final_url)))

        answer = "".join(text_parts).strip()
        if not answer and verbose:
            debug_print_empty_response(interaction, provider_label="Gemini DR")

        sections: list[str] = []
        if answer:
            sections.append(answer)
        if citations:
            sections.append(render_sources_block(citations))
        return "\n\n".join(sections)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception(_is_retryable_gemini_exception),
        reraise=True,
    )
    async def _submit_with_retry(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None,
        verbose: bool,
    ) -> Any:
        """Build request and invoke generate_content with retry on transient errors."""
        contents, system = self._build_messages_and_system(prompt, system_prompt)
        config = self._build_generate_content_config()
        if system:
            from google.genai import types

            if config is None:
                config = types.GenerateContentConfig(system_instruction=system)
            else:
                config.system_instruction = system

        kwargs: dict[str, Any] = {"model": self.model, "contents": contents}
        if config is not None:
            kwargs["config"] = config
        return await self.client.aio.models.generate_content(**kwargs)

    async def _immediate_check_status(self, job_id: str) -> dict[str, Any]:
        """Return completion status for a previously-submitted job."""
        if job_id not in self.jobs:
            return {"status": "not_found", "error": f"Unknown job_id: {job_id}"}
        return {"status": "completed", "progress": 1.0}

    async def _immediate_get_result(self, job_id: str, verbose: bool = False) -> str:
        """Render the stashed response as text + ## Reasoning + ## Sources."""
        if job_id not in self.jobs:
            raise ProviderError(_PROVIDER_NAME_GEMINI, f"Unknown job_id: {job_id}")

        response = self.jobs[job_id]["response"]
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            if verbose:
                debug_print_empty_response(response, provider_label="Gemini")
            return ""

        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        if content is None:
            if verbose:
                debug_print_empty_response(response, provider_label="Gemini")
            return ""

        text_parts: list[str] = []
        thought_parts: list[str] = []
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", "") or ""
            if not text:
                continue
            if getattr(part, "thought", False):
                thought_parts.append(text)
            else:
                text_parts.append(text)

        answer = "".join(text_parts).strip()
        if not answer and verbose:
            debug_print_empty_response(response, provider_label="Gemini")

        sources = self._render_sources(getattr(candidate, "grounding_metadata", None))
        reasoning = "\n".join(thought_parts).strip()

        sections: list[str] = []
        if reasoning:
            sections.append(f"## Reasoning\n\n{reasoning}")
        if answer:
            sections.append(answer)
        if sources:
            sections.append(sources)
        return "\n\n".join(sections)

    def _render_sources(self, grounding_metadata: Any) -> str:
        """Render grounding_metadata.grounding_chunks as a ## Sources block."""
        if grounding_metadata is None:
            return ""
        chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
        citations: list[Citation] = []
        for gc in chunks:
            web = getattr(gc, "web", None)
            if web is None:
                continue
            url = getattr(web, "uri", None)
            if not url:
                continue
            title = getattr(web, "title", "") or urlparse(url).netloc
            citations.append(Citation(title=str(title), url=str(url)))
        return render_sources_block(citations)


# --- DR citation rendering helpers (P28 Task 7) ---

_DR_SOURCES_BLOCK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_DR_REDIRECT_PREFIXES: tuple[str, ...] = (
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/",
)


def _parse_sdk_sources_block(text: str | None) -> dict[str, str]:
    """Parse the SDK's rendered Sources block into {url: domain_title}.

    The DR SDK's last step typically contains markdown like:
        [usenix.org](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ...)
    Returns {redirect_url: domain_title}; empty dict on failure / no matches.
    """
    if not text:
        return {}
    return {url: title for title, url in _DR_SOURCES_BLOCK_RE.findall(text)}


def _is_dr_redirect(url: str) -> bool:
    return any(url.startswith(p) for p in _DR_REDIRECT_PREFIXES)


async def _follow_dr_redirect(
    url: str, *, timeout_s: float = 2.0, client: httpx.AsyncClient | None = None
) -> str | None:
    """Follow a Vertex AI grounding redirect to extract the source URL.

    Returns the resolved URL on success, None on any failure (timeout, error,
    no Location header). Caller falls back to the redirect URL when None.
    """
    if not _is_dr_redirect(url):
        return url
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
    try:
        resp = await client.head(url, follow_redirects=False)
        location = resp.headers.get("Location") or resp.headers.get("location")
        return location if location else None
    except (httpx.RequestError, httpx.HTTPStatusError):
        return None
    finally:
        if owns_client:
            await client.aclose()


async def _resolve_dr_redirects(
    urls: list[str], *, concurrency: int = 10, timeout_s: float = 2.0
) -> dict[str, str | None]:
    """Bounded-concurrency redirect resolution.

    Returns {original_url: resolved_url_or_None}.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(client: httpx.AsyncClient, u: str) -> tuple[str, str | None]:
        async with sem:
            return u, await _follow_dr_redirect(u, timeout_s=timeout_s, client=client)

    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
        results = await asyncio.gather(*(_one(client, u) for u in urls))
    return dict(results)
