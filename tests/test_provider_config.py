"""OpenAI provider config → request payload tests.

Migrated from doxa_test GAP01-01…03.
"""

from __future__ import annotations

import asyncio
import types
import warnings
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from doxa_research.errors import DoxaError
from doxa_research.providers.openai import OpenAIProvider


@pytest.fixture(autouse=True)
def _isolate_config(isolated_doxa_home: Path) -> Path:
    return isolated_doxa_home


def test_max_tool_calls_reaches_request_payload() -> None:
    """GAP01-01: max_tool_calls from provider config reaches the Responses API payload."""
    captured: dict[str, Any] = {}

    async def fake_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(id="job-gap01-1")

    provider = OpenAIProvider(
        api_key="dummy",
        config={"model": "o3-deep-research", "openai": {"max_tool_calls": 80}},
    )
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(create=fake_create))
    )
    asyncio.run(provider.submit("test prompt", mode="deep_research"))
    assert "max_tool_calls" in captured, (
        f"max_tool_calls missing from request payload: {list(captured.keys())}"
    )
    assert captured["max_tool_calls"] == 80, (
        f"expected max_tool_calls=80, got {captured['max_tool_calls']!r}"
    )


def test_code_interpreter_false_excludes_tool() -> None:
    """GAP01-02: code_interpreter=False in config excludes the tool from the request."""
    captured: dict[str, Any] = {}

    async def fake_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(id="job-gap01-2")

    provider = OpenAIProvider(
        api_key="dummy",
        config={"model": "o3-deep-research", "openai": {"code_interpreter": False}},
    )
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(create=fake_create))
    )
    asyncio.run(provider.submit("test prompt", mode="deep_research"))
    tool_types = [t.get("type") for t in captured.get("tools", [])]
    assert "code_interpreter" not in tool_types, (
        f"code_interpreter should be absent when config sets it False, got tools: {tool_types}"
    )
    assert "web_search" in tool_types, f"web_search must still be present, got tools: {tool_types}"


def test_default_config_includes_code_interpreter_and_omits_max_tool_calls() -> None:
    """GAP01-03: default config — no max_tool_calls key, code_interpreter included."""
    captured: dict[str, Any] = {}

    async def fake_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(id="job-gap01-3")

    provider = OpenAIProvider(api_key="dummy", config={"model": "o3-deep-research"})
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(create=fake_create))
    )
    asyncio.run(provider.submit("test prompt", mode="deep_research"))
    assert "max_tool_calls" not in captured, (
        f"max_tool_calls should be absent when not configured, got: {captured.get('max_tool_calls')}"
    )
    tools = captured.get("tools", [])
    code_interp_tool = next((t for t in tools if t.get("type") == "code_interpreter"), None)
    assert code_interp_tool is not None, (
        f"code_interpreter must be included by default, got tools: {[t.get('type') for t in tools]}"
    )
    assert code_interp_tool.get("container") == {"type": "auto"}, (
        f"code_interpreter tool must carry container={{type: auto}} "
        f"(OpenAI API requirement), got: {code_interp_tool}"
    )


def test_create_provider_sets_background_for_deep_research_model() -> None:
    """Regression: create_provider must set background=True when the mode
    pins a deep-research model. Previously only covered end-to-end via
    test_oai_background.py — this pins the contract at the factory."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(
            data={"providers": {"openai": {"api_key": "sk-test-deep-research-factory"}}}
        ),
    )
    mode_config: dict[str, Any] = {"model": "o3-deep-research"}

    provider = create_provider(
        "openai",
        config,
        mode_config=mode_config,
    )
    # provider.config is the mutated provider_config dict passed to the constructor;
    # background=True is set when is_background_mode(provider_config) is True.
    assert provider.config.get("background") is True


def test_create_provider_no_background_for_plain_model() -> None:
    """Inverse: a plain (non-deep-research) model does NOT get background=True."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"openai": {"api_key": "sk-test-plain-factory"}}}),
    )
    mode_config: dict[str, Any] = {"model": "o3"}

    provider = create_provider(
        "openai",
        config,
        mode_config=mode_config,
    )
    assert provider.config.get("background", False) is False


