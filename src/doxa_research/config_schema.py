"""Schema-driven configuration defaults for Doxa Research (P33).

Three-layer architecture:
  Layer 1 — DoxaConfig (this module): Pydantic models that define the
             canonical schema with typed defaults.
  Layer 2 — Overlay / partial shapes (Task 3): Partial variants used for
             merging user/project config files and CLI overrides.
  Layer 3 — ConfigSchema.get_defaults() (src/doxa_research/config.py): Legacy dict-
             based defaults that are materialized from DoxaConfig on demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

# ---------------------------------------------------------------------------
# Module-level loader metadata
# ---------------------------------------------------------------------------

# Set to True by the CLI --no-validate flag so the validation layer (Task 5)
# can skip strict checks when requested.
_no_validate: bool = False


# ---------------------------------------------------------------------------
# StarterField helper
# ---------------------------------------------------------------------------


def StarterField(default: Any = ..., *, default_factory: Any = None, **kwargs: Any) -> Any:  # noqa: N802
    """Mark a literal default as shipped in the starter template.

    Fields with ``default_factory=...`` are env-derived (computed at runtime
    per-user by Pydantic) and are intentionally NOT marked ``in_starter`` —
    they must not appear in the on-disk starter template, since baking a
    literal env-derived value would ship one user's environment to every
    install. See docs/superpowers/specs/2026-05-21-p40-on-disk-starter-template-design.md.
    """
    if default_factory is not None:
        return Field(default_factory=default_factory, **kwargs)
    extra = {"in_starter": True}
    return Field(default, json_schema_extra=extra, **kwargs)


# ---------------------------------------------------------------------------
# Per-section sub-models
# ---------------------------------------------------------------------------


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_project: str = StarterField("")
    default_mode: str = StarterField("default")


def _checkpoint_dir_default() -> str:
    from doxa_research.paths import user_checkpoints_dir  # lazy import avoids circular

    return str(user_checkpoints_dir())


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_output_dir: str = StarterField("./research-outputs")
    checkpoint_dir: str = StarterField(default_factory=_checkpoint_dir_default)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval: int = StarterField(30)
    max_wait: int = StarterField(30)
    parallel_providers: bool = StarterField(True)
    retry_attempts: int = StarterField(3)
    max_transient_errors: int = Field(5)  # advanced — NOT StarterField
    auto_input: bool = StarterField(True)
    prompt_max_bytes: int = Field(1024 * 1024)
    cancel_upstream_on_interrupt: bool = Field(True)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    combine_reports: bool = StarterField(False)
    # Standardization #7: previously also accepted "json", but the "json"
    # branch was only half-implemented — it changed the file extension to
    # `.json` and skipped the YAML frontmatter, but the file content was
    # still raw markdown text. A `.json` file containing markdown was
    # worse than nothing. Users who want JSON output for scripting should
    # use the `--json` envelope flag on individual commands instead
    # (see docs/json-output.md).
    format: Literal["markdown"] = StarterField("markdown")
    include_metadata: bool = StarterField(True)
    timestamp_format: str = StarterField("%Y-%m-%d_%H%M%S")


_CLARIFICATION_SYSTEM_PROMPT = (
    "I don't want you to follow the above question and instructions; "
    "I want you to tell me the ways this is unclear, point out any ambiguities "
    "or anything you don't understand. Follow that by asking questions to help "
    "clarify the ambiguous points. Once there are no more unclear, ambiguous or "
    "not understood portions, help me draft a clear version of the "
    "question/instruction."
)


class ClarificationCLIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = StarterField("openai")
    model: str = StarterField("gpt-4o-mini")
    temperature: float = StarterField(0.7)
    max_tokens: int = StarterField(500)
    retry_attempts: int = StarterField(3)
    retry_delay: float = StarterField(2.0)
    system_prompt: str = StarterField(_CLARIFICATION_SYSTEM_PROMPT)


class ClarificationInteractiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = StarterField("openai")
    model: str = StarterField("gpt-4o-mini")
    temperature: float = StarterField(0.7)
    max_tokens: int = StarterField(800)
    retry_attempts: int = StarterField(3)
    retry_delay: float = StarterField(2.0)
    system_prompt: str = StarterField(_CLARIFICATION_SYSTEM_PROMPT)
    input_height: int = StarterField(6)
    max_input_height: int = StarterField(15)


class ClarificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cli: ClarificationCLIConfig = Field(default_factory=ClarificationCLIConfig)
    interactive: ClarificationInteractiveConfig = Field(
        default_factory=ClarificationInteractiveConfig
    )


class ModeConfig(BaseModel):
    """Schema for `[modes.<name>]` and profile-overlaid mode tables.

    Models the well-known mode-level fields plus per-provider namespaces
    (`[modes.<name>.openai]`, `[modes.<name>.perplexity]`,
    `[modes.<name>.gemini]`). The provider parameter normalizer validates
    recognized namespace keys at runtime and routes explicit extension bags
    such as `perplexity.extra_body`.

    Provider namespaces stay typed as permissive `dict[str, Any]` at the
    schema layer so the TOML shape is accepted there. Runtime normalization is
    stricter: namespace keys must be registered provider parameters or
    explicit extension bags such as `extra_body`. Typos in the well-known
    fields above are still caught by `extra="forbid"`.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    kind: Literal["immediate", "background"] | None = None
    system_prompt: str | None = None
    prompt_prefix: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_tool_calls: int | None = None
    providers: list[str] | None = None
    parallel: bool | None = None
    previous: str | None = None
    next: str | None = None
    auto_input: bool | None = None
    description: str | None = None
    stream: bool | None = None

    # Provider namespaces. Runtime normalization validates recognized keys and
    # explicit extension bags; the schema only accepts the namespace shape.
    openai: dict[str, Any] | None = None
    perplexity: dict[str, Any] | None = None
    gemini: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Provider configs (filled in Task 4)
