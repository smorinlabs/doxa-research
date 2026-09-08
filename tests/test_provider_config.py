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
    """The cheap tier is gone; an invented default would override user limits.

    Checks both placements. An earlier version tested only the `openai`
    namespace, so adding a flat `max_tool_calls` reintroduced the override
    with the test still green.
    """
    from doxa_research.config import BUILTIN_MODES

    mode = BUILTIN_MODES["quick_research"]
    assert "max_tool_calls" not in mode
    # Both placements: a flat key and a namespaced one each override a limit
    # the user set on the mode, so neither may carry a default.
    openai_ns = mode.get("openai")
    assert not isinstance(openai_ns, dict) or "max_tool_calls" not in openai_ns


def _capture_stream_request(config_extra: dict[str, Any], model: str = "o3") -> dict[str, Any]:
    """Capture the request kwargs the streaming path builds."""
    captured: dict[str, Any] = {}

    class _FakeStream:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    def fake_stream(**kwargs: object) -> object:
        captured.update(kwargs)
        return _FakeStream()

    provider = OpenAIProvider(api_key="dummy", config={"model": model, "openai": config_extra})
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(stream=fake_stream))
    )

    async def _drain() -> None:
        async for _ in provider.stream("test prompt", mode="quick"):
            pass

    asyncio.run(_drain())
    return captured


def test_stream_preserves_explicit_reasoning_effort_and_tool_choice() -> None:
    """Explicit settings must survive the immediate path too.

    Defaults may differ between immediate and background, but a value the
    user configured is dropped by neither.
    """
    captured = _capture_stream_request(
        {"reasoning_effort": "low", "tool_choice": "required", "web_search": True}
    )
    assert captured["reasoning"]["effort"] == "low"
    assert captured["tool_choice"] == "required"


def test_stream_omits_temperature_for_models_that_reject_it() -> None:
    """Guards the stream() path against the prefix rule creeping back.

    Uses gpt-5.5 deliberately. Against an o-series model the old
    `startswith("o")` rule and the correct one agree, so such a test passes
    under both implementations and constrains nothing. gpt-5.5 rejects
    temperature (verified live 2026-09-08) while not starting with "o", which
    is exactly where the two rules diverge.
    """
    captured = _capture_stream_request({"web_search": True}, model="gpt-5.5")
    assert "temperature" not in captured
    # And the o-series case still holds.
    assert "temperature" not in _capture_stream_request({"web_search": True}, model="o3")


def test_temperature_dropped_when_reasoning_effort_raised() -> None:
    """gpt-5.2 accepts temperature at effort "none" and rejects it above.

    Verified live 2026-09-08.
    """
    captured: dict[str, Any] = {}

    async def fake_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(id="job-effort")

    provider = OpenAIProvider(
        api_key="dummy",
        config={
            "model": "gpt-5.2",
            "background": True,
            "openai": {"reasoning_effort": "high", "temperature": 0.5},
        },
    )
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(create=fake_create))
    )
    asyncio.run(provider.submit("test prompt", mode="deep_research"))
    assert captured["reasoning"]["effort"] == "high"
    assert "temperature" not in captured


def test_sol_defaults_to_max_reasoning_effort() -> None:
    """The API default is "medium"; owner decision of 2026-09-08 sets "max"."""
    assert _capture_sol_request()["reasoning"]["effort"] == "max"


def test_sol_reasoning_effort_is_overridable() -> None:
    captured = _capture_sol_request({"reasoning_effort": "xhigh"})
    assert captured["reasoning"]["effort"] == "xhigh"


# --- second-review findings: defaults must not leak onto retired models -----


