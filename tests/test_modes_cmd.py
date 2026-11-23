"""Tests for doxa_research.modes_cmd.list_all_modes and ModeInfo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from doxa_research.config import ConfigManager
from doxa_research.modes_cmd import ModeInfo, list_all_modes, modes_command
from tests._fixture_helpers import run_doxa


@pytest.fixture(autouse=True)
def _wide_columns_for_table_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich auto-detects terminal width. Under `capsys` there's no tty, so
    Rich falls back to a narrow default and truncates headers. Force a wide
    virtual terminal so table-rendering assertions ("default", "deep_research",
    full column headers) see the complete text."""
    monkeypatch.setenv("COLUMNS", "200")


def _cm(isolated_doxa_home: Path, toml: str | None = None) -> ConfigManager:
    if toml is not None:
        cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(toml)
    cm = ConfigManager()
    cm.load_all_layers({})
    return cm


def test_returns_all_builtin_modes(isolated_doxa_home: Path) -> None:
    modes = list_all_modes(_cm(isolated_doxa_home))
    names = {m.name for m in modes}
    assert {"default", "clarification", "thinking", "deep_research"} <= names


def test_builtin_mode_fields_populated(isolated_doxa_home: Path) -> None:
    modes = list_all_modes(_cm(isolated_doxa_home))
    default = next(m for m in modes if m.name == "default")
    assert default.source == "builtin"
    assert default.providers == ["openai"]
    assert default.model == "gpt-5.6-sol"
    assert default.kind == "immediate"
    assert default.overrides == {}


def test_deep_research_mode_is_background(isolated_doxa_home: Path) -> None:
    modes = list_all_modes(_cm(isolated_doxa_home))
    dr = next(m for m in modes if m.name == "deep_research")
    assert dr.kind == "background"


def test_providers_list_normalization(isolated_doxa_home: Path) -> None:
    # deep_research uses `providers: ["openai"]` (list form) — must normalize.
    modes = list_all_modes(_cm(isolated_doxa_home))
    dr = next(m for m in modes if m.name == "deep_research")
    assert isinstance(dr.providers, list)
    assert dr.providers == ["openai"]


def test_user_only_mode(isolated_doxa_home: Path) -> None:
    toml = (
        'version = "2.0"\n'
        "[modes.my_brief]\n"
        'provider = "openai"\n'
        'model = "gpt-4o-mini"\n'
        'description = "my user-only mode"\n'
    )
    modes = list_all_modes(_cm(isolated_doxa_home, toml))
    mine = next(m for m in modes if m.name == "my_brief")
    assert mine.source == "user"
    assert mine.model == "gpt-4o-mini"
    assert mine.kind == "immediate"
    assert mine.overrides == {}


def test_overridden_mode_reports_diff(isolated_doxa_home: Path) -> None:
    toml = 'version = "2.0"\n[modes.deep_research]\nparallel = false\n'
    modes = list_all_modes(_cm(isolated_doxa_home, toml))
    dr = next(m for m in modes if m.name == "deep_research")
    assert dr.source == "overridden"
    assert "parallel" in dr.overrides
    assert dr.overrides["parallel"] == {"builtin": True, "effective": False}


def test_malformed_user_mode_kind_unknown(isolated_doxa_home: Path) -> None:
    # No model, no provider — must NOT crash; must surface as unknown.
    toml = 'version = "2.0"\n[modes.broken]\ndescription = "missing model and provider"\n'
    modes = list_all_modes(_cm(isolated_doxa_home, toml))
    broken = next(m for m in modes if m.name == "broken")
    assert broken.source == "user"
    assert broken.kind == "unknown"
    assert broken.warnings  # non-empty list of warning strings


def test_modeinfo_is_frozen_dataclass() -> None:
    m = ModeInfo(
        name="x",
        source="builtin",
        providers=["openai"],
        model="o3",
        kind="immediate",
        description="",
        overrides={},
        warnings=[],
        raw={},
    )
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        m.name = "y"  # type: ignore[misc]  # ty: ignore[invalid-assignment]