# ---------------------------------------------------------------------------


class ProviderConfigBase(BaseModel):
    """Common provider config surface — shared by all providers.

    Renamed from `ProviderBase` per P33 review remediation #7 to avoid
    confusion with the runtime `ResearchProvider` class in
    `src/doxa_research/providers/base.py`.
    """

    model_config = ConfigDict(extra="forbid")
    api_key: str = StarterField()  # required — no default; shipped in init template
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    base_url: str | None = None


class OpenAIConfig(ProviderConfigBase):
    """OpenAI provider config.

    Models the keys that `OpenAIProvider` reads from `self.config` at runtime
    (see `src/doxa_research/providers/openai.py`). New runtime keys must be modeled
    here so `extra="forbid"` doesn't reject valid user configs.
    """

    organization: str | None = None
    max_tool_calls: int | None = None
    code_interpreter: bool | None = None
    background: bool | None = None
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    tool_choice: str | None = None
    web_search: bool | None = None


class PerplexityConfig(ProviderConfigBase):
    """Perplexity provider config.

    Models the keys that `PerplexityProvider` reads from `self.config` at
    runtime (see `src/doxa_research/providers/perplexity.py`):

    - Top-level direct SDK keys: `top_p`, `stop`, `response_format`
      (joined with the inherited `temperature`, `max_tokens`).
    - `perplexity` nested namespace: forwarded verbatim to the SDK's
      `extra_body` (e.g. `web_search_options.search_context_size`,
      `stream_mode`). Modeled as a permissive `dict[str, Any]` because
      Perplexity's namespace evolves with their API and we don't want
      schema drift to block valid user configs.

    Note: `search_context_size` lives at
    `[providers.perplexity.perplexity.web_search_options]` (or the
    equivalent mode namespace), NOT at the top level. The pre-merge
    placement was incorrect.
    """

    top_p: float | None = None
    stop: list[str] | None = None
    response_format: dict[str, Any] | None = None
    perplexity: dict[str, Any] | None = None


