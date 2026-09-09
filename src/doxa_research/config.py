"""Layered configuration for Doxa Research.

Precedence (lowest → highest): defaults → user TOML → project TOML → env → CLI.

- ConfigSchema.get_defaults(): canonical default dict
- ConfigManager: loads + merges layers and exposes dot-notation `.get()`
- get_config(): returns a fully-loaded ConfigManager (respecting _config_path override)
- BUILTIN_MODES: baked-in mode presets merged with `[modes.*]` TOML tables
- Mode dicts may optionally carry `async: bool` to override the default
  background/immediate derivation from the model name (see is_background_mode)
- DOXA_VERSION / CONFIG_VERSION: version constants
- _config_path: process-wide override path, set by the CLI `--config` option
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

from rich.console import Console

from doxa_research import __version__
from doxa_research.config_legacy import (
    detect_legacy_paths,
    format_legacy_config_guidance,
)
from doxa_research.config_profiles import (
    ProfileLayer,
    ProfileSelection,
    collect_profile_catalog,
    resolve_profile_layer,
    resolve_profile_selection,
    without_profiles,
)
from doxa_research.errors import ConfigAmbiguousError, ConfigNotFoundError
from doxa_research.paths import user_config_file

# Console used for config-warning output only.
# Writes to stderr to avoid contaminating JSON stdout output.
_console = Console(stderr=True)

# Version tracking
DOXA_VERSION = __version__
CONFIG_VERSION = "2.0"

# Process-wide config path override. Set by the CLI `--config` option.
_config_path: Path | None = None


# Built-in mode definitions
BUILTIN_MODES = {
    "default": {
        "provider": "openai",
        "model": "o3",
        "kind": "immediate",
        "system_prompt": None,
        "description": "Default mode - passes prompt directly to LLM without any system prompt",
        "auto_input": False,
    },
    "clarification": {
        "provider": "openai",
        "model": "o3",
        "kind": "immediate",
        "system_prompt": """I don't want you to follow the above question and instructions; I want you to tell me the ways this is unclear, point out any ambiguities or anything you don't understand. Follow that by asking questions to help clarify the ambiguous points. Once there are no more unclear, ambiguous or not understood portions, help me draft a clear version of the question/instruction.""",
        "description": "Clarifying takes the prompt to get. Ask clarifying questions to get rid of anything that's ambiguous, unclear, and also make suggestions on what would be a better question.",
        "next": "exploration",
    },
    "quick_research": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Conduct quick, focused research with key findings and essential information. Be concise but thorough.",
        "description": "Concise background research — same model as deep_research with a briefer prompt; set max_tool_calls to bound cost.",
        "auto_input": False,
        # o4-mini-deep-research (retired 2026-07-23) was this mode's cheap
        # tier and has no cheaper replacement: OpenAI names gpt-5.6-sol for
        # both retired models. This mode is now distinguished only by its
        # system prompt. Deliberately no default `max_tool_calls` — an
        # invented cap here would silently override limits users already set
        # on this mode. Set one explicitly to bound cost.
    },
    "mini_research": {
        # P18 rename: `mini_research` → `quick_research`. Stub kept for one
        # minor with deprecation warning at resolution. Removed in v4.0.0
        # (future P19).
        "_deprecated_alias_for": "quick_research",
    },
    "exploration": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Explore the topic at hand, looking at options, alternatives, different trade-offs, and make recommendations based on the use case or alternative/related technologies.",
        "description": "Exploration looks at the topic at hand and explores some options and alternatives, different trade-offs, and makes recommendations based on the use case or just alternative and related technologies.",
        "previous": "clarification",
        "next": "deep_dive",
    },
    "deep_dive": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Deep dive into the specific technology, giving an overview, going deep on it, discussing it, and exploring it. For APIs, cover what the API is, how it works, assumptions, dependencies, if it's deprecated, common pitfalls. For other technologies, cover what the technology is and how it's used.",
        "description": "This deep dives into a specific technology, giving an overview of it, going deep on it, discussing it, and exploring it.",
        "previous": "exploration",
        "next": "tutorial",
    },
    "tutorial": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Create a detailed tutorial with examples of how the technologies are used in common scenarios to get started, along with code samples, command-line execution process, and other useful information.",
        "description": "The tutorial goes into a detailed explanation with examples of how the technologies are used in common scenarios to get started.",
        "previous": "deep_dive",
        "next": "solution",
    },
    "solution": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Design a specific solution to solve the given problem using appropriate technology. Focus on practical implementation.",
        "description": "A solution generally goes into a specific solution to solve a specific problem using technology.",
        "previous": "tutorial",
        "next": "prd",
    },
    "prd": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Create a Product Requirements Document based on prior research. Use previous research on solutions and technologies to create a comprehensive requirements document.",
        "description": "Product Requirements Document based on prior research, we'll create the PRD looking at previous research on solutions to technologies.",
        "previous": "solution",
        "next": "tdd",
    },
    "tdd": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Create a Technical Design Document based on the PRD and prior research. Consider best practices on architecture and good abstractions to make things maintainable and well-structured in code.",
        "description": "The Technical Design Document based on the PRD and prior research puts together a technical design document.",
        "previous": "prd",
    },
    "thinking": {
        "provider": "openai",
        "model": "o3",
        "kind": "immediate",
        "temperature": 0.4,
        "system_prompt": "You are a helpful assistant for quick analysis.",
        "description": "Quick thinking and analysis mode for simple questions.",
    },
    "openai_reasoning": {
        "provider": "openai",
        "model": "o3",
        "kind": "immediate",
        "system_prompt": "You are a helpful assistant for quick, grounded analysis.",
        "description": "OpenAI o3 immediate mode with reasoning summaries and web search grounding.",
        "openai": {
            "reasoning_summary": "auto",
            "web_search": True,
        },
    },
    "deep_research": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "providers": ["openai"],
        "parallel": True,
        "system_prompt": "Conduct comprehensive research with citations and multiple perspectives.\nOrganize findings clearly and highlight key insights.",
        "description": "Deep research mode using OpenAI for comprehensive analysis.",
        "previous": "exploration",
        "auto_input": True,
    },
    "comparison": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "kind": "background",
        "system_prompt": "Compare and contrast the given options, technologies, or approaches. Provide a detailed analysis of pros, cons, and recommendations.",
        "description": "Comparative analysis mode for evaluating multiple options.",
    },
    # P23: Perplexity built-in immediate (synchronous) modes.
    "perplexity_quick": {
        "provider": "perplexity",
        "model": "sonar",
        "kind": "immediate",
        "system_prompt": None,
        "description": "Perplexity Sonar — fast grounded answer with citations.",
        "perplexity": {
            "web_search_options": {"search_context_size": "low"},
            "stream_mode": "full",
        },
    },
    "perplexity_pro": {
        "provider": "perplexity",
        "model": "sonar-pro",
        "kind": "immediate",
        "system_prompt": None,
        "description": "Perplexity Sonar Pro — deeper retrieval, richer context.",
        "perplexity": {
            "web_search_options": {"search_context_size": "high"},
            "stream_mode": "full",
        },
    },
    "perplexity_reasoning": {
        "provider": "perplexity",
        "model": "sonar-reasoning-pro",
        "kind": "immediate",
        "system_prompt": None,
        "description": "Perplexity Sonar Reasoning Pro — exposes <think> chain-of-thought.",
        "perplexity": {
            "web_search_options": {"search_context_size": "medium"},
            "stream_mode": "concise",
        },
    },
    # P27: Perplexity background deep-research mode (async API).
    "perplexity_deep_research": {
        "provider": "perplexity",
        "model": "sonar-deep-research",
        "kind": "background",
        "system_prompt": None,
        "description": "Perplexity Sonar Deep Research — multi-step research via async API. ~$1.32/query at reasoning_effort=high.",
        "perplexity": {
            "reasoning_effort": "high",
        },
    },
    # P24: Gemini built-in immediate (synchronous) modes.
    "gemini_quick": {
        "provider": "gemini",
        "model": "gemini-2.5-flash-lite",
        "kind": "immediate",
        "description": "Fast Gemini 2.5 Flash-Lite with web grounding (no thinking).",
        "gemini": {
            "tools": ["google_search"],
            "thinking_budget": 0,
        },
    },
    "gemini_pro": {
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "kind": "immediate",
        "description": "Gemini 2.5 Pro with web grounding and dynamic thinking.",
        "gemini": {
            "tools": ["google_search"],
            "thinking_budget": -1,
        },
    },
    "gemini_reasoning": {
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "kind": "immediate",
        "description": "Gemini 2.5 Pro with web grounding, dynamic thinking, and surfaced thought summaries.",
        "gemini": {
            "tools": ["google_search"],
            "thinking_budget": -1,
            "include_thoughts": True,
        },
    },
    "gemini_quick_research": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Provide a brief, focused research summary in under 500 words.",
        "description": "Quick Gemini Deep Research — short summary.",
        "previous": "exploration",
        "next": "gemini_deep_dive",
    },
    "gemini_exploration": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Explore the topic broadly. Identify key dimensions, controversies, and open questions.",
        "description": "Open-ended exploratory Gemini Deep Research.",
        "previous": None,
        "next": "gemini_deep_dive",
    },
    "gemini_deep_dive": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Conduct an in-depth, comprehensive analysis. Cite sources.",
        "description": "In-depth Gemini Deep Research dive.",
        "previous": "exploration",
        "next": "gemini_tutorial",
    },
    "gemini_tutorial": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Produce a step-by-step tutorial. Include code examples where applicable.",
        "description": "Tutorial-format Gemini Deep Research.",
        "previous": "gemini_deep_dive",
        "next": "gemini_solution",
    },
    "gemini_solution": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Recommend a concrete solution. Justify trade-offs.",
        "description": "Solution-recommendation Gemini Deep Research.",
        "previous": "gemini_tutorial",
        "next": "gemini_prd",
    },
    "gemini_prd": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Draft a product-requirements document. Include scope, acceptance criteria, and risks.",
        "description": "PRD-format Gemini Deep Research.",
        "previous": "gemini_solution",
        "next": "gemini_tdd",
    },
    "gemini_tdd": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Produce a test-driven-development plan. Tests first, then implementation outline.",
        "description": "TDD-plan Gemini Deep Research.",
        "previous": "gemini_prd",
        "next": "gemini_deep_research",
    },
    "gemini_deep_research": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Conduct exhaustive Deep Research. Cover every relevant angle. Cite sources rigorously.",
        "description": "Exhaustive Gemini Deep Research.",
        "previous": None,
        "next": None,
    },
    "gemini_comparison": {
        "provider": "gemini",
        "model": "deep-research-preview-04-2026",
        "kind": "background",
        "system_prompt": "Compare alternatives across explicit criteria. Produce a comparison matrix.",
        "description": "Comparison-table Gemini Deep Research.",
        "previous": None,
        "next": None,
    },
    "all_deep_research": {
        "providers": ["openai", "perplexity", "gemini"],
        "parallel": True,
        "kind": "background",
        "system_prompt": (
            "Conduct comprehensive research with citations and multiple "
            "perspectives. Organize findings clearly and highlight key insights."
        ),
        "description": (
            "Parallel Deep Research across OpenAI, Perplexity, and Gemini. "
            "Fans one prompt out to all three providers concurrently, each "
            "using its own Deep Research model."
        ),
        "openai": {"model": "gpt-5.6-sol"},
        "perplexity": {"model": "sonar-deep-research"},
        "gemini": {"model": "deep-research-preview-04-2026"},
    },
    "openai_quick": {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "kind": "immediate",
        "description": ("Fast OpenAI gpt-4.1-mini immediate mode with web search grounding."),
        "openai": {
            "web_search": True,
        },
    },
}


#: Background-capable models whose IDs do not carry the "deep-research"
#: substring. OpenAI retired o3-deep-research and o4-mini-deep-research on
#: 2026-07-23 and names gpt-5.6-sol as their replacement; that ID encodes no
#: capability, so the naming convention below cannot classify it and every
#: such model must be registered here explicitly.
BACKGROUND_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5.6",  # documented alias for -sol; does not resolve on all accounts
        "gpt-5.6-sol",
    }
)


def is_background_model(model: str | None) -> bool:
    """Return True if a model implies background/long-running submission.

    Two rules, in order: an exact match against `BACKGROUND_MODELS`, then any
    model name containing the substring "deep-research". The substring rule
    still classifies Gemini's `deep-research-preview-*` agents and Perplexity's
    `sonar-deep-research`, which do follow the convention. Both rules are
    case-sensitive by design (provider model IDs are lowercase). `None` and
    empty string return False.

    P18 keeps this helper as the model-level source of truth for "what does
    *this provider* require for *this model*?" — used inside the OpenAI
    runtime mismatch check (`OpenAIProvider._validate_kind_for_model`) and
    inside `progress.py:should_show_spinner`. Resolution-path callers should
    use `mode_kind(cfg)` instead.
    """
    name = model or ""
    return name in BACKGROUND_MODELS or "deep-research" in name


#: Non-o-series models that reject the `temperature` request parameter.
#: There is no capability-discovery endpoint for this, and no derivable
#: pattern: verified live on 2026-09-08, gpt-5 and gpt-5.5 reject temperature
#: while gpt-5.1, gpt-5.2 and gpt-5.4 accept it. A `gpt-5*` prefix rule is
#: therefore wrong in both directions, so this list records measurements
#: rather than a guess and must be extended as models appear.
NO_TEMPERATURE_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.5",
        "gpt-5.6",  # alias for -sol; kept consistent with it
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
    }
)


#: Reasoning-effort values above "none". Only these suppress `temperature`;
#: "none" and any unrecognised value leave it in place. Verified live
#: 2026-09-08 that gpt-5.2 rejects temperature at "low", "medium" and "high",
#: and accepts it at "none" or with no reasoning block.
RAISED_REASONING_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

_O_SERIES_RE = re.compile(r"^o\d")


def _is_o_series(model: str) -> bool:
    r"""True for OpenAI reasoning models named o1/o3/o4-mini and friends.

    Matches `^o\d` rather than a bare "o" prefix, which also caught unrelated
    names such as "omni-1" and "openai-foo" and wrongly dropped their
    temperature. Case-sensitive by design: provider model IDs are lowercase,
    so "O3" is not a real ID and would fail as `model_not_found` regardless.
    """
    return bool(_O_SERIES_RE.match(model))


def requires_background_submission(model: str | None) -> bool:
    """Return True if `model` *cannot* run on the immediate/streaming path.

    Deliberately narrower than `is_background_model()`, which answers "should
    this default to background research?". The retired o3/o4-mini
    deep-research models and the Gemini deep-research agents offer no
    synchronous mode, so `kind = "immediate"` for them is a config error worth
    refusing before any HTTP call.

    gpt-5.6-sol is different: a general-purpose model that OpenAI supports
    streaming. It belongs in `BACKGROUND_MODELS` so research modes default to
    background submission with tools, but forbidding immediate use of it would
    be wrong. Conflating the two meant registering the replacement model
    silently removed a capability the model has.
    """
    return "deep-research" in (model or "")


def supports_temperature(model: str | None, reasoning_effort: str | None = None) -> bool:
    """Return True if this request may carry the `temperature` parameter.

    Support depends on the model *and* the effective reasoning effort, which
    is why `reasoning_effort` is part of the signature:

    - Every o-series reasoning model rejects temperature outright.
    - The models in `NO_TEMPERATURE_MODELS` reject it outright.
    - Models that otherwise accept it reject it once reasoning effort rises
      above "none". Verified live 2026-09-08: gpt-5.2 and gpt-5.4 accept
      temperature at effort "none" or unset, and reject it at "low" and "high".

    Unknown models with no raised effort default to *sending* temperature.
    That is deliberate: an unsupported parameter fails loudly with
    "Unsupported parameter: 'temperature' is not supported with this model",
    which is diagnosable, whereas silently discarding a configured temperature
    changes sampling with no signal. The rejection surfaces only on a real
    call against the live API; a request with empty `input` short-circuits on
    the missing input before reaching the
    compatibility check.
    """
    name = model or ""
    if _is_o_series(name) or name in NO_TEMPERATURE_MODELS:
        return False
    # Only a *recognised, raised* effort suppresses temperature. An
    # unrecognised value (a typo, or an empty string) must not silently change
    # sampling: leaving temperature in place lets the API reject the bad
    # effort loudly, which is the diagnosable failure.
    return reasoning_effort not in RAISED_REASONING_EFFORTS


def mode_kind(mode_config: dict[str, Any]) -> str:
    """Canonical resolver for a mode's execution kind.

    Returns "immediate" or "background". Precedence (highest first):

    1. Explicit `kind` field — the canonical declaration.
    2. Legacy `async: bool` field — emits a one-time DeprecationWarning per
       process; truthy → background, falsy → immediate. Removed in v4.0.0.
    3. Substring fallback via `is_background_model(model)` — emits a one-time
       warning per (mode_id) the first time it's used. User modes missing
       `kind` hit this path; builtins must declare `kind` explicitly (enforced
       by `tests/test_builtin_modes_have_kind.py`).
    """
    if "kind" in mode_config:
        return mode_config["kind"]
    if "async" in mode_config:
        import warnings

        warnings.warn(
            "Mode config field 'async' is deprecated; use 'kind' = 'immediate' or "
            "'background' instead. The 'async' field will be removed in v4.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return "background" if mode_config["async"] else "immediate"
    # Substring fallback — only user modes should reach this path.
    return "background" if is_background_model(mode_config.get("model")) else "immediate"


def is_background_mode(mode_config: dict[str, Any]) -> bool:
    """Return True if a mode submits as a long-running background job.

    Thin wrapper over `mode_kind(cfg)` kept for backwards-compatibility with
    code paths that pre-date P18. New code should call `mode_kind(cfg)`
    directly. Removed in v4.0.0 (see future P19).
    """
    return mode_kind(mode_config) == "background"


class ConfigSchema:
    """Configuration schema and defaults"""

    @staticmethod
    def get_defaults() -> dict[str, Any]:
        """Return default configuration.

        P33: derived from the typed schema in `doxa_research.config_schema`. Signature
        unchanged from pre-P33 callers' perspective.
        """
        from doxa_research.config_schema import default_config_dict

        # `default_config_dict()` instantiates the schema on every call, so
        # callers get a fresh dict and default_factory values reflect the
        # current environment.
        return default_config_dict()


class ConfigManager:
    """Manages layered configuration with clear precedence hierarchy"""

    def __init__(self, config_path: Path | None = None):
        self.user_config_path = config_path or user_config_file()
        self.project_config_paths = ["./doxa.config.toml", "./.doxa.config.toml"]
        self.layers: dict[str, dict[str, Any]] = {}
        self.data: dict[str, Any] = {}
        self.project_config_path: Path | None = None
        self.profile_selection: ProfileSelection = ProfileSelection(None, "none", None)
        self.active_profile: ProfileLayer | None = None
        self.profile_catalog: list[ProfileLayer] = []
        # P33: per-layer validation reports keyed by layer name.
        from doxa_research.config_schema import ValidationReport

        self.validation_reports: dict[str, ValidationReport] = {}

    def _validate_layer(self, layer: str, data: dict[str, Any]) -> None:
        """Validate a layer's raw data; collect warnings; emit to console."""
        from doxa_research.config_schema import ConfigSchema

        report = ConfigSchema.validate(data, layer=layer)
        self.validation_reports[layer] = report
        for w in report.warnings:
            _console.print(
                f"[yellow]config warning[/yellow] [{layer}] {w.path}: {w.message}",
                highlight=False,
            )

    def load_all_layers(self, cli_args: dict[str, Any] | None = None):
        """Load all configuration layers in precedence order"""
        raw_cli_args = cli_args or {}

        # Defense-in-depth (BUG-05): cli_args is the CLI override LAYER for
        # config values. Its keys must be either the `_profile` sentinel or
        # top-level config-schema keys (e.g., "general", "execution"). Anything
        # else is a programming error — most commonly someone confusing this
        # with a generic options bag and passing metadata like "config_path"
        # (which belongs in the ConfigManager(config_path=...) constructor).
        allowed_top_level = set(ConfigSchema.get_defaults().keys())
        for key in raw_cli_args:
            if key == "_profile" or key in allowed_top_level:
                continue
            raise ValueError(
                f"cli_args key {key!r} is not a known config root or sentinel. "
                f"Allowed: '_profile' or one of {sorted(allowed_top_level)}. "
                f"If {key!r} is metadata about which file to load (e.g. "
                f"'config_path'), pass it to ConfigManager(config_path=...) "
                f"instead."
            )

        cli_profile = raw_cli_args.get("_profile")
        cli_layer = {key: value for key, value in raw_cli_args.items() if key != "_profile"}

        # Layer 1: Internal defaults
        self.layers["defaults"] = ConfigSchema.get_defaults()

        # Layer 2: User config file (raw, including any [profiles.*] tables)
        if self.user_config_path.exists():
            user_raw = self._load_toml_file(self.user_config_path)
        else:
            user_raw = {}

        # Layer 3: Project config file (raw + actual path used)
        project_raw, project_path = self._load_project_config_with_path()
        self.project_config_path = project_path

        # Build the profile catalog from the raw layer data BEFORE stripping
        # profiles, then strip [profiles.*] tables out of the merged user/project
        # layers so they never participate in the regular layer merge.
        self.profile_catalog = collect_profile_catalog(
            user_config=user_raw,
            project_config=project_raw,
            user_path=self.user_config_path,
            project_path=project_path,
        )
        self.layers["user"] = without_profiles(user_raw)
        self._validate_layer("user", user_raw)
        self.layers["project"] = without_profiles(project_raw)
        self._validate_layer("project", project_raw)

        # Resolve which profile is active using a base view that contains
        # defaults + user + project (no env, no CLI). This honors
        # general.default_profile if set in either config file.
        base_config: dict[str, Any] = {}
        for layer_name in ["defaults", "user", "project"]:
            base_config = self._deep_merge(base_config, self.layers[layer_name])

        self.profile_selection = resolve_profile_selection(
            cli_profile=str(cli_profile) if cli_profile else None,
            base_config=base_config,
        )
        self.active_profile = resolve_profile_layer(self.profile_selection, self.profile_catalog)
        self.layers["profile"] = self.active_profile.data if self.active_profile else {}
        self._validate_layer("profile", self.layers["profile"])

        # Layer 5: Environment variables (per-setting overrides only)
        self.layers["env"] = self._get_env_overrides()

        # Layer 6: CLI arguments (per-setting overrides only; _profile sentinel stripped)
        self.layers["cli"] = cli_layer
        self._validate_layer("cli", cli_layer)

        # Merge all layers
        self.data = self._merge_layers()
        self.data = self._substitute_env_vars(self.data)

        # Validate configuration
        self._validate_config()

    def _load_toml_file(self, path: Path) -> dict[str, Any]:
        """Load and parse a TOML configuration file"""
        try:
            with open(path, "rb") as f:
                config = tomllib.load(f)

            # Check version compatibility
            config_version = config.get("version", "0.0")
            if config_version != CONFIG_VERSION:
                _console.print(f"[yellow]Warning:[/yellow] Config version mismatch in {path}")

            # Expand paths and substitute env vars
            config = self._expand_paths(config)
            config = self._substitute_env_vars(config)

            return config
        except Exception as e:
            _console.print(f"[yellow]Warning:[/yellow] Failed to load {path}: {e}")
            return {}

    def _load_project_config_with_path(self) -> tuple[dict[str, Any], Path | None]:
        """Load project-level configuration if it exists; also return its path.

        P21c: when more than one of the canonical project files exists, raise
        `ConfigAmbiguousError` rather than picking one. The two canonical names
        are siblings, not a precedence pair.
        """
        candidates = [Path(p) for p in self.project_config_paths]
        existing = [p for p in candidates if p.exists()]
        if len(existing) > 1:
            raise ConfigAmbiguousError(
                f"Two Doxa Research config files found in the project root:\n"
                f"  {existing[0]}\n"
                f"  {existing[1]}\n"
                f"\nDelete one before continuing. They are not merged and Doxa Research "
                f"will not pick between them.",
            )
        if not existing:
            return {}, None
        return self._load_toml_file(existing[0]), existing[0]

    def _load_project_config(self) -> dict[str, Any]:
        """Load project-level configuration if it exists"""
        data, _ = self._load_project_config_with_path()
        return data

    def _get_env_overrides(self) -> dict[str, Any]:
        """Get configuration overrides from environment variables"""
        overrides = {}

        # Map of env vars to config paths
        env_mappings = {
            "DOXA_DEFAULT_MODE": "general.default_mode",
            "DOXA_DEFAULT_PROJECT": "general.default_project",
            "DOXA_OUTPUT_DIR": "paths.base_output_dir",
            "DOXA_POLL_INTERVAL": "execution.poll_interval",
            "DOXA_MAX_WAIT": "execution.max_wait",
        }

        for env_var, config_path in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                # Convert config path to nested dict
                keys = config_path.split(".")
                current = overrides
                for key in keys[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]

                # Try to convert to appropriate type
                try:
                    if value.lower() in ("true", "false"):
                        current[keys[-1]] = value.lower() == "true"
                    elif value.isdigit():
                        current[keys[-1]] = int(value)
                    else:
                        current[keys[-1]] = value
                except Exception:
                    current[keys[-1]] = value

        return overrides

    def _merge_layers(self) -> dict[str, Any]:
        """Merge all configuration layers with proper precedence"""
        result = {}

        # Merge in order of precedence
        for layer_name in ["defaults", "user", "project", "profile", "env", "cli"]:
            if layer_name in self.layers and self.layers[layer_name]:
                result = self._deep_merge(result, self.layers[layer_name])

        return result

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep merge two dictionaries"""
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def _expand_paths(self, config: dict[str, Any]) -> dict[str, Any]:
        """Expand paths in configuration"""
        if "paths" in config:
            for key, value in config["paths"].items():
                path = Path(value).expanduser()
                if path.exists() and path.is_symlink():
                    path = path.resolve()
                config["paths"][key] = str(path)
        return config

    def _substitute_env_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Replace ${VAR} with environment variable values"""

        def substitute(value):
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                var_name = value[2:-1]
                return os.getenv(var_name, value)
            elif isinstance(value, dict):
                return {k: substitute(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [substitute(v) for v in value]
            return value

        return substitute(config)

    def _validate_config(self):
        """Post-merge invariant checks.

        Schema validation runs per-layer in `load_all_layers`; this method
        only enforces post-merge invariants (e.g. the user-modes kind
        deprecation nudge).
        """
        self._validate_user_modes_kind()

    def _validate_user_modes_kind(self) -> None:
        """P18 Phase H: warn-once when a user-defined mode is missing `kind`.

        Builtins are enforced by `tests/test_builtin_modes_have_kind.py`. User
        modes can omit `kind` for now and fall back to `mode_kind`'s substring
        heuristic, but we nudge migration. Becomes a hard error in v4.0.0
        (future P19).
        """
        import warnings as _warnings

        user_modes = self.data.get("modes") or {}
        for name, cfg in user_modes.items():
            if not isinstance(cfg, dict):
                continue
            # Skip alias stubs — they don't carry kind by design
            if "_deprecated_alias_for" in cfg:
                continue
            # Skip user overrides that only override a non-kind field on a builtin —
            # in that case the merged effective config still has a kind from the builtin
            if name in BUILTIN_MODES and "kind" in BUILTIN_MODES[name]:
                # Check whether the user override removed/contradicted kind
                if "kind" in cfg:
                    continue
                # User overlay on top of builtin without overriding kind: silent.
                continue
            if "kind" not in cfg:
                _warnings.warn(
                    f"User-defined mode '{name}' is missing 'kind' field; falling back "
                    f"to substring heuristic from model name. Set kind = 'immediate' or "
                    f"kind = 'background' explicitly. Will become an error in v4.0.0.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            # TODO(v4.0.0): error on missing kind in user modes (future P19)

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation"""
        keys = key.split(".")
        current = self.data

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current

    def get_mode_config(self, mode: str) -> dict[str, Any]:
        """Get mode configuration, merging built-in with user config.

        Raises ``ModeNotFoundError`` if ``mode`` is neither a built-in nor
        a user-defined mode in the loaded config. Prior behavior silently
        returned ``{}`` for unknown modes, which let bogus ``--mode FOO``
        invocations fall through to default-mode dispatch — confusing and
        a footgun. P18 alias stubs (`_deprecated_alias_for`) still resolve
        with a one-time DeprecationWarning per process.
        """
        from doxa_research.errors import ModeNotFoundError

        user_modes = self.data.get("modes", {}) if isinstance(self.data.get("modes"), dict) else {}
        in_builtin = mode in BUILTIN_MODES
        in_user = mode in user_modes
        if not in_builtin and not in_user:
            # Enumerate everything resolvable so the suggestion is actionable.
            available = sorted(
                {
                    name
                    for name in BUILTIN_MODES
                    if "_deprecated_alias_for" not in BUILTIN_MODES[name]
                }
                | set(user_modes)
            )
            raise ModeNotFoundError(mode, available_modes=available)

        # Resolve P18 alias stubs (e.g., `mini_research` → `quick_research`).
        builtin = BUILTIN_MODES.get(mode, {})
        target_name = builtin.get("_deprecated_alias_for")
        if target_name:
            import warnings as _warnings

            _warnings.warn(
                f"Mode '{mode}' is a deprecated alias for '{target_name}'; "
                f"update your invocation to use '{target_name}' directly. "
                f"The alias will be removed in v4.0.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            target_str = str(target_name)
            if target_str not in BUILTIN_MODES:
                # Defensive: an alias stub pointing at a non-existent target
                # would silently produce an empty mode_config and undermine
                # the ModeNotFoundError contract. Treat it as missing.
                available = sorted(
                    name
                    for name in BUILTIN_MODES
                    if "_deprecated_alias_for" not in BUILTIN_MODES[name]
                )
                raise ModeNotFoundError(target_str, available_modes=available)
            mode_config = BUILTIN_MODES[target_str].copy()
            user_mode = user_modes.get(mode, {})
            return self._deep_merge(mode_config, user_mode)

        mode_config = builtin.copy()
        user_mode = user_modes.get(mode, {})
        return self._deep_merge(mode_config, user_mode)

    def get_effective_config(self) -> dict[str, Any]:
        """Return the merged effective configuration"""
        return self.data


def get_config(profile: str | None = None) -> ConfigManager:
    """Get a fully-loaded ConfigManager instance with custom path if provided"""
    manager = ConfigManager(_config_path) if _config_path else ConfigManager()
    cli_args: dict[str, Any] = {}
    if profile:
        cli_args["_profile"] = profile
    manager.load_all_layers(cli_args)
    return manager


# ---------------------------------------------------------------------------
# P21c: legacy config ConfigNotFoundError formatter.
#
# This helper exists solely to enrich the "no Doxa Research config found" error
# message. Legacy detection is NEVER called from a successful load path - see
# tests/test_config_filename.py::test_c2_successful_load_does_not_call_legacy_detector.
# ---------------------------------------------------------------------------


def _format_config_not_found() -> ConfigNotFoundError:
    canonical = [
        str(user_config_file()),
        "./doxa.config.toml",
        "./.doxa.config.toml",
    ]
    legacy = detect_legacy_paths()
    lines = ["No Doxa Research config found.", "  Looked for:"]
    for path in canonical:
        lines.append(f"    {path}")
    guidance = format_legacy_config_guidance(legacy)
    if guidance:
        lines.append("")
        lines.append(guidance)
    return ConfigNotFoundError("\n".join(lines))
