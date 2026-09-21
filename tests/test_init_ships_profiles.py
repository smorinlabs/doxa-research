"""P21-T10: `doxa init` ships example profiles users can customize.

The generated `~/.config/doxa_research/doxa.config.toml` should contain:
  - daily          — thinking + default project for daily notes
  - quick          — thinking (immediate)
  - openai_deep    — single-provider deep_research
  - all_deep       — parallel openai+perplexity+gemini all_deep_research
  - interactive    — interactive default mode
  - deep_research  — deep_research with a `prompt_prefix` example
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from doxa_research.commands import CommandHandler
from doxa_research.config import ConfigManager
from doxa_research.config_profiles import resolve_prompt_prefix


@pytest.fixture(autouse=True)
def _reset_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI --config tests from leaking into later tests."""
    from doxa_research import config as doxa_config

    monkeypatch.setattr(doxa_config, "_config_path", None)


@pytest.fixture
def init_run(isolated_doxa_home: Path) -> Path:
    """Run init_command against the isolated XDG config dir, return config path."""
    from doxa_research.paths import user_config_file

    handler = CommandHandler(ConfigManager())
    handler.init_command(non_interactive=True)
    path = user_config_file()
    assert path.exists(), f"init did not create config at {path}"
    return path


def test_init_writes_parseable_config(init_run: Path) -> None:
    cm = ConfigManager()
    cm.load_all_layers({})
    # Every shipped profile should be in the catalog.
    names = {entry.name for entry in cm.profile_catalog}
    assert {
        "daily",
        "quick",
        "openai_deep",
        "all_deep",
        "interactive",
        "deep_research",
    }.issubset(names)


@pytest.mark.parametrize(
    "profile_name,expected_default_mode",
    [
        ("daily", "thinking"),
        ("quick", "thinking"),
        ("openai_deep", "deep_research"),
        ("all_deep", "all_deep_research"),
        ("interactive", "interactive"),
        ("deep_research", "deep_research"),
    ],
)
def test_each_shipped_profile_sets_expected_default_mode(
    init_run: Path, profile_name: str, expected_default_mode: str
) -> None:
    cm = ConfigManager()
    cm.load_all_layers({"_profile": profile_name})
    assert cm.profile_selection.name == profile_name
    assert cm.get("general.default_mode") == expected_default_mode


def test_openai_deep_profile_uses_single_provider(init_run: Path) -> None:
    cm = ConfigManager()
    cm.load_all_layers({"_profile": "openai_deep"})
    deep = cm.data["modes"]["deep_research"]
    assert deep.get("providers") == ["openai"]
    assert deep.get("parallel") is False


def test_all_deep_profile_uses_all_deep_research_mode(init_run: Path) -> None:
    """The all_deep profile points at the multi-provider all_deep_research mode.

    Previously it overrode `deep_research.providers` to `[openai, perplexity]`,
    which produced wrong-model dispatch for Perplexity (mode-generic
    `model: o3-deep-research` overrode Perplexity's own model). The
    all_deep_research mode uses per-provider namespace models so each
    provider runs its own Deep Research model.
    """
    cm = ConfigManager()
    cm.load_all_layers({"_profile": "all_deep"})
    assert cm.get("general.default_mode") == "all_deep_research"


def test_all_deep_research_mode_fans_out_with_per_provider_models() -> None:
    """all_deep_research is a built-in background mode that fans out to all
    three providers in parallel, each with its own provider-specific DR model.
    """
    cm = ConfigManager()
    cm.load_all_layers({})
    mode = cm.get_mode_config("all_deep_research")
    assert mode["kind"] == "background"
    assert mode["providers"] == ["openai", "perplexity", "gemini"]
    assert mode["parallel"] is True
    assert mode["openai"]["model"] == "gpt-5.6-sol"
    assert mode["perplexity"]["model"] == "sonar-deep-research"
    assert mode["gemini"]["model"] == "deep-research-preview-04-2026"


def test_openai_quick_immediate_mode_uses_gpt_4_1_mini_with_web_search() -> None:
    """openai_quick is the OpenAI counterpart of perplexity_quick / gemini_quick.

    Modern fast immediate model (gpt-4.1-mini, April 2025) with web search
    grounding enabled. Provides symmetric `*_quick` naming across the three
    providers so users can grep `doxa modes list | grep quick` to find the
    fast variant of any provider.
    """
    cm = ConfigManager()
    cm.load_all_layers({})
    mode = cm.get_mode_config("openai_quick")
    assert mode["kind"] == "immediate"
    assert mode["provider"] == "openai"
    assert mode["model"] == "gpt-4.1-mini"
    assert mode["openai"]["web_search"] is True


def test_all_three_providers_have_symmetric_quick_modes() -> None:
    """Every provider should have a *_quick immediate built-in so users can
    discover the fast variant via `doxa modes list | grep quick`.
    """
    cm = ConfigManager()
    cm.load_all_layers({})
    for mode_name in ("openai_quick", "perplexity_quick", "gemini_quick"):
        mode = cm.get_mode_config(mode_name)
        assert mode["kind"] == "immediate", f"{mode_name} should be immediate kind"


def test_deep_research_profile_carries_prompt_prefix(init_run: Path) -> None:
    cm = ConfigManager()
    cm.load_all_layers({"_profile": "deep_research"})
    prefix = resolve_prompt_prefix(cm, "deep_research")
    assert prefix is not None
    assert len(prefix) > 0


def test_build_profile_section_preserves_sibling_subsections() -> None:
    """C14: siblings sharing a prefix (e.g., modes.deep_research + modes.thinking)
    must coexist under the same intermediate table, not overwrite each other.

    P33: body is now a nested dict; sibling sections are naturally separate keys
    under the same parent dict (e.g. `{"modes": {"deep_research": {...}, "thinking": {...}}}`).
    """
    from doxa_research.commands import _build_profile_section

    body = {
        "modes": {
            "deep_research": {"providers": ["openai"], "parallel": False},
            "thinking": {"prompt_prefix": "Think hard."},
        },
    }
    table = _build_profile_section(body)
    modes = table.get("modes")
    assert modes is not None, "modes intermediate table missing"
    assert "deep_research" in modes, (
        f"deep_research silently dropped; modes keys = {list(modes.keys())}"
    )
    assert "thinking" in modes, f"thinking silently dropped; modes keys = {list(modes.keys())}"
    # Full content must round-trip — not just keys
    assert modes["deep_research"]["providers"] == ["openai"]
    assert modes["deep_research"]["parallel"] is False
    assert modes["thinking"]["prompt_prefix"] == "Think hard."


def test_cli_init_custom_config_path_writes_starter_profiles(
    isolated_doxa_home: Path,
    tmp_path: Path,
) -> None:
    from doxa_research.cli import cli

    target = tmp_path / "custom-cfg.toml"
    result = CliRunner().invoke(cli, ["--config", str(target), "init", "--non-interactive"])

    assert result.exit_code == 0, result.output
    assert target.exists()
    text = target.read_text()
    assert "[profiles.daily.general]" in text
    assert "[profiles.deep_research.modes.deep_research]" in text


def test_cli_init_json_non_interactive_writes_starter_profiles(
    isolated_doxa_home: Path,
    tmp_path: Path,
) -> None:
    import json

    from doxa_research.cli import cli

    target = tmp_path / "json-cfg.toml"
    result = CliRunner().invoke(
        cli,
        ["--config", str(target), "init", "--json", "--non-interactive"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["data"]["created"] is True
    text = target.read_text()
    assert "[profiles.daily.general]" in text
    assert "[profiles.deep_research.modes.deep_research]" in text
