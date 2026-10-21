import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import pytest

from doxa_research.config import ConfigManager
from doxa_research.errors import DoxaError
from doxa_research.providers import create_provider
from doxa_research.providers.parameter_config import build_provider_runtime_config
from doxa_research.providers.perplexity import PerplexityProvider


def _config(data: dict[str, Any]) -> ConfigManager:
    return cast(ConfigManager, SimpleNamespace(data=data))


def _loaded_config(tmp_path: Path, text: str, *, profile: str | None = None) -> ConfigManager:
    config_path = tmp_path / "doxa.config.toml"
    config_path.write_text(text.strip() + "\n", encoding="utf-8")
    manager = ConfigManager(config_path=config_path)
    cli_args = {"_profile": profile} if profile else {}
    manager.load_all_layers(cli_args)
    return manager


@pytest.mark.parametrize(
    "lower_key,higher_key",
    [
        ("max_tokens", "max_output_tokens"),
        ("max_output_tokens", "max_tokens"),
    ],
)
@pytest.mark.parametrize("higher_scope", ["generic", "namespace"])
def test_agent_token_alias_mode_override_reaches_actual_wire(
    tmp_path: Path,
    lower_key: str,
    higher_key: str,
    higher_scope: str,
) -> None:
    higher_table = "" if higher_scope == "generic" else "[modes.alias_test.perplexity]"
    config = _loaded_config(
        tmp_path,
        f"""
        version = "2.0"
        [providers.perplexity]
        {lower_key} = 4096
        [modes.alias_test]
        provider = "perplexity"
        model = "sonar-deep-research"
        kind = "background"
        {higher_table}
        {higher_key} = 1024
    """,
    )
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"id": "alias-job", "status": "queued", "output": []})

    async def run() -> None:
        provider = cast(
            PerplexityProvider,
            create_provider(
                "perplexity",
                config,
                cli_api_key="offline-key",
                mode_config=config.get_mode_config("alias_test"),
            ),
        )
        await provider._async_http.aclose()
        provider._async_http = httpx2.AsyncClient(
            base_url="https://api.perplexity.ai",
            transport=httpx2.MockTransport(respond),
        )
        try:
            assert await provider.submit("query", "alias_test") == "agent:alias-job"
        finally:
            await provider._async_http.aclose()
            await provider.client.close()

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/agent"
    body = json.loads(requests[0].content)
    assert body["max_output_tokens"] == 1024
    assert "max_tokens" not in body


@pytest.mark.parametrize(
    "lower_key,higher_key",
    [
        ("max_tokens", "max_output_tokens"),
        ("max_output_tokens", "max_tokens"),
    ],
)
def test_agent_token_alias_namespace_overrides_generic_mode(
    lower_key: str,
    higher_key: str,
) -> None:
    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=_config({}),
        timeout_override=None,
        mode_config={"kind": "background", lower_key: 4096, "perplexity": {higher_key: 1024}},
    )
    namespace = runtime.to_legacy_config()["perplexity"]
    assert namespace[higher_key] == 1024
    assert lower_key not in namespace
    winning_group = "common_request" if higher_key == "max_output_tokens" else "provider_request"
    losing_group = "common_request" if lower_key == "max_output_tokens" else "provider_request"
    assert runtime.sources[f"{winning_group}.{higher_key}"] == "mode_config.perplexity"
    assert f"{losing_group}.{lower_key}" not in runtime.sources


def test_agent_provider_legacy_token_limit_overrides_common_default() -> None:
    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        timeout_override=None,
        config=_config(
            {
                "providers": {
                    "defaults": {"max_output_tokens": 4096},
                    "perplexity": {"max_tokens": 1024},
                }
            }
        ),
        mode_config={"kind": "background"},
    )
    assert runtime.to_legacy_config()["perplexity"]["max_tokens"] == 1024
    assert "max_output_tokens" not in runtime.common_request