# ---------------------------------------------------------------------------
# P23-TS01 — `--model` passthrough through create_provider.
# P23-RS01 — provider-specific request settings from mode config must reach
# provider constructors, including nested Perplexity extra_body settings.
# ---------------------------------------------------------------------------


def test_create_provider_passes_perplexity_model_from_mode_config() -> None:
    """P23-TS01: mode-config model passes through to PerplexityProvider."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"perplexity": {"api_key": "pplx-test"}}}),
    )
    mode_config: dict[str, Any] = {"model": "sonar-pro", "kind": "immediate"}

    provider = create_provider("perplexity", config, mode_config=mode_config)
    assert provider.config.get("model") == "sonar-pro"


def test_create_provider_perplexity_default_model_is_sonar() -> None:
    """P23-TS01: PerplexityProvider defaults to `sonar` when no model is configured.

    Plan-pinned default; previous stub used `sonar-pro`.
    """
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider
    from doxa_research.providers.perplexity import PerplexityProvider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"perplexity": {"api_key": "pplx-test"}}}),
    )

    provider = create_provider("perplexity", config)
    assert isinstance(provider, PerplexityProvider)
    assert provider.model == "sonar"


def test_create_provider_perplexity_passes_arbitrary_model_string() -> None:
    """P23-TS01: no local provider/model compatibility validation.

    A model string doxa_research has never seen passes through unchanged; any
    invalid-model error is surfaced from the provider/API layer.
    """
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"perplexity": {"api_key": "pplx-test"}}}),
    )
    mode_config: dict[str, Any] = {
        "model": "future-sonar-2027-preview",
        "kind": "immediate",
    }

    provider = create_provider("perplexity", config, mode_config=mode_config)
    assert provider.config.get("model") == "future-sonar-2027-preview"


def test_create_provider_passes_perplexity_namespace_from_mode_config() -> None:
    """P23-RS01: mode_config['perplexity'] reaches PerplexityProvider.config."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"perplexity": {"api_key": "pplx-test"}}}),
    )
    mode_config: dict[str, Any] = {
        "provider": "perplexity",
        "model": "sonar",
        "kind": "immediate",
        "perplexity": {
            "web_search_options": {"search_context_size": "low"},
            "stream_mode": "full",
            "search_domain_filter": ["perplexity.ai"],
        },
    }

    provider = create_provider("perplexity", config, mode_config=mode_config)

    assert provider.config["perplexity"] == {
        "web_search_options": {"search_context_size": "low"},
        "stream_mode": "full",
        "search_domain_filter": ["perplexity.ai"],
    }


def test_create_provider_passes_openai_request_settings_from_mode_config() -> None:
    """OpenAI common and provider-namespaced mode settings reach config."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"openai": {"api_key": "sk-test"}}}),
    )
    mode_config: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "kind": "immediate",
        "temperature": 0.2,
        "openai": {"max_tool_calls": 12},
        "system_prompt": "not provider config",
    }

    provider = create_provider("openai", config, mode_config=mode_config)

    assert provider.config["temperature"] == 0.2
    assert provider.config["openai"]["temperature"] == 0.2
    assert provider.config["openai"]["max_tool_calls"] == 12
    assert "max_tool_calls" not in provider.config
    assert "system_prompt" not in provider.config


def test_create_provider_preserves_legacy_flat_openai_max_tool_calls() -> None:
    """Historical flat mode max_tool_calls still reaches OpenAI without warning."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"openai": {"api_key": "sk-test"}}}),
    )
    mode_config: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "kind": "immediate",
        "max_tool_calls": 12,
    }

    provider = create_provider("openai", config, mode_config=mode_config)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = cast(OpenAIProvider, provider)._resolve_provider_config_value("max_tool_calls")

    assert resolved == 12
    assert provider.config["openai"]["max_tool_calls"] == 12
    assert "max_tool_calls" not in provider.config
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]


