"""Custom exception classes for Doxa Research.

Each exception carries a structured `message` + `suggestion` + `exit_code`
so `handle_error` can render a consistent CLI error banner. Exit codes:

  1  - generic DoxaError / KeyboardInterrupt fallback
  2  - APIKeyError (missing/invalid API key)
  3  - ProviderError (upstream provider failure)
  8  - DiskSpaceError
  9  - APIQuotaError
  10 - APIRateLimitError
  127 - uncaught unexpected exception (handled in handle_error)
"""

from __future__ import annotations

import os
from pathlib import Path


def format_config_context(config_path: Path | str, env_vars: list[str] | None = None) -> str:
    """Return a multi-line "Resolved from:" block for error bodies.

    Shows the config file path + whether it exists, and optional env vars
    + whether each is set. Used by APIKeyError so users can see what the
    tool consulted when resolving credentials.
    """
    p = Path(config_path)
    lines = [f"  Config file: {p}  ({'exists' if p.exists() else 'does not exist'})"]
    if env_vars:
        parts = [f"{name} ({'set' if os.environ.get(name) else 'unset'})" for name in env_vars]
        lines.append(f"  Env checked: {', '.join(parts)}")
    return "\n".join(lines)


# Per-provider human-readable display name for the suggestion section header
# (e.g. "Provide an OpenAI API key via one of:"). `provider.title()` would
# yield "Openai" / "Perplexity" / "Mock"; this mapping covers branded casing.
_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "openai": "OpenAI",
    "perplexity": "Perplexity",
    "mock": "Mock",
}

# Per-provider example placeholder used in the env-var / CLI-flag / config
# example lines. Mirrors the actual key prefix each provider expects.
_PROVIDER_KEY_PLACEHOLDERS: dict[str, str] = {
    "openai": "sk-...",
    "perplexity": "pplx-...",
    "mock": "mock-...",
}


def format_api_key_error_suggestion(provider: str, config_path: Path | str) -> str:
    """Build the multi-channel APIKeyError suggestion body.

    Enumerates all three input channels (env var, CLI flag, config-file
    TOML), suggests switching to another provider, and shows the status
    of every known provider's env var (not just the failing one). The
    title-line `"{provider} API key not found"` is set by `APIKeyError`
    itself and is preserved verbatim.
    """
    # Local import to avoid circulars (providers/__init__.py imports
    # APIKeyError from this module).
    from doxa_research.providers import PROVIDER_CLI_FLAGS, PROVIDER_ENV_VARS

    display = _PROVIDER_DISPLAY_NAMES.get(provider, provider.title())
    placeholder = _PROVIDER_KEY_PLACEHOLDERS.get(provider, "...")
    env_var = PROVIDER_ENV_VARS.get(provider, f"{provider.upper()}_API_KEY")
    cli_flag = PROVIDER_CLI_FLAGS.get(provider, f"--api-key-{provider}")
    cfg_path = Path(config_path)

    # Pick the first OTHER provider in registry-iteration order for the
    # "switch providers" hint. Deterministic: PROVIDER_ENV_VARS dict order.
    other_provider = next(
        (name for name in PROVIDER_ENV_VARS if name != provider),
        None,
    )

    channels = (
        f"Provide an {display} API key via one of:\n"
        f"  1. Environment variable: export {env_var}={placeholder}\n"
        f"  2. CLI flag:              doxa {cli_flag} {placeholder} <command>\n"
        f"  3. Config file:           Add the following to {cfg_path}:\n"
        f"                              [providers.{provider}]\n"
        f'                              api_key = "{placeholder}"'
    )

    if other_provider is not None:
        switch_hint = (
            f"\n\nAlternatively, switch providers with --provider {other_provider} "
            f"(or another) and\nsupply that provider's key via the same channels."
        )
    else:
        switch_hint = ""

    cfg_status = (
        f"  Config file: {cfg_path}  ({'exists' if cfg_path.exists() else 'does not exist'})"
    )
    env_status_parts = [
        f"{name} ({'set' if os.environ.get(name) else 'unset'})"
        for name in PROVIDER_ENV_VARS.values()
    ]
    env_status = f"  Env vars:    {'  '.join(env_status_parts)}"
    status_block = "\n\nStatus of currently-checked sources:\n" + cfg_status + "\n" + env_status

    return channels + switch_hint + status_block


class DoxaError(Exception):
    """Base exception for Doxa Research errors"""

    def __init__(self, message: str, suggestion: str | None = None, exit_code: int = 1):
        self.message = message
        self.suggestion = suggestion
        self.exit_code = exit_code
        super().__init__(message)


class ModeNotFoundError(DoxaError):
    """A requested mode name is not in the resolvable mode catalog."""

    def __init__(self, mode_name: str, *, available_modes: list[str] | None = None):
        suggestion = None
        if available_modes is not None:
            suggestion = f"Available modes: {', '.join(available_modes)}"
        super().__init__(
            f"Mode {mode_name!r} not found",
            suggestion,
            exit_code=1,
        )