@pytest.mark.parametrize("scope", ["provider", "generic", "namespace"])
def test_agent_same_layer_token_alias_conflict_fails_before_provider_creation(
    scope: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doxa_research.providers import PROVIDERS

    created: list[object] = []

    def unexpected_provider(*args: Any, **kwargs: Any) -> None:
        created.append((args, kwargs))
        raise AssertionError("No client or request may be created for conflicting aliases")

    monkeypatch.setitem(PROVIDERS, "perplexity", unexpected_provider)
    values = {"max_tokens": 4096, "max_output_tokens": 1024}
    provider_values = values if scope == "provider" else {}
    mode: dict[str, Any] = {"kind": "background", "model": "sonar-deep-research"}
    if scope == "generic":
        mode.update(values)
    elif scope == "namespace":
        mode["perplexity"] = values
    with pytest.raises(DoxaError, match="Conflicting max_tokens and max_output_tokens"):
        create_provider(
            "perplexity",
            _config({"providers": {"perplexity": provider_values}}),
            cli_api_key="offline-key",
            mode_config=mode,
        )
    assert created == []


@pytest.mark.parametrize("provider,kind", [("perplexity", "immediate"), ("openai", "background")])
def test_agent_alias_reconciliation_does_not_change_other_request_paths(
    provider: str,
    kind: str,
) -> None:
    runtime = build_provider_runtime_config(
        provider_name=provider,
        timeout_override=None,
        config=_config({"providers": {provider: {"max_tokens": 4096}}}),
        mode_config={"kind": kind, "max_output_tokens": 1024},
    )
    assert runtime.common_request["max_output_tokens"] == 1024
    assert runtime.provider_request["max_tokens"] == 4096


def test_agent_same_layer_equal_token_aliases_are_allowed() -> None:
    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=_config({}),
        timeout_override=None,
        mode_config={"kind": "background", "max_tokens": 1024, "max_output_tokens": 1024},
    )
    namespace = runtime.to_legacy_config()["perplexity"]
    assert namespace["max_output_tokens"] == 1024


def test_active_profile_provider_overlay_is_consumed_after_config_manager_merge(
    tmp_path: Path,
) -> None:
    config = _loaded_config(
        tmp_path,
        """
        version = "2.0"

        [providers.gemini]
        api_key = "AIza-root"
        timeout = 30
        temperature = 0.4

        [profiles.work.providers.gemini]
        api_key = "AIza-profile"
        timeout = 45
        temperature = 0.6
        """,
        profile="work",
    )

    runtime = build_provider_runtime_config(
        provider_name="gemini",
        config=config,
        mode_config=None,
        timeout_override=None,
    )

    assert "profiles" not in config.data
    assert runtime.auth["api_key"] == "AIza-profile"
    assert runtime.client["timeout"] == 45
    assert runtime.common_request["temperature"] == 0.6
    assert runtime.sources["client.timeout"] == "providers.gemini"


def test_normalizer_does_not_reapply_raw_profiles_table() -> None:
    """Profiles are a ConfigManager layer, not a second provider-normalizer path."""
    config = _config(
        {
            "providers": {
                "openai": {
                    "api_key": "sk-provider",
                    "temperature": 0.2,
                }
            },
            "profiles": {
                "work": {
                    "providers": {
                        "openai": {
                            "temperature": 0.9,
                            "max_tool_calls": 20,
                        }
                    }
                }
            },
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="openai",
        config=config,
        mode_config=None,
        timeout_override=None,
    )

    assert runtime.common_request["temperature"] == 0.2
    assert runtime.provider_request == {}
    assert runtime.sources["common_request.temperature"] == "providers.openai"


def test_profile_mode_overlay_is_consumed_from_resolved_mode_config(tmp_path: Path) -> None:
    config = _loaded_config(
        tmp_path,
        """
        version = "2.0"

        [providers.perplexity]
        api_key = "pplx-test"
        temperature = 0.1

        [modes.focused]
        provider = "perplexity"
        model = "sonar"
        kind = "immediate"
        temperature = 0.3

        [modes.focused.perplexity]
        temperature = 0.5
        kind = "immediate"
        response_format = { type = "json_object" }

        [profiles.work.modes.focused]
        top_p = 0.8
        temperature = 0.4

        [profiles.work.modes.focused.perplexity]
        kind = "background"
        temperature = 0.7
        response_format = { type = "json_schema" }
        """,
        profile="work",
    )
    mode_config = config.get_mode_config("focused")

    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config=mode_config,
        timeout_override=None,
    )

    assert runtime.common_request["temperature"] == 0.7
    assert runtime.common_request["top_p"] == 0.8
    assert runtime.common_request["response_format"] == {"type": "json_schema"}
    assert runtime.routing["kind"] == "background"
    assert runtime.provider_request == {}


