"""Normalize provider runtime configuration into stable parameter groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALL_PROVIDER_COMMON_REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "temperature",
        "top_p",
        "max_output_tokens",
        "response_format",
        "system_prompt",
    }
)

MODE_COMMON_REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "temperature",
        "top_p",
        "max_output_tokens",
        "stop_sequences",
        "response_format",
        "system_prompt",
    }
)
PROVIDER_COMMON_REQUEST_KEYS: frozenset[str] = MODE_COMMON_REQUEST_KEYS
LEGACY_PROVIDER_NAMESPACE_COMMON_KEYS: frozenset[str] = frozenset(
    {
        "temperature",
        "top_p",
        "max_output_tokens",
        "stop_sequences",
        "response_format",
    }
)
LEGACY_FLAT_COMMON_KEYS: frozenset[str] = PROVIDER_COMMON_REQUEST_KEYS - {"system_prompt"}

CLIENT_KEYS: frozenset[str] = frozenset({"timeout", "base_url", "organization"})
ALL_PROVIDER_CLIENT_KEYS: frozenset[str] = frozenset({"timeout"})
AUTH_KEYS: frozenset[str] = frozenset({"api_key"})
ROUTING_KEYS: frozenset[str] = frozenset({"kind"})
NO_KEYS: frozenset[str] = frozenset()
PROVIDER_NATIVE_REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "code_interpreter",
        "frequency_penalty",
        "include_thoughts",
        "max_tokens",
        "max_tool_calls",
        "n",
        "presence_penalty",
        "reasoning",
        "reasoning_effort",
        "reasoning_summary",
        "response_json_schema",
        "response_mime_type",
        "response_schema",
        "safety_settings",
        "search_context_size",
        "search_domain_filter",
        "seed",
        "stop",
        "stream_mode",
        "thinking_budget",
        "tool_choice",
        "tools",
        "top_k",
        "web_search",
        "web_search_options",
    }
)
LEGACY_MODE_PROVIDER_NATIVE_KEYS: frozenset[str] = frozenset({"max_tokens", "max_tool_calls"})
FRAMEWORK_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "providers",
        "description",
        "previous",
        "next",
        "auto_input",
        "parallel",
        "stream",
        "background",
        # P28: Gemini DR polling tunables (forward-compat schema; runtime
        # wiring lands in v1.1 — see GeminiConfig docstring). These are
        # framework-level, NOT native Gemini API params, so they must NOT
        # flow into provider_request bucket.
        "poll_interval",
        "max_wait_minutes",
    }
)

_KNOWN_PROVIDER_NAMES: frozenset[str] = frozenset({"openai", "perplexity", "gemini", "mock"})


@dataclass(slots=True)
class ProviderRuntimeConfig:
    provider_name: str
    auth: dict[str, Any] = field(default_factory=dict)
    client: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    framework: dict[str, Any] = field(default_factory=dict)
    common_request: dict[str, Any] = field(default_factory=dict)
    provider_request: dict[str, Any] = field(default_factory=dict)
    extension_bags: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def to_legacy_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {}
        config.update(self.auth)
        config.update(self.client)
        config.update(self.routing)
        config.update(self.framework)
        config.update(
            {
                key: value
                for key, value in self.common_request.items()
                if key in LEGACY_FLAT_COMMON_KEYS
            }
        )

        existing_namespace = config.get(self.provider_name)
        provider_namespace = existing_namespace if isinstance(existing_namespace, dict) else {}
        common_namespace = {
            key: value
            for key, value in self.common_request.items()
            if key in LEGACY_PROVIDER_NAMESPACE_COMMON_KEYS
        }
        provider_namespace = _deep_merge(provider_namespace, common_namespace)
        provider_namespace = _deep_merge(provider_namespace, self.provider_request)
        provider_extensions = self.extension_bags.get(self.provider_name)
        if provider_extensions:
            provider_namespace = _deep_merge(provider_namespace, provider_extensions)
        if provider_namespace:
            config[self.provider_name] = provider_namespace
        if "model" in provider_namespace:
            config["model"] = provider_namespace["model"]

        return config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _merge_value(target: dict[str, Any], key: str, value: Any) -> None:
    existing = target.get(key)
    if isinstance(existing, dict) and isinstance(value, dict):
        target[key] = _deep_merge(existing, value)
    else:
        target[key] = value


def _record_source(
    runtime: ProviderRuntimeConfig,
    group: str,
    key: str,
    source: str,
) -> None:
    runtime.sources[f"{group}.{key}"] = source


def _apply_general_layer(
    runtime: ProviderRuntimeConfig,
    layer: dict[str, Any] | None,
    *,
    source: str,
    common_keys: frozenset[str],
    auth_keys: frozenset[str] = AUTH_KEYS,
    client_keys: frozenset[str] = CLIENT_KEYS,
    provider_request_keys: frozenset[str] = PROVIDER_NATIVE_REQUEST_KEYS,
    unknown_to_provider_request: bool = True,
    reject_unknown: bool = False,
) -> None:
    if not isinstance(layer, dict):
        return

    for key, value in layer.items():
        if key in auth_keys:
            _merge_value(runtime.auth, key, value)
            _record_source(runtime, "auth", key, source)
        elif key in client_keys:
            _merge_value(runtime.client, key, value)
            _record_source(runtime, "client", key, source)
        elif key in ROUTING_KEYS:
            _merge_value(runtime.routing, key, value)
            _record_source(runtime, "routing", key, source)
        elif key in common_keys:
            _merge_value(runtime.common_request, key, value)
            _record_source(runtime, "common_request", key, source)
        elif key in FRAMEWORK_KEYS or key in _KNOWN_PROVIDER_NAMES:
            _merge_value(runtime.framework, key, value)
            _record_source(runtime, "framework", key, source)
        elif key in provider_request_keys:
            _merge_value(runtime.provider_request, key, value)
            _record_source(runtime, "provider_request", key, source)
        elif unknown_to_provider_request:
            _merge_value(runtime.provider_request, key, value)
            _record_source(runtime, "provider_request", key, source)
        elif reject_unknown:
            raise ValueError(f"Unsupported provider parameter: {source}.{key}")


def _apply_mode_generic_layer(
    runtime: ProviderRuntimeConfig,
    layer: dict[str, Any] | None,
    *,
    source: str,
) -> None:
    if not isinstance(layer, dict):
        return

    generic_layer = {key: value for key, value in layer.items() if key not in _KNOWN_PROVIDER_NAMES}
    _apply_general_layer(
        runtime,
        generic_layer,
        source=source,
        common_keys=MODE_COMMON_REQUEST_KEYS,
        auth_keys=NO_KEYS,
        client_keys=NO_KEYS,
        provider_request_keys=LEGACY_MODE_PROVIDER_NATIVE_KEYS,
        unknown_to_provider_request=False,
    )


def _apply_provider_namespace_layer(
    runtime: ProviderRuntimeConfig,
    layer: dict[str, Any] | None,
    *,
    source: str,
) -> None:
    if not isinstance(layer, dict):
        return

    provider_extensions = runtime.extension_bags.setdefault(runtime.provider_name, {})
    for key, value in layer.items():
        if key in ROUTING_KEYS:
            _merge_value(runtime.routing, key, value)
            _record_source(runtime, "routing", key, source)
        elif key == "extra_body":
            _merge_value(provider_extensions, key, value)
            _record_source(runtime, "extension_bags", key, source)
        elif key == "model":
            _merge_value(runtime.provider_request, key, value)
            _record_source(runtime, "provider_request", key, source)
        elif key in PROVIDER_COMMON_REQUEST_KEYS:
            _merge_value(runtime.common_request, key, value)
            _record_source(runtime, "common_request", key, source)
        elif key in PROVIDER_NATIVE_REQUEST_KEYS:
            _merge_value(runtime.provider_request, key, value)
            _record_source(runtime, "provider_request", key, source)
        else:
            raise ValueError(f"Unsupported provider parameter: {source}.{key}")

    if not provider_extensions:
        runtime.extension_bags.pop(runtime.provider_name, None)


def _provider_namespace(layer: dict[str, Any] | None, provider_name: str) -> dict[str, Any] | None:
    if not isinstance(layer, dict):
        return None
    namespace = layer.get(provider_name)
    return namespace if isinstance(namespace, dict) else None


def build_provider_runtime_config(
    *,
    provider_name: str,
    config: Any,
    mode_config: dict[str, Any] | None,
    timeout_override: float | None,
) -> ProviderRuntimeConfig:
    # ConfigManager applies the selected profile as a normal config layer before
    # provider normalization. mode_config is likewise expected to be the resolved
    # mode config from ConfigManager.get_mode_config().
    config_data = getattr(config, "data", {})
    if not isinstance(config_data, dict):
        config_data = {}

    runtime = ProviderRuntimeConfig(provider_name=provider_name)

    providers = config_data.get("providers")
    providers = providers if isinstance(providers, dict) else {}
    _apply_general_layer(
        runtime,
        providers.get("defaults"),
        source="providers.defaults",
        common_keys=ALL_PROVIDER_COMMON_REQUEST_KEYS,
        auth_keys=NO_KEYS,
        client_keys=ALL_PROVIDER_CLIENT_KEYS,
        provider_request_keys=NO_KEYS,
        unknown_to_provider_request=False,
        reject_unknown=True,
    )
    _apply_general_layer(
        runtime,
        providers.get(provider_name),
        source=f"providers.{provider_name}",
        common_keys=PROVIDER_COMMON_REQUEST_KEYS,
        unknown_to_provider_request=False,
        reject_unknown=True,
    )

    _apply_mode_generic_layer(runtime, mode_config, source="mode_config")
    _apply_provider_namespace_layer(
        runtime,
        _provider_namespace(mode_config, provider_name),
        source=f"mode_config.{provider_name}",
    )

    if timeout_override is not None:
        _merge_value(runtime.client, "timeout", timeout_override)
        _record_source(runtime, "client", "timeout", "timeout_override")

    return runtime