class GeminiConfig(ProviderConfigBase):
    """Gemini provider config — placeholder for P28.

    Inherits `api_key`, `model`, `temperature`, `max_tokens`, `timeout`,
    `base_url`. Provider-specific fields (e.g. safety settings) will be
    added when P28 lands.
    """

    # Forward-compat for v1.1: these provider-namespace tunables are
    # accepted by the schema but NOT yet honored by the runtime polling
    # loop in v1. The runtime currently reads `[execution].poll_interval`
    # / `[execution].max_wait` for ALL providers (see src/doxa_research/run.py
    # _run_polling_loop). v1.1 will wire per-provider overrides:
    # `[providers.gemini].poll_interval` / `.max_wait_minutes` will then
    # take precedence over the global `[execution]` values when polling
    # a kind="background" Gemini interaction.
    #
    # v1 workaround for Gemini DR users who need longer waits: set
    # `[execution].max_wait = 60` in your config (default is 30 minutes;
    # upstream Gemini DR hard limit is 60 minutes).
    poll_interval: int = Field(
        default=10,
        description=(
            "Seconds between Deep Research interaction.get polls. "
            "v1: schema-only (runtime uses [execution].poll_interval); "
            "wired in v1.1."
        ),
    )
    max_wait_minutes: int = Field(
        default=60,
        description=(
            "Max research time before timing out the polling loop. "
            "Upstream Gemini Deep Research hard-limit is 60 minutes; "
            "exceeding indicates a server-side stall. "
            "v1: schema-only (runtime uses [execution].max_wait); "
            "wired in v1.1."
        ),
    )


class MockConfig(ProviderConfigBase):
    """Mock provider config — used in tests and local development."""

    pass


class ProvidersConfig(BaseModel):
    """`[providers]` super-table — typed per-provider section."""

    model_config = ConfigDict(extra="forbid")
    openai: OpenAIConfig = StarterField(
        default_factory=lambda: OpenAIConfig(api_key="${OPENAI_API_KEY}"),
    )
    perplexity: PerplexityConfig = StarterField(
        default_factory=lambda: PerplexityConfig(api_key="${PERPLEXITY_API_KEY}"),
    )
    gemini: GeminiConfig = StarterField(
        default_factory=lambda: GeminiConfig(api_key="${GEMINI_API_KEY}"),
    )
    mock: MockConfig | None = Field(None)  # not StarterField — test/dev only


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------


class DoxaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # TODO(P33): unify with CONFIG_VERSION from doxa_research.config (circular import
    # risk — left as literal until Task 6 refactors the import graph)
    version: str = "2.0"
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    clarification: ClarificationConfig = Field(default_factory=ClarificationConfig)
    modes: dict[str, ModeConfig] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Overlay/partial/user-file shapes (filled in Task 3)
# ---------------------------------------------------------------------------


def make_partial(model: type[BaseModel], *, suffix: str = "Partial") -> type[BaseModel]:
    """Return a new BaseModel subclass with every field made optional.

    Recursively partials nested BaseModel-typed fields. `dict[str, BaseModel]`
    fields are kept as-is — their value type is already optional-shaped (e.g.
    `ModeConfig` has every field optional).

    The returned model has `model_config = ConfigDict(extra="forbid")` so
    it still catches typos in keys it knows about.
    """
    new_fields: dict[str, Any] = {}
    for name, finfo in model.model_fields.items():
        annotation = finfo.annotation

        # If the field type is itself a BaseModel, recurse.
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            sub_partial = make_partial(annotation, suffix=suffix)
            new_fields[name] = (sub_partial | None, None)
            continue

        # Otherwise wrap whatever it was in Optional with None default.
        new_fields[name] = (annotation | None, None)

    return create_model(
        f"{model.__name__}{suffix}",
        __config__=ConfigDict(extra="forbid"),
        **new_fields,
    )


PartialDoxaConfig: type[BaseModel] = make_partial(DoxaConfig)
"""Mechanically-derived runtime partial — used for CLI/profile-overlay
internals and as the alignment baseline for TS03."""