def test_provider_namespace_model_is_provider_specific_override() -> None:
    config = _config(
        {
            "providers": {
                "perplexity": {"api_key": "pplx-test"},
            }
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config={
            "provider": "perplexity",
            "model": "sonar",
            "perplexity": {"model": "sonar-pro"},
        },
        timeout_override=None,
    )

    assert runtime.common_request["model"] == "sonar"
    assert runtime.provider_request["model"] == "sonar-pro"
    assert runtime.to_legacy_config()["model"] == "sonar-pro"
    assert runtime.to_legacy_config()["perplexity"]["model"] == "sonar-pro"


def test_runtime_timeout_override_wins_over_config_layers() -> None:
    config = _config(
        {
            "providers": {
                "defaults": {"timeout": 30},
                "openai": {"api_key": "sk-test", "timeout": 45},
            }
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="openai",
        config=config,
        mode_config=None,
        timeout_override=60,
    )

    assert runtime.client["timeout"] == 60


def test_all_provider_defaults_only_promote_shared_fields() -> None:
    config = _config(
        {
            "providers": {
                "defaults": {
                    "timeout": 45,
                    "temperature": 0.4,
                    "kind": "background",
                },
                "openai": {"api_key": "sk-profile"},
            },
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="openai",
        config=config,
        mode_config=None,
        timeout_override=None,
    )

    assert runtime.auth == {"api_key": "sk-profile"}
    assert runtime.client == {"timeout": 45}
    assert runtime.common_request["temperature"] == 0.4
    assert runtime.routing["kind"] == "background"
    assert runtime.provider_request == {}
    assert runtime.to_legacy_config() == {
        "api_key": "sk-profile",
        "timeout": 45,
        "kind": "background",
        "temperature": 0.4,
        "openai": {"temperature": 0.4},
    }


def test_all_provider_defaults_reject_disallowed_fields() -> None:
    config = _config(
        {
            "providers": {
                "defaults": {
                    "api_key": "shared-secret",
                },
                "openai": {"api_key": "sk-provider"},
            }
        }
    )

    with pytest.raises(ValueError, match="providers\\.defaults\\.api_key"):
        build_provider_runtime_config(
            provider_name="openai",
            config=config,
            mode_config=None,
            timeout_override=None,
        )


def test_mode_generic_params_do_not_promote_auth_client_or_unknown_keys() -> None:
    config = _config(
        {
            "providers": {
                "openai": {
                    "api_key": "sk-provider",
                    "timeout": 30,
                }
            }
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="openai",
        config=config,
        mode_config={
            "api_key": "mode-secret-should-not-promote",
            "timeout": 5,
            "temperature": 0.2,
            "unknown_mode_key": "ignored",
        },
        timeout_override=None,
    )

    assert runtime.auth == {"api_key": "sk-provider"}
    assert runtime.client == {"timeout": 30}
    assert runtime.common_request == {"temperature": 0.2}
    assert runtime.provider_request == {}


def test_legacy_flat_mode_provider_native_key_is_preserved_for_selected_provider() -> None:
    config = _config(
        {
            "providers": {
                "openai": {"api_key": "sk-provider"},
            }
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="openai",
        config=config,
        mode_config={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "kind": "immediate",
            "max_tool_calls": 12,
        },
        timeout_override=None,
    )

    assert runtime.provider_request["max_tool_calls"] == 12
    assert runtime.to_legacy_config()["openai"]["max_tool_calls"] == 12


def test_mode_level_max_tokens_routes_to_provider_request() -> None:
    config = _config(
        {
            "providers": {
                "perplexity": {"api_key": "pplx-x"},
            }
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config={
            "provider": "perplexity",
            "max_tokens": 4096,
        },
        timeout_override=None,
    )

    assert runtime.provider_request["max_tokens"] == 4096
    assert runtime.to_legacy_config()["perplexity"]["max_tokens"] == 4096
    assert runtime.sources["provider_request.max_tokens"] == "mode_config"


def test_root_provider_recognized_native_keys_become_provider_request(tmp_path: Path) -> None:
    config = _loaded_config(
        tmp_path,
        """
        version = "2.0"

        [providers.openai]
        api_key = "sk-provider"
        max_tool_calls = 50

        [providers.perplexity]
        api_key = "pplx-provider"
        stream_mode = "full"

        [profiles.work.providers.openai]
        max_tool_calls = 30
        """,
        profile="work",
    )

    openai_runtime = build_provider_runtime_config(
        provider_name="openai",
        config=config,
        mode_config=None,
        timeout_override=None,
    )
    perplexity_runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config=None,
        timeout_override=None,
    )

    assert openai_runtime.provider_request == {"max_tool_calls": 30}
    assert perplexity_runtime.provider_request == {"stream_mode": "full"}


def test_unknown_root_provider_key_is_rejected() -> None:
    config = _config(
        {
            "providers": {
                "perplexity": {
                    "api_key": "pplx-test",
                    "definitely_not_real": "x",
                }
            }
        }
    )

    with pytest.raises(ValueError, match="providers\\.perplexity\\.definitely_not_real"):
        build_provider_runtime_config(
            provider_name="perplexity",
            config=config,
            mode_config=None,
            timeout_override=None,
        )


def test_unknown_mode_provider_namespace_key_is_rejected() -> None:
    config = _config(
        {
            "providers": {
                "perplexity": {"api_key": "pplx-test"},
            }
        }
    )

    with pytest.raises(ValueError, match="mode_config\\.perplexity\\.future_sdk_option"):
        build_provider_runtime_config(
            provider_name="perplexity",
            config=config,
            mode_config={
                "provider": "perplexity",
                "model": "sonar",
                "kind": "immediate",
                "perplexity": {"future_sdk_option": True},
            },
            timeout_override=None,
        )


def test_mode_provider_namespace_extra_body_is_allowed() -> None:
    config = _config(
        {
            "providers": {
                "perplexity": {"api_key": "pplx-test"},
            }
        }
    )

    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config={
            "provider": "perplexity",
            "model": "sonar",
            "kind": "immediate",
            "perplexity": {"extra_body": {"new_vendor_flag": True}},
        },
        timeout_override=None,
    )

    assert runtime.extension_bags["perplexity"]["extra_body"] == {"new_vendor_flag": True}


def test_profile_mode_provider_namespace_extra_body_is_allowed(tmp_path: Path) -> None:
    config = _loaded_config(
        tmp_path,
        """
        version = "2.0"

        [providers.perplexity]
        api_key = "pplx-test"

        [modes.focused]
        provider = "perplexity"
        model = "sonar"
        kind = "immediate"

        [profiles.work.modes.focused.perplexity]
        extra_body = { profile_vendor_flag = true }
        """,
        profile="work",
    )
    mode_config = config.get_mode_config("focused")

    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config=mode_config,
        timeout_override=None,
    )

    assert runtime.extension_bags["perplexity"]["extra_body"] == {"profile_vendor_flag": True}


def test_perplexity_extra_body_survives_runtime_to_legacy_request_shapes() -> None:
    from doxa_research.providers.perplexity import PerplexityProvider

    config = _config(
        {
            "providers": {
                "perplexity": {"api_key": "pplx-test"},
            }
        }
    )
    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config={
            "provider": "perplexity",
            "model": "sonar",
            "kind": "immediate",
            "perplexity": {"extra_body": {"new_vendor_flag": True}},
        },
        timeout_override=None,
    )

    provider = PerplexityProvider(
        api_key=runtime.auth["api_key"],
        config=runtime.to_legacy_config(),
    )
    sync_params = provider._build_request_params("prompt", None)
    assert sync_params["extra_body"]["new_vendor_flag"] is True

    from doxa_research.errors import ProviderError

    agent_runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config={
            "provider": "perplexity",
            "model": "sonar-deep-research",
            "kind": "background",
            "perplexity": {"extra_body": {"new_vendor_flag": True}},
        },
        timeout_override=None,
    )
    agent = PerplexityProvider("offline-key", agent_runtime.to_legacy_config())
    with pytest.raises(ProviderError, match="new_vendor_flag"):
        agent._build_agent_request_body("prompt", None)


def test_profile_perplexity_extra_body_survives_runtime_to_legacy_request_shapes(
    tmp_path: Path,
) -> None:
    from doxa_research.providers.perplexity import PerplexityProvider

    config = _loaded_config(
        tmp_path,
        """
        version = "2.0"

        [providers.perplexity]
        api_key = "pplx-test"

        [modes.focused]
        provider = "perplexity"
        model = "sonar"
        kind = "immediate"

        [profiles.work.modes.focused.perplexity]
        extra_body = { profile_vendor_flag = true }
        """,
        profile="work",
    )
    mode_config = config.get_mode_config("focused")
    runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config=mode_config,
        timeout_override=None,
    )

    provider = PerplexityProvider(
        api_key=runtime.auth["api_key"],
        config=runtime.to_legacy_config(),
    )
    sync_params = provider._build_request_params("prompt", None)
    assert sync_params["extra_body"]["profile_vendor_flag"] is True

    from doxa_research.errors import ProviderError

    agent_runtime = build_provider_runtime_config(
        provider_name="perplexity",
        config=config,
        mode_config={
            "provider": "perplexity",
            "model": "sonar-deep-research",
            "kind": "background",
            "perplexity": {"extra_body": {"profile_vendor_flag": True}},
        },
        timeout_override=None,
    )
    agent = PerplexityProvider("offline-key", agent_runtime.to_legacy_config())
    with pytest.raises(ProviderError, match="profile_vendor_flag"):
        agent._build_agent_request_body("prompt", None)


def test_builtin_mode_provider_namespace_user_override_deep_merges() -> None:
    from doxa_research.config import ConfigManager

    manager = ConfigManager.__new__(ConfigManager)
    manager.data = {
        "modes": {
            "gemini_quick": {
                "gemini": {"temperature": 0.2},
            }
        }
    }

    mode = manager.get_mode_config("gemini_quick")

    assert mode["gemini"]["temperature"] == 0.2
    assert mode["gemini"]["tools"] == ["google_search"]
    assert mode["gemini"]["thinking_budget"] == 0
