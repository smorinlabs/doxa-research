"""OpenAI Responses API provider for Deep Research."""

from __future__ import annotations

import logging
import warnings
from datetime import date, datetime
from typing import Any
from uuid import uuid4

import httpx
import openai
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from doxa_research.config import (
    is_background_model,
    requires_background_submission,
    supports_temperature,
)
from doxa_research.errors import (
    APIKeyError,
    APIQuotaError,
    APIRateLimitError,
    DoxaError,
    ModeKindMismatchError,
    ProviderError,
)
from doxa_research.models import ModelCache
from doxa_research.providers._helpers import (
    _extract_unsupported_param,
    _invalid_key_doxaerror,
    debug_print_empty_response,
    render_sources_block,
)
from doxa_research.providers._status import _translate_provider_status
from doxa_research.providers.base import Citation, ResearchProvider

_LOG = logging.getLogger(__name__)

# Provider-status → Doxa Research-status template. Used by check_status; the in_progress
# template's progress is overridden at runtime from response.metadata; failed
# and incomplete templates need an `error` field filled in by the caller.
_OPENAI_STATUS_TABLE: dict[str, dict[str, Any]] = {
    "completed": {"status": "completed", "progress": 1.0},
    "in_progress": {"status": "running", "progress": 0.5},
    "failed": {"status": "permanent_error"},
    "incomplete": {"status": "permanent_error"},
    "cancelled": {"status": "cancelled", "error": "Response was cancelled"},
    "queued": {"status": "queued", "progress": 0.0},
}


_PROVIDER_NAME_OPENAI = "openai"
_DIRECT_SDK_KEYS_OPENAI: tuple[str, ...] = (
    "model",
    "input",
    "reasoning",
    "tools",
    "background",
    "temperature",
    "max_tool_calls",
)

# Framework-level config keys that stay flat on `provider.config` and are NOT
# eligible for the [modes.X.openai] namespace migration. Reading any of these
# from the flat top level must NOT emit a DeprecationWarning.
_FRAMEWORK_FLAT_KEYS_OPENAI: frozenset[str] = frozenset(
    {"openai", "kind", "model", "timeout", "background"}
)


def _annotation_get(annotation: Any, key: str) -> Any:
    """Read an annotation field from either dict or SDK object shape."""
    if isinstance(annotation, dict):
        return annotation.get(key)
    return getattr(annotation, key, None)


def _annotation_debug_value(annotation: Any) -> Any:
    """Return a compact value suitable for warning logs."""
    if isinstance(annotation, dict):
        return annotation
    attrs = getattr(annotation, "__dict__", None)
    if isinstance(attrs, dict):
        return {k: v for k, v in attrs.items() if not k.startswith("_")}
    return repr(annotation)


def _url_citation_from_annotation(annotation: Any) -> Citation | None:
    """Normalize a Responses API annotation into a URL citation or skip it.

    Missing type is treated as a legacy URL-citation shape for backward
    compatibility. Known non-URL types with URL-shaped fields are skipped and
    warned so provider drift is visible without rendering misleading sources.
    """
    url = _annotation_get(annotation, "url")
    if not url:
        return None

    annotation_type = _annotation_get(annotation, "type")
    debug_value = _annotation_debug_value(annotation)
    if annotation_type is None:
        _LOG.warning(
            "OpenAI annotation with URL is missing type; treating as url_citation: %r",
            debug_value,
        )
    elif str(annotation_type) != "url_citation":
        _LOG.warning(
            "OpenAI annotation with URL has unsupported type %r; skipping: %r",
            annotation_type,
            debug_value,
        )
        return None

    title = _annotation_get(annotation, "title") or url
    return Citation(title=str(title), url=str(url))


def _rate_limit_error_is_quota(exc: BaseException) -> bool:
    """Return True when a 429-style SDK error is actually quota/billing exhaustion."""
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


