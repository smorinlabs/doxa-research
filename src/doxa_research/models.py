"""Core data structures used by Doxa Research.

- InputMode: interactive prompt mode enum
- OperationStatus: canonical research-operation state record + validated transitions
- InteractiveInitialSettings: CLI-arg → interactive-mode defaults bundle
- ModelCache: per-provider disk cache for model-list responses
- VALID_OPERATION_STATES / VALID_STATE_TRANSITIONS: state-machine constants
- ModelSpec / KNOWN_MODELS: P18 registry derived from BUILTIN_MODES
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

from doxa_research.paths import user_model_cache_dir


class ModelSpec(NamedTuple):
    """A (provider, model, kind) triple. The canonical "this model exists" record.

    Built once at import time by `derive_known_models()` from `BUILTIN_MODES`.
    Models referenced only by user-defined modes are not in `KNOWN_MODELS` —
    user opted in, user owns kind correctness.
    """

    id: str
    provider: str
    kind: Literal["immediate", "background"]


def derive_known_models(builtin_modes: dict[str, dict[str, Any]] | None = None) -> list[ModelSpec]:
    """Build the canonical model registry from `BUILTIN_MODES`.

    Every `(provider, model)` pair referenced by any non-alias builtin is a
    "known" model. Cross-mode kind conflicts (same `(provider, model)` declared
    with two different kinds across builtins) raise `DoxaError` — that
    indicates a doxa-research bug, not a user-config issue. Explicitly registered
    OpenAI background-capable models that also support streaming are the
    exception: retain their background discovery default, not the mode kind.

    Alias stubs (entries carrying `_deprecated_alias_for`) are skipped because
    they delegate to a real mode and don't carry provider/model/kind themselves.

    Pass `builtin_modes` to test against a synthetic catalog; production code
    calls with no argument to read the real `BUILTIN_MODES`.
    """
    # Lazy import to dodge the config.py ↔ models.py circular at module load.
    from doxa_research.config import (
        BACKGROUND_MODELS,
        BUILTIN_MODES,
        requires_background_submission,
    )
    from doxa_research.errors import DoxaError

    modes = builtin_modes if builtin_modes is not None else BUILTIN_MODES
    seen: dict[tuple[str, str], ModelSpec] = {}
    for mode_name, cfg in modes.items():
        if "_deprecated_alias_for" in cfg:
            continue
        # Multi-provider fan-out modes (e.g. all_deep_research) declare a
        # `providers` list plus per-provider namespace models instead of a
        # singular provider/model pair. They are dispatch constructs, not
        # (provider, model) pairs, so skip them in KNOWN_MODELS.
        if cfg.get("provider") is None and isinstance(cfg.get("providers"), list):
            continue
        provider_raw = cfg.get("provider")
        model_raw = cfg.get("model")
        kind_raw = cfg.get("kind")
        if (
            not isinstance(provider_raw, str)
            or not isinstance(model_raw, str)
            or kind_raw not in ("immediate", "background")
        ):
            raise DoxaError(
                f"Builtin mode '{mode_name}' is missing one of provider/model/kind, "
                f"or kind is not 'immediate'/'background'",
                "This is a doxa-research bug — every non-alias builtin must declare all three fields.",
            )
        provider: str = provider_raw
        model: str = model_raw
        # ty cannot narrow `dict.get` returns through tuple-membership; the
        # `not in (...)` guard above already excludes everything else.
        kind = cast(Literal["immediate", "background"], kind_raw)
        # A general-purpose model may back both immediate and research modes.
        # Registry kind is its discovery default; each mode retains its own kind.
        if (
            provider == "openai"
            and model in BACKGROUND_MODELS
            and not requires_background_submission(model)
        ):
            kind = "background"
        key = (provider, model)
        if key in seen and seen[key].kind != kind:
            raise DoxaError(
                f"Inconsistent kind for {provider}/{model}: "
                f"declared as both '{seen[key].kind}' and '{kind}' across builtin modes "
                f"(latest conflict at mode '{mode_name}')",
                "Pick one kind for this (provider, model) pair and update the conflicting builtin.",
            )
        seen[key] = ModelSpec(id=model, provider=provider, kind=kind)
    return list(seen.values())


class InputMode(Enum):
    """Interactive input mode states"""

    EDIT_MODE = "edit"
    CLARIFICATION_MODE = "clarification"


# Valid operation states
VALID_OPERATION_STATES = {"queued", "running", "completed", "failed", "cancelled"}

# Valid state transitions: source → allowed targets
VALID_STATE_TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),  # terminal state
    "failed": {"running"},  # resumable when failure_type is "recoverable"
    "cancelled": {"running"},  # resumable after Ctrl-C
}


@dataclass
class OperationStatus:
    """Status of a research operation"""

    id: str
    prompt: str
    mode: str
    status: str  # "queued", "running", "completed", "failed", "cancelled"
    created_at: datetime
    updated_at: datetime
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    output_paths: dict[str, Path] = field(default_factory=dict)
    error: str | None = None
    progress: float = 0.0  # 0.0 to 1.0
    project: str | None = None
    input_files: list[Path] = field(default_factory=list)
    failure_type: str | None = None  # "recoverable" | "permanent" | None

    def transition_to(self, new_status: str, error: str | None = None) -> None:
        """Transition to a new status with validation.

        Validates that the transition is allowed per the state machine.
        Updates updated_at timestamp automatically.
        """
        if new_status not in VALID_OPERATION_STATES:
            raise ValueError(
                f"Invalid operation status: '{new_status}'. Valid states: {VALID_OPERATION_STATES}"
            )
        allowed = VALID_STATE_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid state transition: '{self.status}' → '{new_status}'. "
                f"Allowed transitions from '{self.status}': {allowed}"
            )
        self.status = new_status
        self.updated_at = datetime.now()
        if error is not None:
            self.error = error


@dataclass
class InteractiveInitialSettings:
    """Initial settings for interactive mode from command-line arguments"""

    mode: str | None = None
    provider: str | None = None
    prompt: str | None = None
    async_mode: bool = False
    cli_api_keys: dict[str, str | None] | None = None
    clarify_mode: bool = False  # Start in clarification mode if True


class ModelCache:
    """Manages cached model lists for providers"""

    def __init__(self, provider_name: str, cache_dir: Path | None = None):
        """Initialize model cache for a specific provider

        Args:
            provider_name: Name of the provider (openai, perplexity, etc.)
            cache_dir: Optional cache directory, defaults to ~/.doxa_research/model_cache/
        """
        self.provider_name = provider_name
        if cache_dir:
            self.cache_dir = cache_dir
        else:
            self.cache_dir = user_model_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / f"{provider_name}_models.json"
        self.cache_max_age_days = 7  # Cache expires after 1 week

    def get_cache_path(self) -> Path:
        """Get the cache file path for this provider"""
        return self.cache_file

    def is_cache_valid(self, force_refresh: bool = False) -> bool:
        """Check if cache exists and is still valid

        Args:
            force_refresh: If True, cache is always considered invalid

        Returns:
            True if cache is valid and can be used, False otherwise
        """
        if force_refresh:
            return False

        if not self.cache_file.exists():
            return False

        try:
            with open(self.cache_file) as f:
                cache_data = json.load(f)

            cached_at = datetime.fromisoformat(cache_data.get("cached_at", ""))
            age = datetime.now() - cached_at

            return age.days < self.cache_max_age_days
        except (json.JSONDecodeError, ValueError, KeyError):
            # Cache file is corrupted or invalid
            return False

    def load_cache(self) -> list[dict[str, Any]] | None:
        """Load models from cache if valid

        Returns:
            List of model dictionaries if cache is valid, None otherwise
        """
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file) as f:
                cache_data = json.load(f)
            return cache_data.get("models", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return None

    def save_cache(self, models: list[dict[str, Any]]) -> None:
        """Save models to cache with timestamp

        Args:
            models: List of model dictionaries to cache
        """
        cache_data = {
            "provider": self.provider_name,
            "cached_at": datetime.now().isoformat(),
            "models": models,
        }

        # Save atomically with temp file
        temp_file = self.cache_file.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(cache_data, f, indent=2)
        temp_file.replace(self.cache_file)

    def clear_cache(self) -> None:
        """Remove the cache file for this provider"""
        if self.cache_file.exists():
            self.cache_file.unlink()

    def get_cache_age(self) -> timedelta | None:
        """Get the age of the cache

        Returns:
            timedelta object representing cache age, or None if cache doesn't exist
        """
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file) as f:
                cache_data = json.load(f)
            cached_at = datetime.fromisoformat(cache_data.get("cached_at", ""))
            return datetime.now() - cached_at
        except (json.JSONDecodeError, ValueError, KeyError):
            return None


# Module-level registry — built once at import. Sits at the end of the module
# so `derive_known_models()` is fully defined before it runs. The function does
# its own lazy import of `BUILTIN_MODES` so this top-level call is safe even if
# models.py is imported before config.py finishes initializing in some path.
KNOWN_MODELS: list[ModelSpec] = derive_known_models()