# Pre-computed partials used in ConfigOverlay field annotations.
_PartialPathsConfig: type[BaseModel] = make_partial(PathsConfig)
_PartialExecutionConfig: type[BaseModel] = make_partial(ExecutionConfig)
_PartialOutputConfig: type[BaseModel] = make_partial(OutputConfig)
_PartialClarificationConfig: type[BaseModel] = make_partial(ClarificationConfig)
_PartialProvidersConfig: type[BaseModel] = make_partial(ProvidersConfig)


class GeneralOverlay(BaseModel):
    """`[general]` table as it can appear in user/profile/cli overlays.

    Mirrors `GeneralConfig`'s fields (all optional) plus the P21 user-only
    fields that are NOT part of `get_defaults()`.
    """

    model_config = ConfigDict(extra="forbid")
    default_project: str | None = None
    default_mode: str | None = None
    default_profile: str | None = None
    prompt_prefix: str | None = None


class ConfigOverlay(BaseModel):
    """A user/profile/cli config layer.

    Every runtime-default field is optional (mirror of `make_partial(DoxaConfig)`)
    and `general` is replaced by `GeneralOverlay` so P21 user-only fields are
    accepted.
    """

    model_config = ConfigDict(extra="forbid")
    version: str | None = None
    general: GeneralOverlay | None = None
    paths: _PartialPathsConfig | None = None  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    execution: _PartialExecutionConfig | None = None  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    output: _PartialOutputConfig | None = None  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    providers: _PartialProvidersConfig | None = None  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    clarification: _PartialClarificationConfig | None = None  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    modes: dict[str, ModeConfig] | None = None


class ProfileConfig(ConfigOverlay):
    """Profile overlay schema.

    Same as `ConfigOverlay` plus a profile-root `prompt_prefix` (P21).
    """

    prompt_prefix: str | None = None