class APIKeyError(DoxaError):
    """Missing or invalid API key"""

    def __init__(self, provider: str):
        from doxa_research import config_legacy
        from doxa_research.paths import user_config_file  # local import to avoid cycles

        cfg_path = user_config_file()
        suggestion = format_api_key_error_suggestion(provider, cfg_path)
        # Look up via module attribute so test monkeypatching of
        # `doxa_research.config_legacy.format_legacy_config_guidance` is honored.
        legacy_guidance = config_legacy.format_legacy_config_guidance()
        if legacy_guidance:
            suggestion = f"{suggestion}\n\n{legacy_guidance}"
        super().__init__(
            f"{provider} API key not found",
            suggestion,
            exit_code=2,
        )


class ProviderError(DoxaError):
    """Provider-specific error with raw error support"""

    def __init__(self, provider: str, message: str, raw_error: str | None = None):
        self.provider = provider
        self.raw_error = raw_error
        super().__init__(
            f"{provider} error: {message}",
            "Check API status or try again later",
            exit_code=3,
        )


class DiskSpaceError(DoxaError):
    """Insufficient disk space"""

    def __init__(self, message: str):
        super().__init__(message, "Free up disk space and try again", exit_code=8)


class APIQuotaError(DoxaError):
    """API quota exceeded"""

    def __init__(self, provider: str):
        super().__init__(
            f"{provider} API quota exceeded",
            "Wait for quota reset or upgrade your plan",
            exit_code=9,
        )


class APIRateLimitError(DoxaError):
    """API rate limit exceeded"""

    def __init__(self, provider: str):
        super().__init__(
            f"{provider} API rate limit exceeded",
            "Wait and retry later, or reduce request frequency.",
            exit_code=10,
        )


class ConfigProfileError(DoxaError):
    """Configuration profile selection or validation failed."""

    def __init__(
        self,
        message: str,
        *,
        available_profiles: list[str] | None = None,
        source: str | None = None,
        suggestion: str | None = None,
    ):
        details = message if source is None else f"{message} (from {source})"
        if suggestion is None:
            suggestion_parts = ["Run `doxa config profiles list` to see available profiles."]
            if available_profiles:
                suggestion_parts.append(f"Available profiles: {', '.join(available_profiles)}.")
            suggestion = " ".join(suggestion_parts)
        super().__init__(
            details,
            suggestion,
            exit_code=1,
        )


class ConfigNotFoundError(DoxaError):
    """Configuration file is required but no canonical file exists."""

    def __init__(self, message: str, suggestion: str | None = None):
        super().__init__(message, suggestion, exit_code=1)


class ConfigAmbiguousError(DoxaError):
    """Both `./doxa.config.toml` and `./.doxa.config.toml` exist."""

    def __init__(self, message: str, suggestion: str | None = None):
        super().__init__(message, suggestion, exit_code=1)


class ModeKindMismatchError(DoxaError):
    """A mode's declared `kind` is incompatible with its model's required kind.

    Raised by `OpenAIProvider._validate_kind_for_model` BEFORE any HTTP call,
    so the user sees a config-edit suggestion instead of a confusing API
    error mid-run. See spec §5.6 + §4 Q1.

    Attributes (for programmatic access):
      * mode_name      — the mode whose `kind` is wrong
      * model          — the model the mode is configured to use
      * declared_kind  — what the mode says (e.g., "immediate")
      * required_kind  — what the model actually requires (e.g., "background")
    """

    def __init__(
        self,
        mode_name: str,
        model: str,
        declared_kind: str,
        required_kind: str,
    ):
        self.mode_name = mode_name
        self.model = model
        self.declared_kind = declared_kind
        self.required_kind = required_kind
        super().__init__(
            (
                f"Mode '{mode_name}' is declared as kind='{declared_kind}', "
                f"but model '{model}' requires kind='{required_kind}'."
            ),
            (
                f"Update [modes.{mode_name}] in your config: set "  # nosec B608 — TOML config-edit suggestion, not SQL
                f"kind = '{required_kind}', or pick a model compatible with "
                f"'{declared_kind}' execution. Run `doxa modes list` to see "
                f"current kinds."
            ),
            exit_code=1,
        )