def test_create_provider_preserves_perplexity_extra_body_extension_bag() -> None:
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"perplexity": {"api_key": "pplx-test"}}}),
    )
    mode_config: dict[str, Any] = {
        "provider": "perplexity",
        "model": "sonar",
        "kind": "immediate",
        "perplexity": {"extra_body": {"future_sdk_option": True}},
    }

    provider = create_provider("perplexity", config, mode_config=mode_config)
    request_params = cast(Any, provider)._build_request_params("prompt", None)

    assert provider.config["perplexity"]["extra_body"]["future_sdk_option"] is True
    assert request_params["extra_body"]["future_sdk_option"] is True


# ---------------------------------------------------------------------------
# P24 Task 3.1 — [modes.X.openai] namespace migration with backwards-compat
# deprecation. Mirrors P23/Perplexity's [modes.X.perplexity] namespace pattern.
# ---------------------------------------------------------------------------


def test_openai_reads_namespaced_temperature() -> None:
    """OpenAIProvider reads [modes.X.openai].temperature."""
    provider = OpenAIProvider(
        api_key="dummy",
        config={"openai": {"temperature": 0.42}, "kind": "immediate"},
    )
    assert provider._resolve_provider_config_value("temperature", 0.7) == 0.42


def test_openai_reads_flat_temperature_with_deprecation_warning() -> None:
    """Flat top-level temperature still works but emits DeprecationWarning."""
    provider = OpenAIProvider(
        api_key="dummy",
        config={"temperature": 0.42, "kind": "immediate"},
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = provider._resolve_provider_config_value("temperature", 0.7)

    assert resolved == 0.42
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any(
        "namespace" in str(w.message).lower()
        or "flat config" in str(w.message).lower()
        or "modes." in str(w.message)
        for w in dep_warnings
    ), "expected DeprecationWarning advising migration to [modes.X.openai] namespace"


def test_openai_namespaced_overrides_flat_silently() -> None:
    """When both namespaced and flat keys exist, namespaced wins. No deprecation."""
    provider = OpenAIProvider(
        api_key="dummy",
        config={
            "temperature": 0.1,
            "openai": {"temperature": 0.9},
            "kind": "immediate",
        },
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = provider._resolve_provider_config_value("temperature", 0.7)

    assert resolved == 0.9
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not dep_warnings, "DeprecationWarning fired even though user is on the namespace path"


def test_openai_default_when_neither_present() -> None:
    """Returns the default when neither namespaced nor flat key is set."""
    provider = OpenAIProvider(api_key="dummy", config={"kind": "immediate"})
    assert provider._resolve_provider_config_value("temperature", 0.7) == 0.7
    assert provider._resolve_provider_config_value("max_tool_calls", None) is None


class _OpenAIEmptyStreamCM:
    """Async context manager that yields no upstream events."""

    async def __aenter__(self) -> _OpenAIEmptyStreamCM:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def __aiter__(self) -> _OpenAIEmptyStreamCM:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


def _capture_openai_stream_request(provider: OpenAIProvider) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_stream(**kwargs: Any) -> _OpenAIEmptyStreamCM:
        captured.update(kwargs)
        return _OpenAIEmptyStreamCM()

    async def drive() -> list[Any]:
        return [event async for event in provider.stream("hi", mode="openai_reasoning")]

    with patch.object(provider.client.responses, "stream", new=fake_stream):
        asyncio.run(drive())
    return captured


def test_openai_reasoning_builtin_mode_enables_reasoning_and_web_search() -> None:
    """Built-in OpenAI reasoning mode opts into reasoning summaries + web search."""
    from doxa_research.config import BUILTIN_MODES

    mode = BUILTIN_MODES["openai_reasoning"]
    assert mode["provider"] == "openai"
    assert mode["model"] == "o3"
    assert mode["kind"] == "immediate"
    assert mode["openai"] == {"reasoning_summary": "auto", "web_search": True}


def test_openai_stream_namespaced_reasoning_summary_reaches_request() -> None:
    """[modes.X.openai].reasoning_summary enables Responses stream reasoning."""
    provider = OpenAIProvider(
        api_key="dummy",
        config={
            "model": "o3",
            "kind": "immediate",
            "openai": {"reasoning_summary": "auto"},
        },
    )

    captured = _capture_openai_stream_request(provider)

    assert captured["reasoning"] == {"summary": "auto"}


def test_openai_stream_web_search_true_reaches_request_tools() -> None:
    """[modes.X.openai].web_search=true opts immediate streaming into web search."""
    provider = OpenAIProvider(
        api_key="dummy",
        config={
            "model": "o3",
            "kind": "immediate",
            "openai": {"web_search": True},
        },
    )

    captured = _capture_openai_stream_request(provider)

    assert captured["tools"] == [{"type": "web_search"}]


def test_openai_stream_web_search_false_omits_request_tools() -> None:
    """[modes.X.openai].web_search=false leaves immediate streaming ungrounded."""
    provider = OpenAIProvider(
        api_key="dummy",
        config={
            "model": "o3",
            "kind": "immediate",
            "openai": {"reasoning_summary": "auto", "web_search": False},
        },
    )

    captured = _capture_openai_stream_request(provider)

    assert captured["reasoning"] == {"summary": "auto"}
    assert "tools" not in captured


# ---------------------------------------------------------------------------
# P24 Task 5.1 — Gemini provider registry + CLI plumbing surface tests.
# Mirrors P23 Perplexity precedent.
# ---------------------------------------------------------------------------


def test_create_provider_returns_gemini_when_provider_is_gemini() -> None:
    """P24-T07: create_provider('gemini', ...) returns a GeminiProvider instance."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider
    from doxa_research.providers.gemini import GeminiProvider

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"gemini": {"api_key": "AIza-test"}}}),
    )

    with patch("google.genai.Client", return_value=mock_client):
        provider = create_provider("gemini", config)

    assert isinstance(provider, GeminiProvider)


def test_provider_env_vars_includes_gemini() -> None:
    """P24-T07: PROVIDER_ENV_VARS['gemini'] = 'GEMINI_API_KEY'."""
    from doxa_research.providers import PROVIDER_ENV_VARS

    assert PROVIDER_ENV_VARS.get("gemini") == "GEMINI_API_KEY"


def test_providers_dict_includes_gemini() -> None:
    """P24-T07: the PROVIDERS dict registers GeminiProvider under 'gemini' key."""
    from doxa_research.providers import PROVIDERS
    from doxa_research.providers.gemini import GeminiProvider

    assert PROVIDERS.get("gemini") is GeminiProvider


def test_provider_cli_flags_includes_gemini() -> None:
    """P24-T07: PROVIDER_CLI_FLAGS['gemini'] = '--api-key-gemini'."""
    from doxa_research.providers import PROVIDER_CLI_FLAGS

    assert PROVIDER_CLI_FLAGS.get("gemini") == "--api-key-gemini"


def test_create_provider_passes_gemini_model_from_mode_config() -> None:
    """P24-T07: mode-config model passes through to GeminiProvider."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"gemini": {"api_key": "AIza-test"}}}),
    )
    mode_config: dict[str, Any] = {"model": "gemini-2.5-pro", "kind": "immediate"}

    with patch("google.genai.Client", return_value=mock_client):
        provider = create_provider("gemini", config, mode_config=mode_config)
    assert provider.config.get("model") == "gemini-2.5-pro"


def test_create_provider_passes_gemini_namespace_from_mode_config() -> None:
    """P24-T07: mode_config['gemini'] reaches GeminiProvider.config."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"gemini": {"api_key": "AIza-test"}}}),
    )
    mode_config: dict[str, Any] = {
        "provider": "gemini",
        "model": "gemini-2.5-flash-lite",
        "kind": "immediate",
        "gemini": {
            "tools": ["google_search"],
            "thinking_budget": 0,
        },
    }

    with patch("google.genai.Client", return_value=mock_client):
        provider = create_provider("gemini", config, mode_config=mode_config)

    assert provider.config["gemini"] == {
        "tools": ["google_search"],
        "thinking_budget": 0,
    }


# ---------------------------------------------------------------------------
# Provider parameter normalizer through-path coverage.
#
# Values under [providers.defaults] and [providers.X] should flow to provider
# constructors as global defaults. Mode-level provider namespaces retain
# higher precedence, and OpenAI must not treat normalized root/provider
# defaults as deprecated flat mode passthrough.
# ---------------------------------------------------------------------------


def test_providers_defaults_temperature_flows_to_openai_without_deprecation() -> None:
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(
            data={
                "providers": {
                    "defaults": {"temperature": 0.3},
                    "openai": {"api_key": "sk-test"},
                }
            }
        ),
    )

    provider = create_provider("openai", config)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = cast(OpenAIProvider, provider)._resolve_provider_config_value("temperature", 0.7)

    assert resolved == 0.3
    assert cast(OpenAIProvider, provider).config["openai"]["temperature"] == 0.3
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not dep_warnings


def test_providers_defaults_temperature_flows_to_perplexity_and_gemini() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(
            data={
                "providers": {
                    "defaults": {"temperature": 0.31},
                    "perplexity": {"api_key": "pplx-test"},
                    "gemini": {"api_key": "AIza-test"},
                }
            }
        ),
    )

    perplexity_provider = create_provider("perplexity", config)
    assert perplexity_provider.config["temperature"] == 0.31
    assert perplexity_provider.config["perplexity"]["temperature"] == 0.31

    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    with patch("google.genai.Client", return_value=mock_client):
        gemini_provider = create_provider("gemini", config)
    assert gemini_provider.config["temperature"] == 0.31
    assert gemini_provider.config["gemini"]["temperature"] == 0.31


def test_root_providers_namespace_temperature_flows_to_openai_provider() -> None:
    """[providers.openai].temperature flows to OpenAIProvider as a default.

    Desired: when no [modes.X.openai].temperature is set, the value from
    [providers.openai].temperature is used as a global default — without
    emitting a DeprecationWarning advising migration to a mode-level key
    (the user is *intentionally* setting a global default).
    """
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"openai": {"api_key": "sk-test", "temperature": 0.3}}}),
    )

    provider = create_provider("openai", config)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = cast(OpenAIProvider, provider)._resolve_provider_config_value("temperature", 0.7)

    assert resolved == 0.3
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not dep_warnings, (
        "Reading a value sourced from [providers.openai] should NOT emit the "
        "[modes.X.openai] migration DeprecationWarning."
    )


def test_mode_level_openai_temperature_overrides_root_providers_default() -> None:
    """[modes.X.openai].temperature wins over [providers.openai].temperature."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(data={"providers": {"openai": {"api_key": "sk-test", "temperature": 0.3}}}),
    )
    mode_config: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "kind": "immediate",
        "openai": {"temperature": 0.9},
    }

    provider = create_provider("openai", config, mode_config=mode_config)
    resolved = cast(OpenAIProvider, provider)._resolve_provider_config_value("temperature", 0.7)
    assert resolved == 0.9