def _map_openai_error(
    exc: BaseException, model: str | None = None, verbose: bool = False
) -> DoxaError:
    """Map an openai SDK exception to a Doxa Research error type.

    Ordering note: APITimeoutError is a subclass of APIConnectionError, so it is
    checked first. RateLimitError inspects body/code to route insufficient_quota
    specifically to APIQuotaError.
    """
    raw = str(exc) if verbose else None

    if isinstance(exc, openai.AuthenticationError):
        msg = str(exc).lower()
        if "incorrect api key" in msg:
            return _invalid_key_doxaerror("OpenAI", "https://platform.openai.com/account/api-keys")
        return APIKeyError(_PROVIDER_NAME_OPENAI)

    if isinstance(exc, openai.RateLimitError):
        if _rate_limit_error_is_quota(exc):
            return APIQuotaError(_PROVIDER_NAME_OPENAI)
        return APIRateLimitError(_PROVIDER_NAME_OPENAI)

    if isinstance(exc, openai.NotFoundError):
        return ProviderError(
            _PROVIDER_NAME_OPENAI,
            f"Model '{model}' not found. Please check available models with "
            f"'doxa providers models --provider openai'",
            raw_error=raw,
        )

    if isinstance(exc, openai.BadRequestError):
        msg = str(exc)
        msg_lower = msg.lower()
        if "unsupported parameter" in msg_lower and "temperature" in msg_lower:
            return ProviderError(
                _PROVIDER_NAME_OPENAI,
                f"Model '{model}' does not support temperature parameter. "
                "This is likely a response model (o3, gpt-5.6-sol, etc.)",
                raw_error=raw,
            )
        if "unsupported parameter" in msg_lower:
            param_name = _extract_unsupported_param(msg) or "unknown"
            return ProviderError(
                _PROVIDER_NAME_OPENAI,
                f"Model '{model}' does not support parameter '{param_name}'",
                raw_error=raw,
            )
        return ProviderError(_PROVIDER_NAME_OPENAI, f"Invalid request: {msg}", raw_error=raw)

    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError(
            _PROVIDER_NAME_OPENAI, "Permission denied by OpenAI API.", raw_error=raw
        )

    if isinstance(exc, openai.InternalServerError):
        return ProviderError(
            _PROVIDER_NAME_OPENAI,
            "OpenAI server error. Try again in a moment.",
            raw_error=raw,
        )

    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(
            _PROVIDER_NAME_OPENAI,
            "Request timed out. Try increasing timeout in config.",
            raw_error=raw,
        )

    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(
            _PROVIDER_NAME_OPENAI,
            "Failed to connect to OpenAI API. Check your internet connection.",
            raw_error=raw,
        )

    if isinstance(exc, openai.APIError):
        return ProviderError(_PROVIDER_NAME_OPENAI, str(exc), raw_error=raw)

    # A6 (P27 factor-dedup): intentional defense-in-depth. APIError is the
    # SDK base class so this fallthrough is unreachable in practice; kept to
    # guard against non-SDK exceptions sneaking through future refactors.
    return ProviderError(_PROVIDER_NAME_OPENAI, str(exc), raw_error=raw)


def _is_retired(shutdown_date: str | None) -> bool:
    """True if `shutdown_date` (YYYY-MM-DD) is today or in the past."""
    if not shutdown_date:
        return False
    try:
        return date.fromisoformat(shutdown_date) <= date.today()
    except (TypeError, ValueError):
        return False