class UserConfigFile(ConfigOverlay):
    """Top-level shape of an actual `doxa.config.toml` on disk.

    `ConfigOverlay` fields plus a `profiles` super-table (validated by
    `ProfileConfig`) and an `experimental` carve-out (the only field that
    permits arbitrary keys).
    """

    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    experimental: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation report types (filled in Task 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationWarning:
    """A single advisory finding from schema validation."""

    layer: str
    path: str
    message: str
    value_preview: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    """Result of `ConfigSchema.validate()`. Empty `warnings` does NOT mean
    fully valid — validation is advisory."""

    warnings: tuple[ValidationWarning, ...] = ()


# ---------------------------------------------------------------------------
# Path resolution helper
# ---------------------------------------------------------------------------


def resolve_path(
    model: type[BaseModel],
    path: tuple[str, ...],
) -> tuple[type[BaseModel], str]:
    """Walk *path* through nested Pydantic BaseModel fields.

    Returns ``(model_class, field_name)`` for the leaf field.

    Raises ``KeyError`` when *path* does not correspond to a declared field.

    Handles:
    - Empty path → KeyError
    - ``head not in model.model_fields`` → KeyError with model name
    - Nested BaseModel → recurse into sub-model
    - ``dict[str, BaseModel]`` annotation → consume one extra path step (the
      dict key) as a wildcard, then recurse into the value type. If path stops
      at the dict-keyed level, accept as resolved.
    - Otherwise → KeyError saying path continues past leaf
    """
    import typing

    if not path:
        raise KeyError(f"Empty path passed to resolve_path for {model.__name__!r}")

    head, *rest = path

    if head not in model.model_fields:
        raise KeyError(
            f"Field {head!r} not found in {model.__name__!r}. "
            f"Available: {sorted(model.model_fields)}"
        )

    if not rest:
        # Leaf reached
        return (model, head)

    # Inspect the annotation to decide how to recurse
    annotation = model.model_fields[head].annotation

    # Unwrap Optional[X] / X | None → X  (handles both typing.Union and PEP 604 types.UnionType)
    import types

    origin = typing.get_origin(annotation)
    if origin is typing.Union or isinstance(annotation, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            annotation = args[0]
            origin = typing.get_origin(annotation)

    # Case 1: nested BaseModel
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return resolve_path(annotation, tuple(rest))

    # Case 2: dict[str, SomeType] — consume the next path step as a dict key
    if origin is dict:
        dict_args = typing.get_args(annotation)
        if len(dict_args) == 2:
            value_type = dict_args[1]
            # Consume the wildcard key step
            _key_step, *rest_after_key = rest
            if not rest_after_key:
                # Path stopped at dict key level — accepted as resolved
                return (model, head)
            # Recurse into value type if it's a BaseModel
            if isinstance(value_type, type) and issubclass(value_type, BaseModel):
                return resolve_path(value_type, tuple(rest_after_key))
            # dict[str, dict[...]] or other — if path still has steps we can't
            # validate further; treat as resolved to avoid false negatives
            return (model, head)

    raise KeyError(f"Path {rest!r} continues past leaf field {head!r} in {model.__name__!r}")


# ---------------------------------------------------------------------------
# Public façade (filled in Task 5)
# ---------------------------------------------------------------------------


def default_config_dict() -> dict[str, Any]:
    """Materialize runtime defaults from the root schema.

    Instantiate the schema on every call so default_factory fields reflect the
    current environment. `paths.checkpoint_dir` depends on XDG_STATE_HOME and
    must not be frozen at module import time.
    """
    return DoxaConfig().model_dump(mode="python", exclude_none=True)


class ConfigSchema:
    """Public façade for the schema module."""

    @staticmethod
    def get_defaults() -> dict[str, Any]:
        return default_config_dict()

    @staticmethod
    def model() -> type[BaseModel]:
        return DoxaConfig

    @staticmethod
    def starter_keys() -> set[tuple[str, ...]]:
        result: set[tuple[str, ...]] = set()
        _collect_starter_paths(DoxaConfig, prefix=(), out=result)
        return result

    @staticmethod
    def validate(
        data: dict[str, Any],
        *,
        layer: str = "user",
        strict: bool = False,
    ) -> ValidationReport:
        if _no_validate:
            return ValidationReport()
        target = _layer_to_model(layer)
        if strict:
            target.model_validate(data)
            return ValidationReport()
        try:
            target.model_validate(data)
        except ValidationError as exc:
            warnings = tuple(_pydantic_error_to_warning(layer, e) for e in exc.errors())
            return ValidationReport(warnings=warnings)
        return ValidationReport()


def _layer_to_model(layer: str) -> type[BaseModel]:
    if layer == "user" or layer == "project":
        return UserConfigFile
    if layer == "profile":
        return ProfileConfig
    if layer == "cli" or layer == "env":
        return ConfigOverlay
    if layer == "defaults":
        return DoxaConfig
    raise ValueError(f"unknown validation layer {layer!r}")


def _pydantic_error_to_warning(layer: str, error: Any) -> ValidationWarning:
    loc = error.get("loc", ())
    path = ".".join(str(p) for p in loc)
    msg = error.get("msg", "validation error")
    raw_value = error.get("input", None)
    preview: str | None = None
    if raw_value is not None:
        s = repr(raw_value)
        preview = s if len(s) <= 80 else s[:77] + "..."
    return ValidationWarning(layer=layer, path=path, message=msg, value_preview=preview)


def _collect_starter_paths(
    model: type[BaseModel],
    prefix: tuple[str, ...],
    out: set[tuple[str, ...]],
) -> None:
    """Walk every BaseModel field, descending unconditionally into nested
    BaseModels. Only fields marked `StarterField(...)` (i.e. with
    `json_schema_extra["in_starter"]` truthy) are emitted as leaves.

    Containers (like `DoxaConfig.general: GeneralConfig`) don't need to
    be StarterField themselves — only their leaf children do.
    """
    for name, finfo in model.model_fields.items():
        extra = (finfo.json_schema_extra or {}) if isinstance(finfo.json_schema_extra, dict) else {}
        is_starter = bool(extra.get("in_starter"))
        path = prefix + (name,)
        annotation = finfo.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            # Always recurse — the leaf decides shipping.
            _collect_starter_paths(annotation, path, out)
        elif is_starter:
            out.add(path)