def test_root_providers_namespace_unknown_keys_are_rejected() -> None:
    """Unrecognized keys at [providers.X] surface as user-facing DoxaError."""
    from types import SimpleNamespace

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = cast(
        ConfigManager,
        SimpleNamespace(
            data={"providers": {"openai": {"api_key": "sk-test", "definitely_not_a_real_key": "x"}}}
        ),
    )
    with pytest.raises(
        DoxaError,
        match=r"Unsupported provider parameter: providers\.openai\.definitely_not_a_real_key",
    ):
        create_provider("openai", config)


def test_root_providers_namespace_works_for_perplexity_and_gemini() -> None:
    """[providers.perplexity] / [providers.gemini] flow through symmetrically."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    # Perplexity: a root-level key should be reachable as a default.
    pplx_config = cast(
        ConfigManager,
        SimpleNamespace(
            data={"providers": {"perplexity": {"api_key": "pplx-test", "temperature": 0.25}}}
        ),
    )
    pplx_provider = create_provider("perplexity", pplx_config)
    assert pplx_provider.config.get("temperature") == 0.25

    # Gemini: same shape.
    mock_client = SimpleNamespace()
    mock_client.aio = SimpleNamespace()
    mock_client.aio.models = SimpleNamespace()
    gemini_config = cast(
        ConfigManager,
        SimpleNamespace(
            data={"providers": {"gemini": {"api_key": "AIza-test", "temperature": 0.4}}}
        ),
    )
    with patch("google.genai.Client", return_value=mock_client):
        gemini_provider = create_provider("gemini", gemini_config)
    assert gemini_provider.config.get("temperature") == 0.4


# --- gpt-5.6-sol migration (o3/o4-mini-deep-research retired 2026-07-23) ------


def _capture_sol_request(config_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Submit through a stubbed client and return the request kwargs."""
    captured: dict[str, Any] = {}

    async def fake_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(id="job-sol")

    cfg: dict[str, Any] = {"model": "gpt-5.6-sol"}
    if config_extra:
        cfg["openai"] = config_extra
    provider = OpenAIProvider(api_key="dummy", config=cfg)
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(create=fake_create))
    )
    asyncio.run(provider.submit("test prompt", mode="deep_research"))
    return captured


