"""PROVIDERS registry + create_provider factory tests.

Verifies that the R02 collapse of ProviderRegistry + legacy create_provider
into a single PROVIDERS dict + create_provider function keeps the public
contract: the factory looks up classes by name, raises on unknown names,
and resolves API keys via resolve_api_key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from doxa_research import __main__ as doxa_main
from doxa_research.errors import DoxaError
from doxa_research.providers import (
    PROVIDER_ENV_VARS,
    PROVIDERS,
    MockProvider,
    OpenAIProvider,
    PerplexityProvider,
    create_provider,
)


def _stub_config(providers: dict[str, dict]) -> SimpleNamespace:
    """Minimal ConfigManager shape: just .data['providers']."""
    return SimpleNamespace(data={"providers": providers})


def test_providers_dict_has_expected_keys() -> None:
    assert set(PROVIDERS) == {"openai", "perplexity", "gemini", "mock"}


def test_providers_dict_maps_to_classes() -> None:
    assert PROVIDERS["openai"] is OpenAIProvider
    assert PROVIDERS["perplexity"] is PerplexityProvider
    assert PROVIDERS["mock"] is MockProvider


def test_provider_env_vars_keys_match_providers() -> None:
    assert set(PROVIDER_ENV_VARS) == set(PROVIDERS)


def test_create_provider_returns_mock_instance() -> None:
    config = _stub_config({"mock": {"api_key": "test-key"}})
    provider = create_provider("mock", config)  # ty: ignore[invalid-argument-type]
    assert isinstance(provider, MockProvider)
    assert provider.api_key == "test-key"


def test_create_provider_unknown_name_raises() -> None:
    config = _stub_config({})
    with pytest.raises(DoxaError, match="Unknown provider"):
        create_provider("bogus", config)  # ty: ignore[invalid-argument-type]


def test_create_provider_rejects_invalid_mock_key() -> None:
    config = _stub_config({"mock": {"api_key": "invalid"}})
    with pytest.raises(DoxaError, match="Invalid mock API key"):
        create_provider("mock", config)  # ty: ignore[invalid-argument-type]


def test_create_provider_is_reexported_from_main() -> None:
    assert doxa_main.create_provider is create_provider
    assert doxa_main.PROVIDERS is PROVIDERS
    assert doxa_main.MockProvider is MockProvider


# ---------------------------------------------------------------------------
# P27-T15 — Perplexity background-mode registry assertions
# ---------------------------------------------------------------------------


def test_perplexity_provider_is_implemented_for_background_kind() -> None:
    """T15: PerplexityProvider created with kind=background reports as implemented.

    P23 flipped is_implemented() to True for sync; P27 confirms the same
    holds for the background lifecycle. Used by the runner / CLI to decide
    whether to actually call the provider vs. surface a 'not yet
    implemented' message.
    """
    from doxa_research.providers.perplexity import PerplexityProvider

    p = PerplexityProvider(
        api_key="pplx-test",
        config={"model": "sonar-deep-research", "kind": "background"},
    )
    assert p.is_implemented() is True
    assert p.implementation_status() is None


def test_perplexity_deep_research_mode_resolves_to_sonar_deep_research() -> None:
    """T15: BUILTIN_MODES['perplexity_deep_research'] points at the right model + kind.

    Locks the mode-config contract that the runner relies on: a user
    invoking `--mode perplexity_deep_research` ends up with model =
    sonar-deep-research and kind = background, which routes to the async
    submit path.
    """
    from doxa_research.config import BUILTIN_MODES

    mode = BUILTIN_MODES["perplexity_deep_research"]
    assert mode["provider"] == "perplexity"
    assert mode["model"] == "sonar-deep-research"
    assert mode["kind"] == "background"
    perp_cfg = mode.get("perplexity")
    assert isinstance(perp_cfg, dict)
    assert perp_cfg.get("preset") == "high"
    assert "reasoning_effort" not in perp_cfg


def test_create_provider_returns_perplexity_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T15: `create_provider('perplexity', cfg)` returns a usable PerplexityProvider.

    monkeypatch.delenv guards against developer machines (and CI runners with
    PERPLEXITY_API_KEY exported) overriding the stub config's api_key — the
    factory prefers env-var values when present.
    """
    from doxa_research.providers.perplexity import PerplexityProvider

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    config = _stub_config({"perplexity": {"api_key": "pplx-test"}})
    provider = create_provider("perplexity", config)  # ty: ignore[invalid-argument-type]
    assert isinstance(provider, PerplexityProvider)
    assert provider.api_key == "pplx-test"