def test_retired_deep_research_model_gets_no_new_defaults() -> None:
    """gpt-5.6 defaults must not reach o3-deep-research.

    `is_background_model()` is still true for it via the substring rule, but
    that model never exposed reasoning effort. Keying the defaults on the
    registry instead of the substring keeps the old request shape for it.
    """
    captured: dict[str, Any] = {}

    async def fake_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(id="job-retired")

    provider = OpenAIProvider(api_key="dummy", config={"model": "o3-deep-research"})
    provider.client = cast(
        Any, types.SimpleNamespace(responses=types.SimpleNamespace(create=fake_create))
    )
    asyncio.run(provider.submit("test prompt", mode="deep_research"))
    assert "effort" not in captured["reasoning"]
    assert "tool_choice" not in captured


def test_explicit_tool_choice_survives_web_search_disabled() -> None:
    """Only the default is conditioned on web search being present."""
    captured = _capture_sol_request(
        {"web_search": False, "tool_choice": {"type": "code_interpreter"}}
    )
    assert captured["tool_choice"] == {"type": "code_interpreter"}


def test_stream_explicit_tool_choice_survives_without_web_search() -> None:
    captured = _capture_stream_request({"tool_choice": "required"}, model="gpt-5.4")
    assert captured["tool_choice"] == "required"


def test_stream_sends_temperature_when_supported() -> None:
    """Positive control: the stream guard must not omit temperature always."""
    captured = _capture_stream_request({"temperature": 0.3}, model="gpt-4o")
    assert captured["temperature"] == 0.3


def test_sol_streams_without_raising() -> None:
    """The headline fix, asserted behaviourally rather than via the predicate."""
    captured = _capture_stream_request({"web_search": True}, model="gpt-5.6-sol")
    assert captured["model"] == "gpt-5.6-sol"


def test_true_deep_research_model_still_refuses_to_stream() -> None:
    provider = OpenAIProvider(api_key="dummy", config={"model": "o3-deep-research"})

    async def _drain() -> None:
        async for _ in provider.stream("p", mode="quick"):
            pass

    with pytest.raises(NotImplementedError):
        asyncio.run(_drain())


def test_immediate_kind_with_sol_does_not_raise() -> None:
    """Registering Sol as background must not make immediate use a config error."""
    provider = OpenAIProvider(api_key="dummy", config={"model": "gpt-5.6-sol", "kind": "immediate"})
    provider._validate_kind_for_model("some_mode")  # must not raise


def test_list_models_marks_retired_entries() -> None:
    """The listing must say a model is dead; that is the whole point.

    `/v1/models` keeps returning retired IDs, which is why the o3/o4-mini
    shutdown was invisible.
    """

    async def fake_list() -> object:
        return types.SimpleNamespace(
            data=[
                types.SimpleNamespace(
                    id="o3-deep-research",
                    created=1719500001,
                    owned_by="system",
                    shutdown_date="2026-07-23",
                ),
                types.SimpleNamespace(
                    id="gpt-4o", created=1719500002, owned_by="system", shutdown_date=None
                ),
            ]
        )

    provider = OpenAIProvider(api_key="dummy", config={"model": "gpt-5.6-sol"})
    provider.client = cast(Any, types.SimpleNamespace(models=types.SimpleNamespace(list=fake_list)))
    models = asyncio.run(provider.list_models())
    by_id = {m["id"]: m for m in models}
    assert by_id["o3-deep-research"]["type"] == "retired"
    assert by_id["o3-deep-research"]["shutdown_date"] == "2026-07-23"
    assert by_id["gpt-4o"]["type"] == "unknown"
    # Every row carries the key, so consumers need no defensive .get().
    assert all("shutdown_date" in m for m in models)


def test_provider_scope_accepts_structured_tool_choice_and_summary() -> None:
    """Provider scope and mode scope must accept the same shapes."""
    from doxa_research.config_schema import OpenAIConfig

    cfg = OpenAIConfig(api_key="k", tool_choice={"type": "web_search"}, reasoning_summary="auto")
    assert cfg.tool_choice == {"type": "web_search"}
    assert cfg.reasoning_summary == "auto"