class OpenAIProvider(ResearchProvider):
    """OpenAI Responses API implementation for Deep Research"""

    def __init__(self, api_key: str, config: dict[str, Any] | None = None):
        self.api_key = api_key
        self.config = config or {}
        # Model will be passed from mode configuration, default to o3
        self.model = self.config.get("model", "o3")
        self.jobs: dict[str, dict[str, Any]] = {}  # Store job information for async tracking
        self.model_cache = ModelCache(_PROVIDER_NAME_OPENAI)  # Initialize cache for OpenAI

        # Add timeout configuration
        timeout = self.config.get("timeout", 30.0)
        self.client = AsyncOpenAI(api_key=api_key, timeout=httpx.Timeout(timeout, connect=5.0))

    def _resolve_provider_config_value(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        """Read a provider-specific config key.

        Resolution chain:
          1. ``self.config["openai"][key]``  -- the ``[modes.X.openai]``
             namespace (preferred).
          2. ``self.config[key]``            -- flat top-level (deprecated;
             emits ``DeprecationWarning``).
          3. ``default``.

        Mirrors Perplexity's ``[modes.X.perplexity]`` namespace pattern. Flat
        keys are supported for backwards-compat with mode TOMLs that pre-date
        P24, but emit a ``DeprecationWarning`` telling users to migrate.
        Framework-level keys (``kind``, ``model``, ``timeout``, ``background``,
        ``openai``) are never warned on — they are framework-owned, not
        user-owned, and are read directly from ``self.config`` elsewhere.
        """
        nested = self.config.get(_PROVIDER_NAME_OPENAI) or {}
        if not isinstance(nested, dict):
            nested = {}
        if key in nested:
            return nested[key]
        if key in self.config and key not in _FRAMEWORK_FLAT_KEYS_OPENAI:
            warnings.warn(
                f"OpenAI provider read flat config key {key!r}; migrate to "
                f"[modes.X.openai].{key} namespace. Flat-key support will be "
                f"removed in a future release.",
                DeprecationWarning,
                stacklevel=3,
            )
            return self.config[key]
        return default

    def _validate_kind_for_model(self, mode: str) -> None:
        """Refuse to submit when declared `kind` contradicts the model's required kind.

        P18 contract: a mode declared `kind = "immediate"` cannot use a
        deep-research model — those models require OpenAI's background flow.
        Raised BEFORE any HTTP call so users see a config-edit suggestion
        instead of a confusing API error mid-run. The reverse case
        (`background` declared, regular model) is legal — OpenAI lets you
        force-background any model — and is not checked.

        See `docs/superpowers/specs/2026-04-26-p18-immediate-vs-background-design.md`
        §5.6 + §4 Q1.
        """
        declared = self.config.get("kind")
        if declared == "immediate" and requires_background_submission(self.model):
            raise ModeKindMismatchError(
                mode_name=mode,
                model=self.model,
                declared_kind="immediate",
                required_kind="background",
            )

    async def submit(
        self, prompt: str, mode: str, system_prompt: str | None = None, verbose: bool = False
    ) -> str:
        """Submit research using OpenAI Responses API.

        Raw openai.* exceptions from the retryable inner call are mapped here to
        DoxaError subclasses so callers always see a single error taxonomy.
        """
        self._validate_kind_for_model(mode)
        try:
            return await self._submit_with_retry(prompt, mode, system_prompt, verbose)
        except ModeKindMismatchError:
            raise
        except (openai.APIError, Exception) as e:
            raise _map_openai_error(e, model=self.model, verbose=verbose) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((openai.APITimeoutError, openai.APIConnectionError)),
        reraise=True,
    )
    async def _submit_with_retry(
        self, prompt: str, mode: str, system_prompt: str | None, verbose: bool
    ) -> str:
        """Inner retryable submit. Raises raw openai.* exceptions."""
        # Build structured input format for Responses API
        input_messages: list[dict[str, Any]] = []

        if system_prompt:
            input_messages.append(
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": system_prompt}],
                }
            )

        input_messages.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})

        # Configure tools based on model type.
        # `web_search`, not the legacy `web_search_preview`: OpenAI's web-search
        # migration table directs Responses integrations to the non-preview tool,
        # which alone supports `filters`, `external_web_access` and
        # `return_token_budget`. Preview is still accepted but ignores them.
        tools: list[dict[str, Any]] = []
        if is_background_model(self.model):
            if self._resolve_provider_config_value("web_search", True):
                tools.append({"type": "web_search"})
            if self._resolve_provider_config_value("code_interpreter", True):
                tools.append({"type": "code_interpreter", "container": {"type": "auto"}})

        # Determine if background mode should be used
        # Background for deep-research models (via is_background_model) or explicit config
        use_background = is_background_model(self.model) or self.config.get("background", False)

        # Get configuration parameters
        temperature = self._resolve_provider_config_value("temperature", 0.7)

        # Build request parameters
        # Reasoning effort is a gpt-5.6 capability the retired deep-research
        # models did not expose. Their research depth came from the specialised
        # model itself; on a general-purpose replacement it must be asked for.
        # OpenAI documents the default as "medium"; research work wants more.
        reasoning: dict[str, Any] = {"summary": "auto"}
        effort = self._resolve_provider_config_value(
            "reasoning_effort", "high" if is_background_model(self.model) else None
        )
        if effort is not None:
            reasoning["effort"] = effort

        request_params: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "reasoning": reasoning,
            "tools": tools,
            "background": use_background,
        }

        # The retired deep-research models always browsed. gpt-5.6-sol treats
        # web search as an ordinary tool and under `tool_choice: "auto"` may
        # answer from parametric memory instead — a silent loss of grounding
        # for a research tool.
        #
        # Target web search specifically rather than "required": with Code
        # Interpreter also enabled by default, plain "required" is satisfied
        # by any tool, so a calculation alone would meet it and the answer
        # could still be ungrounded.
        tool_types = {t["type"] for t in tools}
        if "web_search" in tool_types and is_background_model(self.model):
            request_params["tool_choice"] = self._resolve_provider_config_value(
                "tool_choice", {"type": "web_search"}
            )

        # Only add temperature for models that support it. Reasoning models
        # (o-series) and the gpt-5 family both reject it: gpt-5.6-sol returns
        # "Unsupported parameter: 'temperature' is not supported with this
        # model." The old `startswith("o")` test passed gpt-5.6-sol through.
        if supports_temperature(self.model, reasoning.get("effort")):
            request_params["temperature"] = temperature

        # Apply max_tool_calls if configured — primary lever for cost and latency control
        max_tool_calls = self._resolve_provider_config_value("max_tool_calls")
        if max_tool_calls is not None:
            request_params["max_tool_calls"] = max_tool_calls

        # Use Responses API
        response = await self.client.responses.create(**request_params)

        # Store job information
        job_id = (
            response.id
            if hasattr(response, "id")
            else f"openai-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        self.jobs[job_id] = {
            "response": response,
            "background": use_background,
            "created_at": datetime.now(),
        }

        return job_id

    async def check_status(self, job_id: str) -> dict[str, Any]:
        """Check actual status of research task"""
        if job_id not in self.jobs:
            return {"status": "not_found", "error": "Job not found"}

        job_info = self.jobs[job_id]

        # If not background mode, it's already completed
        # TODO(v4.0.0 / future P19): remove this shortcut. Post-P18,
        # immediate-kind runs use `_execute_immediate` which calls
        # `provider.stream()` directly and never reaches `check_status`.
        # The shortcut survives only as defense-in-depth for edge cases like
        # `--async` on a non-deep-research model. Confirm dead via a full
        # audit before deletion.
        if not job_info.get("background", False):
            return {"status": "completed", "progress": 1.0}

        try:
            # Poll the Responses API for status of background job
            response = await self.client.responses.retrieve(job_id)

            if not hasattr(response, "status"):
                return {
                    "status": "permanent_error",
                    "error": "Response object has no status attribute",
                }

            status_str = str(response.status) if response.status is not None else ""
            translated = _translate_provider_status(status_str, _OPENAI_STATUS_TABLE)

            # Per-status post-mutation: dynamic fields the table can't carry.
            if status_str == "completed":
                # Cache the completed response so stale-cache fallback below
                # and get_result() can rely on it.
                self.jobs[job_id]["response"] = response
            elif status_str == "in_progress":
                # Pull live progress from response.metadata if available; the
                # table default is 0.5.
                if hasattr(response, "metadata") and response.metadata:
                    translated["progress"] = response.metadata.get("progress", 0.5)
            elif status_str == "failed":
                error_msg = getattr(response, "error", "Unknown error")
                translated["error"] = str(error_msg)
            elif status_str == "incomplete":
                error_msg = (
                    getattr(response, "error", None) or "Response was incomplete (output truncated)"
                )
                translated["error"] = str(error_msg)
            # "cancelled" and "queued" need no post-mutation; the table covers them.
            # Unknown statuses are handled by _translate_provider_status's default
            # unknown branch (permanent_error with the unrecognized literal).
            return translated
        except (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as e:
            cached = job_info.get("response")
            if cached and getattr(cached, "status", None) == "completed":
                return {"status": "completed", "progress": 1.0}
            return {
                "status": "transient_error",
                "error": str(e),
                "error_class": type(e).__name__,
            }
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.BadRequestError,
            openai.NotFoundError,
        ) as e:
            return {
                "status": "permanent_error",
                "error": str(e),
                "error_class": type(e).__name__,
            }
        except Exception as e:
            # Unknown — treat as transient so we don't kill long jobs over novel errors.
            cached = job_info.get("response")
            if cached and getattr(cached, "status", None) == "completed":
                return {"status": "completed", "progress": 1.0}
            return {
                "status": "transient_error",
                "error": f"Unexpected error ({type(e).__name__}): {e}",
                "error_class": type(e).__name__,
            }

    async def reconnect(self, job_id: str) -> None:
        """Re-attach to an existing background job after a fresh process start."""
        response = await self.client.responses.retrieve(job_id)
        self.jobs[job_id] = {
            "response": response,
            "background": True,
            "created_at": datetime.now(),
        }

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Best-effort upstream cancel via OpenAI Responses API.

        P18 Phase G. Documented in `planning/p18-cancel-research.md`:
          * `client.responses.cancel(job_id)` works for queued/in_progress
            background responses.
          * Already-completed jobs return status='completed' (no error).
          * The full Response object is returned; we map .status to our
            uniform status dict.

        If `job_id` isn't in our local `self.jobs` dict (e.g., `doxa cancel`
        invoked after a fresh process start), we attempt `reconnect()` first.
        """
        if job_id not in self.jobs:
            try:
                await self.reconnect(job_id)
            except Exception as e:
                return {
                    "status": "permanent_error",
                    "error": f"reconnect failed: {e}",
                    "error_class": type(e).__name__,
                }
        try:
            response = await self.client.responses.cancel(job_id)
            status = getattr(response, "status", None)
            if status == "completed":
                # Job finished before cancel landed.
                return {"status": "completed", "progress": 1.0}
            # Treat any non-completed terminal state as cancelled (cancelled,
            # failed, incomplete, or any future state).
            return {"status": "cancelled", "error": "Response was cancelled"}
        except Exception as e:
            return {
                "status": "permanent_error",
                "error": str(e),
                "error_class": type(e).__name__,
            }

    async def stream(
        self,
        prompt: str,
        mode: str,
        system_prompt: str | None = None,
        verbose: bool = False,
    ):
        """Yield text/reasoning/citation/done events from the OpenAI Responses streaming API.

        P18 Phase E: only legal for non-background (immediate-kind) models.
        Background models require server-side async submission and don't
        stream tokens. The `_validate_kind_for_model` runtime check upstream
        catches the mismatch; this method is defense-in-depth.

        Event mapping (P24 Task 6.1 — see planning/p24-openai-stream-audit.v1.md):
          * `response.output_text.delta`              -> kind="text"
          * `response.reasoning_summary_text.delta`   -> kind="reasoning"
          * `response.output_text.annotation.added`   -> kind="citation"
            (only when the annotation carries a URL; file_citation /
            container_file_citation / file_path variants are skipped to
            mirror `get_result()`'s URL-only filter at lines ~602-610)
          * (terminal stream exit)                    -> kind="done"

        Request shape:
          [modes.X.openai].reasoning_summary enables reasoning summaries.
          [modes.X.openai].web_search=true enables the web_search tool.
          Web search is opt-in for user modes and enabled by the builtin
          openai_reasoning mode.
        """
        from doxa_research.providers.base import StreamEvent

        self._validate_kind_for_model(mode)
        if requires_background_submission(self.model):
            raise NotImplementedError(
                f"OpenAIProvider.stream(): model {self.model!r} requires background "
                f"submission; streaming is not supported. Use submit() + check_status() instead."
            )

        input_messages: list[dict[str, Any]] = []
        if system_prompt:
            input_messages.append(
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": system_prompt}],
                }
            )
        input_messages.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})

        request_params: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
        }
        reasoning_summary = self._resolve_provider_config_value("reasoning_summary")
        # Explicit settings must survive both execution paths. Immediate and
        # background may default differently, but a value the user actually
        # configured is dropped by neither.
        stream_effort = self._resolve_provider_config_value("reasoning_effort")
        if reasoning_summary is not None or stream_effort is not None:
            stream_reasoning: dict[str, Any] = {}
            if reasoning_summary is not None:
                stream_reasoning["summary"] = reasoning_summary
            if stream_effort is not None:
                stream_reasoning["effort"] = stream_effort
            request_params["reasoning"] = stream_reasoning
        if self._resolve_provider_config_value("web_search", False):
            request_params["tools"] = [{"type": "web_search"}]
            stream_tool_choice = self._resolve_provider_config_value("tool_choice")
            if stream_tool_choice is not None:
                request_params["tool_choice"] = stream_tool_choice
        # Same effort-aware capability test as submit().
        if supports_temperature(self.model, stream_effort):
            request_params["temperature"] = self._resolve_provider_config_value("temperature", 0.7)

        try:
            async with self.client.responses.stream(**request_params) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            yield StreamEvent(kind="text", text=delta)
                    elif event_type == "response.reasoning_summary_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            yield StreamEvent(kind="reasoning", text=delta)
                    elif event_type == "response.output_text.annotation.added":
                        ann = getattr(event, "annotation", None)
                        if ann is None:
                            continue
                        citation = _url_citation_from_annotation(ann)
                        if citation is None:
                            continue
                        yield StreamEvent(
                            kind="citation",
                            text=citation.title,
                            citation=citation,
                        )
                    # Other event types (response.created, response.completed,
                    # response.output_item.*, etc.) are intentionally skipped.
            yield StreamEvent(kind="done", text="")
        except (openai.APIError, Exception) as e:
            raise _map_openai_error(e, model=self.model, verbose=verbose) from e

    async def get_result(self, job_id: str, verbose: bool = False) -> str:
        """Get the Deep Research result"""
        if job_id not in self.jobs:
            raise ValueError("Job not found")

        job_info = self.jobs[job_id]

        # If background mode, retrieve the latest response
        if job_info.get("background", False):
            try:
                response = await self.client.responses.retrieve(job_id)
                job_info["response"] = response  # Update cached response
            except Exception as e:
                # Use cached response if retrieval fails
                response = job_info.get("response")
                if not response:
                    return f"Error retrieving result: {str(e)}"
        else:
            response = job_info.get("response")

        if not response:
            return "No response available"

        # Extract content based on response structure
        content = ""
        citations: list[Citation] = []

        # Handle different response formats
        if hasattr(response, "output"):
            # Responses API format
            if isinstance(response.output, list):
                # Select the final assistant message instead of concatenating commentary.
                message_items = [
                    item for item in response.output if getattr(item, "type", None) == "message"
                ]
                target_message = (
                    next(
                        (
                            item
                            for item in reversed(message_items)
                            if getattr(item, "phase", None) == "final_answer"
                            and getattr(item, "status", None) == "completed"
                        ),
                        None,
                    )
                    or next(
                        (
                            item
                            for item in reversed(message_items)
                            if getattr(item, "status", None) == "completed"
                        ),
                        None,
                    )
                    or (message_items[-1] if message_items else None)
                )

                texts = []
                if target_message and hasattr(target_message, "content"):
                    if isinstance(target_message.content, list):
                        for content_item in target_message.content:
                            item_type = getattr(content_item, "type", None)
                            if item_type == "output_text" or (
                                item_type is None and hasattr(content_item, "text")
                            ):
                                texts.append(getattr(content_item, "text", ""))
                                for ann in getattr(content_item, "annotations", None) or []:
                                    citation = _url_citation_from_annotation(ann)
                                    if citation is not None:
                                        citations.append(citation)
                    else:
                        texts.append(str(target_message.content))
                content = "\n".join(texts) if texts else ""
            elif isinstance(response.output, dict):
                content = response.output.get("content", "")
            elif isinstance(response.output, str):
                content = response.output
            elif hasattr(response.output, "content"):
                content = response.output.content
        elif hasattr(response, "choices"):
            # Chat-style response format (might be returned by responses API for non-background)
            if response.choices and len(response.choices) > 0:
                # Check if it's a message or direct content
                choice = response.choices[0]
                if hasattr(choice, "message") and hasattr(choice.message, "content"):
                    content = choice.message.content
                elif hasattr(choice, "text"):
                    content = choice.text
                elif hasattr(choice, "content"):
                    content = choice.content
        elif hasattr(response, "content"):
            content = response.content
        elif hasattr(response, "text"):
            content = response.text

        # When no content can be extracted, log debug info to console (verbose only)
        if not content and response:
            if verbose:
                debug_print_empty_response(response, provider_label="OpenAI")
            return "No content in response"

        # Extract reasoning from the new format if available
        reasoning_content = ""
        if hasattr(response, "output") and isinstance(response.output, list):
            for item in response.output:
                if hasattr(item, "type") and item.type == "reasoning":
                    if hasattr(item, "summary") and item.summary:
                        if isinstance(item.summary, list):
                            parts = []
                            for s in item.summary:
                                text_attr = getattr(s, "text", None)
                                parts.append(str(text_attr) if text_attr is not None else str(s))
                            reasoning_content = "\n".join(parts)
                        else:
                            reasoning_content = str(item.summary)
                    break

        # Include reasoning summary if available (either from new format or old format)
        if reasoning_content:
            content = f"## Reasoning Summary\n{reasoning_content}\n\n{content}"
        elif hasattr(response, "reasoning") and response.reasoning:
            if isinstance(response.reasoning, dict) and response.reasoning.get("summary"):
                reasoning_summary = response.reasoning["summary"]
                content = f"## Reasoning Summary\n{reasoning_summary}\n\n{content}"

        # Append deduplicated sources section when citations are present
        sources = render_sources_block(citations)
        if sources:
            content += f"\n\n{sources}"

        return content if content else "No content in response"

    async def list_models(self) -> list[dict[str, Any]]:
        """List available models including Responses API models"""
        # Start with known Responses API models
        response_models = [
            {
                "id": "o3",
                "type": "response",
                "description": "Standard response model for general tasks",
                "created": 1719500000,  # Approximate timestamp
                "owned_by": _PROVIDER_NAME_OPENAI,
            },
            {
                "id": "gpt-5.6-sol",
                "type": "deep_research",
                "description": (
                    "Deep research via the general-purpose gpt-5.6 flagship: web search "
                    "and code execution, with research depth supplied by reasoning effort "
                    "and instructions rather than by a specialised model"
                ),
                "created": 1782228018,
                "owned_by": _PROVIDER_NAME_OPENAI,
            },
        ]

        # Also try to fetch other models from API
        try:
            response = await self.client.models.list()
            api_models = []
            for model in response.data:
                # Include all models without filtering
                # Don't duplicate the response models we already added
                if model.id not in ["o3", "gpt-5.6-sol"]:
                    # `/v1/models` lists retired models too, carrying a past
                    # `shutdown_date`. Discarding that field is what let the
                    # o3/o4-mini shutdown stay invisible: the IDs still appear,
                    # but every call returns `model_not_found`. Surface it.
                    shutdown = getattr(model, "shutdown_date", None)
                    api_models.append(
                        {
                            "id": model.id,
                            "created": model.created,
                            "owned_by": model.owned_by,
                            "type": "retired" if _is_retired(shutdown) else "unknown",
                            "shutdown_date": shutdown,
                        }
                    )
            # Combine and sort all models
            all_models = response_models + api_models
            return sorted(all_models, key=lambda x: x.get("created", 0), reverse=True)
        except Exception as e:
            raise ProviderError(_PROVIDER_NAME_OPENAI, f"Failed to fetch models: {str(e)}")

    async def list_models_cached(
        self, force_refresh: bool = False, no_cache: bool = False
    ) -> list[dict[str, Any]]:
        """List OpenAI models with caching support

        Args:
            force_refresh: If True, bypass cache and fetch fresh data
            no_cache: If True, bypass cache without updating it

        Returns:
            List of model dictionaries from cache or API
        """
        # If no_cache is True, bypass cache without updating
        if no_cache:
            return await self.list_models()

        # Check if cache is valid
        if not force_refresh and self.model_cache.is_cache_valid():
            # Try to load from cache
            cached_models = self.model_cache.load_cache()
            if cached_models is not None:
                return cached_models

        # Fetch fresh models from API
        models = await self.list_models()

        # Save to cache (only when not using no_cache)
        self.model_cache.save_cache(models)

        return models