def test_modes_command_list_default_prints_table(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = modes_command("list", [])
    out = capsys.readouterr().out
    assert rc == 0
    # Column headers present
    for header in ("Mode", "Source", "Provider", "Model", "Kind", "Description"):
        assert header in out
    # Known mode rows present
    assert "default" in out
    assert "deep_research" in out


def test_modes_command_default_op_is_list(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `doxa modes` with no op should behave like `doxa modes list`.
    rc = modes_command(None, [])
    out = capsys.readouterr().out
    assert rc == 0
    assert "default" in out


def test_modes_command_unknown_op_returns_2(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = modes_command("bogus", [])
    assert rc == 2


def test_modes_command_list_sort_order(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Sort: source -> kind -> provider -> model -> name.
    # builtin + immediate modes (default, clarification, thinking) must appear
    # before builtin + background modes (deep_research, exploration, etc.).
    rc = modes_command("list", [])
    out = capsys.readouterr().out
    assert rc == 0
    # default (immediate) line number < deep_research (background) line number
    lines = out.splitlines()
    default_idx = next(i for i, ln in enumerate(lines) if " default " in ln)
    deep_idx = next(i for i, ln in enumerate(lines) if " deep_research " in ln)
    assert default_idx < deep_idx


def test_modes_list_json_shape(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = modes_command("list", ["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, dict)
    assert data["schema_version"] == "1"
    assert isinstance(data["modes"], list)
    default = next(m for m in data["modes"] if m["name"] == "default")
    assert default["kind"] == "immediate"
    assert default["providers"] == ["openai"]
    assert "description" in default


def test_modes_list_masks_api_key_inside_mode(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        'version = "2.0"\n'
        "[modes.secret_mode]\n"
        'provider = "openai"\n'
        'model = "o3"\n'
        'api_key = "sk-verysecretverysecret1234"\n'
    )
    rc = modes_command("list", ["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    secret = next(m for m in data["modes"] if m["name"] == "secret_mode")
    # Secret value masked: only last 4 chars retained.
    assert "sk-verysecretverysecret1234" not in out
    assert secret["raw"]["api_key"].startswith("****")
    assert secret["raw"]["api_key"].endswith("1234")


def test_modes_list_show_secrets_unmasks(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        'version = "2.0"\n'
        "[modes.secret_mode]\n"
        'provider = "openai"\n'
        'model = "o3"\n'
        'api_key = "sk-verysecretverysecret1234"\n'
    )
    rc = modes_command("list", ["--json", "--show-secrets"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sk-verysecretverysecret1234" in out


def test_modes_list_source_filter_user(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        'version = "2.0"\n[modes.my_brief]\nprovider = "openai"\nmodel = "gpt-4o-mini"\n'
    )
    rc = modes_command("list", ["--json", "--source", "user"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    names = {m["name"] for m in data["modes"]}
    assert names == {"my_brief"}


def test_modes_list_source_filter_overridden(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('version = "2.0"\n[modes.deep_research]\nparallel = false\n')
    rc = modes_command("list", ["--json", "--source", "overridden"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert [m["name"] for m in data["modes"]] == ["deep_research"]


def test_modes_list_invalid_source_returns_2(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = modes_command("list", ["--source", "bogus"])
    assert rc == 2


def test_modes_detail_unknown_name_returns_0(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Q5-A row 11.i: --name X with no match is empty intersection, exit 0
    (not an error). JSON form returns `{"mode": null}`."""
    rc = modes_command("list", ["--name", "no_such_mode", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["mode"] is None


def test_modes_detail_builtin(isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = modes_command("list", ["--name", "default"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Mode: default" in out
    assert "Source: builtin" in out
    assert "Model: gpt-5.6-sol" in out
    assert "Kind: immediate" in out


def test_modes_detail_overridden_shows_diff(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('version = "2.0"\n[modes.deep_research]\nparallel = false\n')
    rc = modes_command("list", ["--name", "deep_research"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "parallel" in out
    assert "True" in out and "False" in out


def test_modes_detail_truncates_system_prompt_without_full(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = modes_command("list", ["--name", "deep_research"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "use --full" in out


def test_modes_detail_full_dumps_system_prompt(
    isolated_doxa_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = modes_command("list", ["--name", "deep_research", "--full"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "comprehensive research with citations" in out


def test_doxa_modes_subprocess_lists_modes(isolated_doxa_home: Path) -> None:
    """PR2 (Q5-A row 5): bare `doxa modes` exits 2; canonical leaf is
    `doxa modes list`."""
    rc, out, err = run_doxa(["modes", "list"])
    assert rc == 0, f"stderr: {err}"
    assert "default" in out
    assert "deep_research" in out


def test_doxa_help_modes_subprocess(isolated_doxa_home: Path) -> None:
    rc, out, err = run_doxa(["help", "modes"])
    assert rc == 0, f"stderr: {err}"
    assert "doxa modes" in out


def test_help_epilog_lists_mode_names(
    isolated_doxa_home: Path,
) -> None:
    rc, out, err = run_doxa(["--help"])
    assert rc == 0, f"stderr: {err}"
    assert "doxa modes" in out
    # Teaser still shows at least one mode name.
    assert "default" in out


def test_interactive_and_help_use_list_all_modes(
    isolated_doxa_home: Path,
) -> None:
    """Regression: direct `BUILTIN_MODES` iteration for LISTING (not validation)
    should no longer exist in interactive.py or help.py. Both must go through
    `list_all_modes()`.

    Note: validation in interactive.py (`if mode in BUILTIN_MODES`) and the
    teaser in help.py (`', '.join(BUILTIN_MODES.keys())`) remain intentionally.
    What we forbid is PER-MODE ITERATION — `for ... in BUILTIN_MODES.items()`
    or an equivalent listing loop — anywhere in those files.
    """
    from pathlib import Path as _Path

    interactive_src = _Path("src/doxa_research/interactive.py").read_text()
    help_src = _Path("src/doxa_research/help.py").read_text()

    # Forbidden: iterating BUILTIN_MODES.items() for listing
    assert "BUILTIN_MODES.items()" not in interactive_src, (
        "interactive.py should route listings through list_all_modes()"
    )
    assert "BUILTIN_MODES.items()" not in help_src, (
        "help.py should not iterate BUILTIN_MODES.items() — removed in Task 9"
    )
    # interactive.py should reference list_all_modes now
    assert "list_all_modes" in interactive_src


def test_doxa_modes_subprocess_json_flag_reaches_subcommand(
    isolated_doxa_home: Path,
) -> None:
    """PR2 canonical: `doxa modes list --json` (legacy `doxa modes --json`
    is gated to exit 2 with a migration hint per Q6-PR2-C1).

    P16 PR3 T12 promotes `--json` to the canonical envelope contract
    (`{"status": "ok", "data": {schema_version, modes}}`).
    """
    rc, out, err = run_doxa(["modes", "list", "--json"])
    assert rc == 0, f"stderr: {err}"
    payload = json.loads(out)
    assert payload["status"] == "ok"
    data = payload["data"]
    assert data["schema_version"] == "1"
    assert len(data["modes"]) >= 10
    thinking = next(m for m in data["modes"] if m["name"] == "thinking")
    assert thinking["kind"] == "immediate"
    assert thinking["model"] == "gpt-5.6-sol"


def test_doxa_modes_subprocess_name_flag(isolated_doxa_home: Path) -> None:
    rc, out, err = run_doxa(["modes", "list", "--name", "thinking"])
    assert rc == 0, f"stderr: {err}"
    assert "Mode: thinking" in out
    assert "Model: gpt-5.6-sol" in out
    assert "Kind: immediate" in out


def test_doxa_modes_subprocess_source_filter(isolated_doxa_home: Path) -> None:
    rc, out, err = run_doxa(["modes", "list", "--source", "builtin", "--json"])
    assert rc == 0, f"stderr: {err}"
    payload = json.loads(out)
    sources = {m["source"] for m in payload["data"]["modes"]}
    assert sources == {"builtin"}