def test_sol_is_submitted_in_background_mode() -> None:
    """gpt-5.6-sol carries no "deep-research" substring; the registry classifies it."""
    assert _capture_sol_request()["background"] is True


def test_sol_omits_temperature() -> None:
    """The API rejects temperature for gpt-5.6-sol.

    Verified live 2026-09-08: "Unsupported parameter: 'temperature' is not
    supported with this model." The previous guard tested `startswith("o")`,
    which let the gpt-5 family through.
    """
    assert "temperature" not in _capture_sol_request()


def test_sol_uses_non_preview_web_search() -> None:
    """web_search_preview ignores filters/return_token_budget; use web_search."""
    tools = [t.get("type") for t in _capture_sol_request()["tools"]]
    assert "web_search" in tools
    assert "web_search_preview" not in tools


def test_sol_requires_web_search_specifically_by_default() -> None:
    """Sol is general-purpose and may skip search under tool_choice=auto.

    Targets web search by name rather than "required": Code Interpreter is
    enabled by default, so plain "required" is satisfied by a calculation and
    would leave the answer ungrounded.
    """
    assert _capture_sol_request()["tool_choice"] == {"type": "web_search"}


def test_sol_tool_choice_is_overridable() -> None:
    assert _capture_sol_request({"tool_choice": "auto"})["tool_choice"] == "auto"


def test_sol_web_search_false_omits_tool_and_choice() -> None:
    """A mode that disables search must not have it forced back on.

    `prd` and similar modes legitimately synthesise from supplied material.
    """
    captured = _capture_sol_request({"web_search": False})
    assert "web_search" not in [t["type"] for t in captured["tools"]]
    assert "tool_choice" not in captured


def test_quick_research_has_no_invented_tool_call_cap() -> None:
    """The cheap tier is gone; an invented default would override user limits."""
    from doxa_research.config import BUILTIN_MODES

    # The mode carries no openai namespace at all now, which is the invariant:
    # any default here would win over limits users set on the mode.
    assert "openai" not in BUILTIN_MODES["quick_research"]


def test_sol_defaults_to_high_reasoning_effort() -> None:
    """The API default is "medium"; research work asks for more explicitly."""
    assert _capture_sol_request()["reasoning"]["effort"] == "high"


def test_sol_reasoning_effort_is_overridable() -> None:
    captured = _capture_sol_request({"reasoning_effort": "xhigh"})
    assert captured["reasoning"]["effort"] == "xhigh"