class BackgroundFlagError(DoxaError):
    """A streaming-only flag was passed to a background-kind mode.

    Standardization #4: stop silently ignoring `--out` and `--append` when
    the user passes them to a background mode. Both flags target the
    immediate streaming path (MultiSink); background runs write files via
    the operation lifecycle and OutputManager, so streaming-only flags are
    meaningless and silently dropping them was a footgun.

    Attributes:
      * flag_name  — the offending CLI flag (e.g. "--out", "--append")
      * mode_name  — the background mode the user invoked
    """

    def __init__(self, flag_name: str, mode_name: str):
        self.flag_name = flag_name
        self.mode_name = mode_name
        super().__init__(
            (
                f"Flag {flag_name} targets immediate-kind streaming runs only; "
                f"mode '{mode_name}' is background."
            ),
            (
                "Background modes write files atomically via the operation "
                "lifecycle, not via streaming sinks. Use --output-dir DIR to "
                "control where files land, or --project NAME for a subdirectory "
                "namespace under base_output_dir. Drop --out / --append for "
                "background modes."
            ),
            exit_code=1,
        )


class CombinedNeedsMultiProviderError(DoxaError):
    """`--combined` was passed but only one provider would run.

    Standardization #4: `--combined` synthesizes one unified report from
    N per-provider outputs. With a single provider there's nothing to
    combine — silently no-op'ing it would hide a user typo or misunderstanding.

    Attributes:
      * mode_name  — the mode the user invoked
      * providers  — the (single-entry) providers list that would have run
    """

    def __init__(self, mode_name: str, providers: list[str]):
        self.mode_name = mode_name
        self.providers = providers
        only = providers[0] if providers else "one provider"
        super().__init__(
            (
                f"--combined needs multiple providers to synthesize from, but "
                f"mode '{mode_name}' would run only {only}."
            ),
            (
                "Use a multi-provider background mode like all_deep_research "
                "(openai + perplexity + gemini fan-out), or drop --combined."
            ),
            exit_code=1,
        )


class ModelOverrideMultiProviderError(DoxaError):
    """`--model NAME` is ambiguous on a multi-provider mode.

    Standardization #5: each provider in a multi-provider mode has its
    own per-provider namespace model (`mode.openai.model`,
    `mode.perplexity.model`, etc.) that wins in the parameter_config.py
    merge order. A single `--model NAME` flag can't sensibly fan out to
    three different upstream model spaces — gpt-4.1-mini doesn't exist
    on Perplexity, sonar doesn't exist on Gemini.

    Attributes:
      * mode_name — the multi-provider mode the user invoked
      * providers — the list that would have run
      * model     — the override the user attempted
    """

    def __init__(self, mode_name: str, providers: list[str], model: str):
        self.mode_name = mode_name
        self.providers = providers
        self.model = model
        super().__init__(
            (
                f"--model '{model}' is ambiguous on multi-provider mode "
                f"'{mode_name}' ({', '.join(providers)} would run)."
            ),
            (
                "Each provider in a multi-provider mode has its own per-provider "
                "model (e.g. openai -> gpt-5.6-sol, perplexity -> "
                "sonar-deep-research, gemini -> deep-research-preview-04-2026). "
                "Narrow to one provider first: --provider PROVIDER --model "
                f"{model}. Or edit the mode's per-provider namespace model "
                "in your config to change defaults persistently."
            ),
            exit_code=1,
        )


class ImmediateMultiProviderError(DoxaError):
    """Preflight: an immediate-kind mode cannot declare multiple providers.

    Doxa has two upstream model spaces:
      * Immediate models — fast, single-provider, streaming output
        (e.g. gpt-4.1-mini, sonar, gemini-2.5-flash-lite). Built-ins:
        `openai_quick`, `perplexity_quick`, `gemini_quick`.
      * Deep Research models — long-running, background, support
        multi-provider fan-out (e.g. gpt-5.6-sol, sonar-deep-research,
        deep-research-preview-04-2026). Built-in fan-out: `all_deep_research`.

    The two families are distinct upstream and cannot be mixed: an immediate
    mode that declares `providers: [a, b, ...]` is structurally invalid.
    Raised at preflight before any provider is instantiated.

    Attributes:
      * mode_name — the offending mode
      * providers — the multi-provider list that triggered the error
    """

    def __init__(self, mode_name: str, providers: list[str]):
        self.mode_name = mode_name
        self.providers = providers
        super().__init__(
            (
                f"Mode '{mode_name}' is kind='immediate' but declares "
                f"{len(providers)} providers ({', '.join(providers)}). "
                f"Immediate modes are single-provider by design."
            ),
            (
                "Doxa has two separate upstream model spaces:\n"
                "  - Immediate models: fast, single-provider, streaming. "
                "Built-ins: openai_quick, perplexity_quick, gemini_quick.\n"
                "  - Deep Research models: long-running, background, "
                "support multi-provider fan-out. Built-in: all_deep_research "
                "(openai -> gpt-5.6-sol, perplexity -> sonar-deep-research, "
                "gemini -> deep-research-preview-04-2026).\n"
                "To fan out across all three providers, use "
                "`--mode all_deep_research`. To call one provider quickly, "
                "use `--mode openai_quick` (or perplexity_quick / gemini_quick)."
            ),
            exit_code=1,
        )
