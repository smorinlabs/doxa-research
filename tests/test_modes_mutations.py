"""Tests for doxa modes mutation commands (P12)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_parse_target_flags_defaults() -> None:
    from doxa_research.modes_cmd import _parse_target_flags

    flags, remaining, rc = _parse_target_flags([])
    assert rc == 0
    assert flags.project is False
    assert flags.config_path is None
    assert flags.profile is None
    assert flags.from_profile is None
    assert flags.force_string is False
    assert flags.override is False
    assert remaining == []


def test_parse_target_flags_project_success() -> None:
    from doxa_research.modes_cmd import _parse_target_flags

    flags, remaining, rc = _parse_target_flags(
        [
            "alpha",
            "--project",
            "--profile",
            "dev",
            "--from-profile",
            "ci",
            "--string",
            "--override",
            "beta",
            "--model",
            "gpt-4o-mini",
        ]
    )
    assert rc == 0
    assert flags.project is True
    assert flags.config_path is None
    assert flags.profile == "dev"
    assert flags.from_profile == "ci"
    assert flags.force_string is True
    assert flags.override is True
    assert remaining == ["alpha", "beta", "--model", "gpt-4o-mini"]


def test_parse_target_flags_config_success() -> None:
    from doxa_research.modes_cmd import _parse_target_flags

    flags, remaining, rc = _parse_target_flags(["alpha", "--config", "/tmp/x.toml", "beta"])
    assert rc == 0
    assert flags.project is False
    assert flags.config_path == "/tmp/x.toml"
    assert remaining == ["alpha", "beta"]


def test_parse_target_flags_project_config_conflict() -> None:
    from doxa_research.modes_cmd import _parse_target_flags

    flags, remaining, rc = _parse_target_flags(["--project", "--config", "/tmp/x.toml"])
    assert rc == 2
    # rc=2 signals USAGE_ERROR; the caller is responsible for the error
    # message (the Click wrapper emits structured JSON errors when needed).


def test_parse_target_flags_override_without_profile_allowed() -> None:
    from doxa_research.modes_cmd import _parse_target_flags

    flags, remaining, rc = _parse_target_flags(["--override"])
    assert rc == 0
    assert flags.override is True
    assert flags.profile is None
    # Operation-specific parsers decide whether --override is accepted.
    # P12 accepts it for add/copy and rejects it for set/unset/remove/rename.


def test_op_specs_registry_starts_empty() -> None:
    """The registry is populated by per-command tasks (4-9). Task 3 lays
    the type only — entries land later."""
    from doxa_research.modes_cmd import _OP_SPECS, _ModesOpSpec

    assert isinstance(_OP_SPECS, dict)
    # Per-command tasks register their specs; at infra-task time the
    # registry can be empty or partial — we only check the type.
    for spec in _OP_SPECS.values():
        assert isinstance(spec, _ModesOpSpec)


def test_resolve_write_target_default(isolated_doxa_home: Path) -> None:
    from doxa_research.modes_cmd import _resolve_write_target, _TargetFlags

    flags = _TargetFlags()
    context, err = _resolve_write_target(flags, config_path=None)
    assert err is None
    assert context is not None
    assert context.target_path.name == "doxa.config.toml"


def test_resolve_write_target_project_config_conflict() -> None:
    from doxa_research.modes_cmd import _resolve_write_target, _TargetFlags

    flags = _TargetFlags(project=True, config_path="/tmp/x.toml")
    context, err = _resolve_write_target(flags, config_path=None)
    assert context is None
    assert err is not None
    assert err["error"] == "PROJECT_CONFIG_CONFLICT"


def test_check_builtin_guard_refuses_builtin_for_add_without_override() -> None:
    from doxa_research.modes_cmd import _check_builtin_guard

    err = _check_builtin_guard("deep_research", override=False, op_name="add")
    assert err is not None
    assert err["error"] == "BUILTIN_NAME_RESERVED"


def test_check_builtin_guard_allows_builtin_for_add_with_override() -> None:
    from doxa_research.modes_cmd import _check_builtin_guard

    assert _check_builtin_guard("deep_research", override=True, op_name="add") is None


def test_check_builtin_guard_refuses_builtin_for_remove_regardless_of_override() -> None:
    """`remove` and `rename` builtin guards are absolute — `--override`
    doesn't bypass them. Only `add` and `copy` (DST-side) honor override."""
    from doxa_research.modes_cmd import _check_builtin_guard

    err = _check_builtin_guard("deep_research", override=True, op_name="remove")
    assert err is not None
    assert err["error"] == "BUILTIN_NAME_RESERVED"


def test_check_override_strict_rejects_nonbuiltin_with_override() -> None:
    """BQ resolution: `--override` on a non-builtin name is USAGE_ERROR
    (the flag is the explicit shadow opt-in, not a no-op modifier)."""
    from doxa_research.modes_cmd import _check_override_strict

    err = _check_override_strict("my_brief", override=True, op_name="add")
    assert err is not None
    assert err["error"] == "USAGE_ERROR"


def test_check_override_strict_allows_nonbuiltin_without_override() -> None:
    from doxa_research.modes_cmd import _check_override_strict

    assert _check_override_strict("my_brief", override=False, op_name="add") is None


def test_parse_modes_args_unknown_op_returns_usage_error() -> None:
    from doxa_research.modes_cmd import parse_modes_args

    parsed_kwargs, target_flags, err = parse_modes_args("nonexistent_op", [])
    assert err is not None
    assert err["error"] == "USAGE_ERROR"
    assert "unknown" in err["message"].lower()


def test_parse_modes_args_with_temp_spec_validates_arity(monkeypatch) -> None:
    """Coverage for parse_modes_args's positional-arity check using a
    scratch spec, independent of any per-command task registration."""
    from doxa_research.modes_cmd import _OP_SPECS, _ModesOpSpec, parse_modes_args

    monkeypatch.setitem(
        _OP_SPECS,
        "_test_op",
        _ModesOpSpec(
            name="_test_op",
            positionals=("NAME",),
            op_flags={"--model": "model"},
            required_op_flags=frozenset({"model"}),
        ),
    )
    # No positional args — arity mismatch
    _, _, err = parse_modes_args("_test_op", ["--model", "x"])
    assert err is not None
    assert err["error"] == "USAGE_ERROR"
    assert "NAME" in err["message"]


def test_parse_modes_args_with_temp_spec_validates_required_op_flag(monkeypatch) -> None:
    """Coverage for required-op-flags missing branch."""
    from doxa_research.modes_cmd import _OP_SPECS, _ModesOpSpec, parse_modes_args

    monkeypatch.setitem(
        _OP_SPECS,
        "_test_op",
        _ModesOpSpec(
            name="_test_op",
            positionals=("NAME",),
            op_flags={"--model": "model"},
            required_op_flags=frozenset({"model"}),
        ),
    )
    # Has positional, missing required --model
    _, _, err = parse_modes_args("_test_op", ["alpha"])
    assert err is not None
    assert err["error"] == "USAGE_ERROR"
    assert "model" in err["message"]


def test_parse_modes_args_rejects_from_profile_when_not_accepted(monkeypatch) -> None:
    """Coverage for per-spec gating of --from-profile."""
    from doxa_research.modes_cmd import _OP_SPECS, _ModesOpSpec, parse_modes_args

    monkeypatch.setitem(
        _OP_SPECS,
        "_test_op",
        _ModesOpSpec(
            name="_test_op",
            positionals=(),
            op_flags={},
            required_op_flags=frozenset(),
            accepts_from_profile=False,
        ),
    )
    _, _, err = parse_modes_args("_test_op", ["--from-profile", "dev"])
    assert err is not None
    assert err["error"] == "USAGE_ERROR"
    assert "from-profile" in err["message"]


def test_parse_modes_args_rejects_override_when_not_accepted(monkeypatch) -> None:
    """Coverage for per-spec gating of --override."""
    from doxa_research.modes_cmd import _OP_SPECS, _ModesOpSpec, parse_modes_args

    monkeypatch.setitem(
        _OP_SPECS,
        "_test_op",
        _ModesOpSpec(
            name="_test_op",
            positionals=(),
            op_flags={},
            required_op_flags=frozenset(),
            accepts_override=False,
        ),
    )
    _, _, err = parse_modes_args("_test_op", ["--override"])
    assert err is not None
    assert err["error"] == "USAGE_ERROR"
    assert "override" in err["message"]


def test_parse_modes_args_op_flag_missing_value(monkeypatch) -> None:
    """Coverage for `--flag requires a value` branch."""
    from doxa_research.modes_cmd import _OP_SPECS, _ModesOpSpec, parse_modes_args

    monkeypatch.setitem(
        _OP_SPECS,
        "_test_op",
        _ModesOpSpec(
            name="_test_op",
            positionals=("NAME",),
            op_flags={"--model": "model"},
            required_op_flags=frozenset(),
        ),
    )
    _, _, err = parse_modes_args("_test_op", ["alpha", "--model"])
    assert err is not None
    assert err["error"] == "USAGE_ERROR"
    assert "--model" in err["message"]


# ---------------------------------------------------------------------------
# TS01a-j — `doxa modes add` (P12 Task 4)
# ---------------------------------------------------------------------------


def test_add_happy_path_creates_mode(isolated_doxa_home: Path) -> None:  # TS01a
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    assert rc == 0

    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    text = cfg.read_text()
    assert "[modes.brief]" in text
    assert 'model = "gpt-4o-mini"' in text
    assert 'provider = "openai"' in text  # default
    assert 'kind = "immediate"' in text  # default


def test_add_writes_to_project_with_flag(
    isolated_doxa_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # TS01h base
    monkeypatch.chdir(tmp_path)
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini", "--project"])
    assert rc == 0
    proj_cfg = tmp_path / "doxa.config.toml"
    assert proj_cfg.exists()
    assert "[modes.brief]" in proj_cfg.read_text()


def test_add_with_provider_flag(isolated_doxa_home: Path) -> None:  # TS01b
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini", "--provider", "perplexity"])
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert 'provider = "perplexity"' in cfg.read_text()


def test_add_with_description(isolated_doxa_home: Path) -> None:  # TS01c
    from doxa_research.modes_cmd import modes_command

    rc = modes_command(
        "add",
        ["brief", "--model", "gpt-4o-mini", "--description", "terse daily"],
    )
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert 'description = "terse daily"' in cfg.read_text()


def test_add_kind_background(isolated_doxa_home: Path) -> None:  # TS01d
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini", "--kind", "background"])
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert 'kind = "background"' in cfg.read_text()


def test_add_invalid_kind_rejected(isolated_doxa_home: Path) -> None:  # TS01d (negative)
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini", "--kind", "weird"])
    assert rc == 2


def test_add_idempotent_same_model(isolated_doxa_home: Path) -> None:  # TS01e
    from doxa_research.modes_cmd import modes_command

    assert modes_command("add", ["brief", "--model", "gpt-4o-mini"]) == 0
    assert modes_command("add", ["brief", "--model", "gpt-4o-mini"]) == 0


def test_add_idempotency_ignores_other_flags(isolated_doxa_home: Path) -> None:  # TS01e (key)
    from doxa_research.modes_cmd import modes_command

    assert modes_command("add", ["brief", "--model", "gpt-4o-mini", "--description", "first"]) == 0
    # Same model, different description → still no-op (model-only idempotency).
    assert modes_command("add", ["brief", "--model", "gpt-4o-mini", "--description", "second"]) == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert 'description = "first"' in cfg.read_text()  # unchanged


def test_add_different_model_errors(isolated_doxa_home: Path) -> None:  # TS01f
    from doxa_research.modes_cmd import modes_command

    assert modes_command("add", ["brief", "--model", "gpt-4o-mini"]) == 0
    assert modes_command("add", ["brief", "--model", "gpt-5"]) == 1


def test_add_builtin_name_reserved(isolated_doxa_home: Path) -> None:  # TS01g
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["deep_research", "--model", "gpt-4o-mini"])
    assert rc == 1


def test_add_with_config_path(tmp_path: Path) -> None:  # TS01h
    from doxa_research.modes_cmd import modes_command

    target = tmp_path / "custom.toml"
    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini", "--config", str(target)])
    assert rc == 0
    assert "[modes.brief]" in target.read_text()


def test_add_project_and_config_conflict(isolated_doxa_home: Path) -> None:  # TS01h
    from doxa_research.modes_cmd import modes_command

    rc = modes_command(
        "add",
        [
            "brief",
            "--model",
            "gpt-4o-mini",
            "--project",
            "--config",
            "/tmp/x.toml",
        ],
    )
    assert rc == 2


def test_add_with_profile_overlay(isolated_doxa_home: Path) -> None:  # TS01j base
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["cheap", "--model", "gpt-4o-mini", "--profile", "dev"])
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert "[profiles.dev.modes.cheap]" in cfg.read_text()


def test_add_override_required_for_builtin(isolated_doxa_home: Path) -> None:  # TS01j
    from doxa_research.modes_cmd import modes_command

    # Without --override, even with --profile, builtin name is reserved.
    rc = modes_command("add", ["deep_research", "--model", "gpt-4o-mini", "--profile", "dev"])
    assert rc == 1

    # With --override and no --profile, writes a base-tier override.
    rc = modes_command("add", ["deep_research", "--model", "gpt-4o-mini", "--override"])
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert "[modes.deep_research]" in cfg.read_text()


def test_add_override_allows_builtin_in_profile_tier(
    isolated_doxa_home: Path,
) -> None:  # TS01j
    from doxa_research.modes_cmd import modes_command

    rc = modes_command(
        "add",
        [
            "deep_research",
            "--model",
            "gpt-4o-mini",
            "--profile",
            "dev",
            "--override",
        ],
    )
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert "[profiles.dev.modes.deep_research]" in cfg.read_text()


def test_add_override_on_nonbuiltin_rejected(
    isolated_doxa_home: Path,
) -> None:  # TS01j (strict)
    """`--override` is the builtin-shadow opt-in. Passing it on a
    non-builtin name (where there's no guard to bypass) is a USAGE_ERROR."""
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("add", ["my_brief", "--model", "gpt-4o-mini", "--override"])
    assert rc == 2


def test_add_existing_mode_with_no_model_returns_clear_error(
    isolated_doxa_home: Path,
) -> None:
    """A mode table that exists but lacks a `model` field is a degenerate
    state (mid-edit). Surface a specific error rather than the misleading
    'MODE_EXISTS_DIFFERENT_MODEL with model None'."""
    from doxa_research.config_document import ConfigDocument
    from doxa_research.modes_cmd import modes_command

    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Manually create a degenerate [modes.brief] with no model key
    doc = ConfigDocument.load(cfg)
    doc.ensure_mode("brief")
    doc.set_mode_value("brief", "description", "no model yet")
    doc.save()

    rc = modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    assert rc == 1  # MODE_EXISTS_NO_MODEL → exit 1 via _emit_human_receipt


def test_get_modes_data_from_args_filters_by_signature(
    isolated_doxa_home: Path,
) -> None:
    """The dispatcher should only spread kwargs that the data fn accepts.
    `add` doesn't accept `from_profile` or `force_string` after this fix;
    passing them via the dispatcher must not cause a TypeError."""
    from doxa_research.modes_cmd import get_modes_data_from_args

    # Add doesn't take --from-profile (rejected by parser via spec gating)
    # but the inner spread filter independently ensures the data fn isn't
    # called with kwargs it doesn't accept. Smoke-test happy path:
    data, exit_code = get_modes_data_from_args("add", ["brief", "--model", "gpt-4o-mini"])
    assert exit_code == 0
    assert data["created"] is True
    assert data["mode"] == "brief"


# ---------------------------------------------------------------------------
# TS02a-f — `doxa modes set` (P12 Task 5)
# ---------------------------------------------------------------------------


def test_set_updates_existing_user_mode(isolated_doxa_home: Path) -> None:  # TS02a
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    rc = modes_command("set", ["brief", "temperature", "0.2"])
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert "temperature = 0.2" in cfg.read_text()


def test_set_string_flag_keeps_string(isolated_doxa_home: Path) -> None:  # TS02b
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    rc = modes_command("set", ["brief", "secret_key", "12345", "--string"])
    assert rc == 0
    cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"
    assert 'secret_key = "12345"' in cfg.read_text()


def test_set_type_coercion(isolated_doxa_home: Path) -> None:  # TS02c
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    modes_command("set", ["brief", "verbose", "true"])
    modes_command("set", ["brief", "max_tokens", "1000"])
    modes_command("set", ["brief", "temperature", "0.2"])
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "verbose = true" in cfg
    assert "max_tokens = 1000" in cfg
    assert "temperature = 0.2" in cfg


def test_set_on_builtin_creates_override(isolated_doxa_home: Path) -> None:  # TS02d
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("set", ["deep_research", "parallel", "false"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.deep_research]" in cfg
    assert "parallel = false" in cfg


def test_set_absent_nonbuiltin_errors(isolated_doxa_home: Path) -> None:  # TS02e
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("set", ["missing_mode", "model", "gpt-4o-mini"])
    assert rc == 1


def test_set_overlay_via_profile(isolated_doxa_home: Path) -> None:  # TS02f
    """`set` on a non-existent name + --profile dev should be MODE_NOT_FOUND
    (because absent in the chosen tier) UNLESS the name is a builtin
    (then implicit override). For this test, use a builtin name to
    confirm overlay-tier write works."""
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("set", ["deep_research", "parallel", "false", "--profile", "dev"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[profiles.dev.modes.deep_research]" in cfg
    assert "parallel = false" in cfg


# ---------------------------------------------------------------------------
# TS03a-f — `doxa modes unset` (P12 Task 6)
# ---------------------------------------------------------------------------


def test_unset_drops_key_from_user_mode(isolated_doxa_home: Path) -> None:  # TS03a
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    modes_command("set", ["brief", "temperature", "0.2"])
    rc = modes_command("unset", ["brief", "temperature"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "temperature" not in cfg
    assert "[modes.brief]" in cfg  # mode itself remains


def test_unset_last_key_prunes_empty_table(isolated_doxa_home: Path) -> None:  # TS03b
    from doxa_research.modes_cmd import modes_command

    # Build a mode with only model + provider + kind (the defaults)
    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    modes_command("unset", ["brief", "model"])
    modes_command("unset", ["brief", "provider"])
    rc = modes_command("unset", ["brief", "kind"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    # Empty [modes.brief] should have been pruned
    assert "[modes.brief]" not in cfg


def test_unset_override_reverts_to_builtin(isolated_doxa_home: Path) -> None:  # TS03c
    """`set` on a builtin creates [modes.<builtin>] override; unset of
    every key prunes the table; `list_all_modes` then reports source=builtin again."""
    from doxa_research.config import ConfigManager
    from doxa_research.modes_cmd import list_all_modes, modes_command

    # Create override
    modes_command("set", ["deep_research", "parallel", "false"])
    cm = ConfigManager()
    cm.load_all_layers({})
    info = next(m for m in list_all_modes(cm) if m.name == "deep_research")
    assert info.source == "overridden"

    # Drop the only override key — should prune the table and revert
    modes_command("unset", ["deep_research", "parallel"])
    cm = ConfigManager()
    cm.load_all_layers({})
    info = next(m for m in list_all_modes(cm) if m.name == "deep_research")
    assert info.source == "builtin"


def test_unset_idempotent_on_absent_key(isolated_doxa_home: Path) -> None:  # TS03d
    """Absent KEY on a present mode → exit 0 with `removed: False`."""
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    rc = modes_command("unset", ["brief", "nonexistent_key"])
    assert rc == 0


def test_unset_pure_builtin_errors(isolated_doxa_home: Path) -> None:  # TS03e
    """Pure-builtin NAME (no user-side override in chosen tier) → MODE_NOT_FOUND."""
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("unset", ["deep_research", "parallel"])
    assert rc == 1


def test_unset_overlay_via_profile(isolated_doxa_home: Path) -> None:  # TS03f
    """Unset on a profile-overlay mode."""
    from doxa_research.modes_cmd import modes_command

    modes_command("set", ["deep_research", "parallel", "false", "--profile", "dev"])
    rc = modes_command("unset", ["deep_research", "parallel", "--profile", "dev"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    # The overlay table should be pruned (was the only key)
    assert "profiles.dev.modes.deep_research" not in cfg


# ---------------------------------------------------------------------------
# TS04a-e — `doxa modes remove` (P12 Task 7)
# ---------------------------------------------------------------------------


def test_remove_drops_user_only_mode(isolated_doxa_home: Path) -> None:  # TS04a
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["brief", "--model", "gpt-4o-mini"])
    rc = modes_command("remove", ["brief"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.brief]" not in cfg


def test_remove_overridden_builtin_reverts(isolated_doxa_home: Path) -> None:  # TS04b
    """`set` on builtin creates override; `remove` drops the override
    and the mode reverts to source=builtin."""
    from doxa_research.config import ConfigManager
    from doxa_research.modes_cmd import list_all_modes, modes_command

    modes_command("set", ["deep_research", "parallel", "false"])
    cm = ConfigManager()
    cm.load_all_layers({})
    info = next(m for m in list_all_modes(cm) if m.name == "deep_research")
    assert info.source == "overridden"

    rc = modes_command("remove", ["deep_research"])
    assert rc == 0
    cm = ConfigManager()
    cm.load_all_layers({})
    info = next(m for m in list_all_modes(cm) if m.name == "deep_research")
    assert info.source == "builtin"


def test_remove_pure_builtin_reserved(isolated_doxa_home: Path) -> None:  # TS04c
    """Pure-builtin (no user override) → BUILTIN_NAME_RESERVED exit 1."""
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("remove", ["deep_research"])
    assert rc == 1


def test_remove_idempotent_on_absent_nonbuiltin(isolated_doxa_home: Path) -> None:  # TS04d
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("remove", ["never_existed"])
    assert rc == 0


def test_remove_overlay_via_profile(isolated_doxa_home: Path) -> None:  # TS04e
    from doxa_research.modes_cmd import modes_command

    modes_command("set", ["deep_research", "parallel", "false", "--profile", "dev"])
    rc = modes_command("remove", ["deep_research", "--profile", "dev"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "profiles.dev.modes.deep_research" not in cfg


def test_rename_user_only_mode(isolated_doxa_home: Path) -> None:  # TS05a
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["old_name", "--model", "gpt-4o-mini"])
    assert modes_command("rename", ["old_name", "new_name"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.old_name]" not in cfg
    assert "[modes.new_name]" in cfg


def test_rename_builtin_old_reserved(isolated_doxa_home: Path) -> None:  # TS05b
    from doxa_research.modes_cmd import modes_command

    assert modes_command("rename", ["deep_research", "my_research"]) == 1


def test_rename_overridden_builtin_old_reserved(isolated_doxa_home: Path) -> None:  # TS05c
    from doxa_research.modes_cmd import modes_command

    modes_command("set", ["deep_research", "parallel", "false"])
    assert modes_command("rename", ["deep_research", "my_research"]) == 1


def test_rename_new_is_builtin_dst_taken(isolated_doxa_home: Path) -> None:  # TS05d
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["my_brief", "--model", "gpt-4o-mini"])
    assert modes_command("rename", ["my_brief", "deep_research"]) == 1


def test_rename_new_already_exists_dst_taken(isolated_doxa_home: Path) -> None:  # TS05e
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["alpha", "--model", "gpt-4o-mini"])
    modes_command("add", ["beta", "--model", "gpt-5"])
    assert modes_command("rename", ["alpha", "beta"]) == 1


def test_rename_old_absent_mode_not_found(isolated_doxa_home: Path) -> None:  # TS05f
    from doxa_research.modes_cmd import modes_command

    assert modes_command("rename", ["never_existed", "new_name"]) == 1


def test_rename_overlay_via_profile(isolated_doxa_home: Path) -> None:  # TS05g
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["old_o", "--model", "gpt-4o-mini", "--profile", "dev"])
    assert modes_command("rename", ["old_o", "new_o", "--profile", "dev"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "profiles.dev.modes.old_o" not in cfg
    assert "profiles.dev.modes.new_o" in cfg


def test_copy_base_to_base(isolated_doxa_home: Path) -> None:  # TS06g1
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    assert modes_command("copy", ["src", "dst"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.src]" in cfg
    assert "[modes.dst]" in cfg


def test_copy_builtin_src_writes_effective_config(
    isolated_doxa_home: Path,
) -> None:  # TS06a
    """SRC = builtin (deep_research, no override). DST should contain the
    builtin's keys (provider, model, kind, etc.)."""
    from doxa_research.modes_cmd import modes_command

    assert modes_command("copy", ["deep_research", "my_research"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.my_research]" in cfg
    # deep_research's model is o3-deep-research per BUILTIN_MODES
    assert 'model = "gpt-5.6-sol"' in cfg


def test_copy_user_only_src(isolated_doxa_home: Path) -> None:  # TS06b
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    assert modes_command("copy", ["src", "dst"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.src]" in cfg  # SRC unchanged
    assert "[modes.dst]" in cfg
    # DST should have SRC's keys
    assert cfg.count('model = "gpt-4o-mini"') == 2


def test_copy_overridden_src_writes_effective(
    isolated_doxa_home: Path,
) -> None:  # TS06c
    """SRC is overridden builtin: DST should get effective (builtin layered with override)."""
    from doxa_research.modes_cmd import modes_command

    # Override deep_research's `parallel` field
    modes_command("set", ["deep_research", "parallel", "false"])
    assert modes_command("copy", ["deep_research", "my_research"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.my_research]" in cfg
    # DST should have the override value, not the builtin default
    assert "parallel = false" in cfg
    # And the builtin's other keys (model)
    assert 'model = "gpt-5.6-sol"' in cfg


def test_copy_dst_builtin_without_override_dst_taken(
    isolated_doxa_home: Path,
) -> None:  # TS06d (no --override)
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    assert modes_command("copy", ["src", "deep_research"]) == 1


def test_copy_dst_builtin_with_override_succeeds(
    isolated_doxa_home: Path,
) -> None:  # TS06d (with --override)
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    assert modes_command("copy", ["src", "deep_research", "--override"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.deep_research]" in cfg
    assert 'model = "gpt-4o-mini"' in cfg  # The override value, not the builtin


def test_copy_override_on_nonbuiltin_dst_rejected(
    isolated_doxa_home: Path,
) -> None:  # TS06d (BQ-strict)
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    assert modes_command("copy", ["src", "new_dst", "--override"]) == 2


def test_copy_dst_already_exists_dst_taken(
    isolated_doxa_home: Path,
) -> None:  # TS06e
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    modes_command("add", ["dst", "--model", "gpt-5"])
    assert modes_command("copy", ["src", "dst"]) == 1


def test_copy_src_absent_mode_not_found(isolated_doxa_home: Path) -> None:  # TS06f
    from doxa_research.modes_cmd import modes_command

    assert modes_command("copy", ["never_existed", "dst"]) == 1


def test_copy_base_to_overlay(isolated_doxa_home: Path) -> None:  # TS06g2
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini"])
    assert modes_command("copy", ["src", "dst", "--profile", "dev"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.src]" in cfg
    assert "[profiles.dev.modes.dst]" in cfg


def test_copy_overlay_to_base(isolated_doxa_home: Path) -> None:  # TS06g3
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini", "--profile", "dev"])
    assert modes_command("copy", ["src", "dst", "--from-profile", "dev"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[profiles.dev.modes.src]" in cfg
    assert "[modes.dst]" in cfg


def test_copy_overlay_to_overlay_cross_profile(
    isolated_doxa_home: Path,
) -> None:  # TS06g4
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini", "--profile", "dev"])
    assert modes_command("copy", ["src", "dst", "--from-profile", "dev", "--profile", "ci"]) == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[profiles.dev.modes.src]" in cfg
    assert "[profiles.ci.modes.dst]" in cfg


def test_copy_with_project_flag(
    isolated_doxa_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # TS06g5
    monkeypatch.chdir(tmp_path)
    from doxa_research.modes_cmd import modes_command

    modes_command("add", ["src", "--model", "gpt-4o-mini", "--project"])
    assert modes_command("copy", ["src", "dst", "--project"]) == 0
    proj_cfg = tmp_path / "doxa.config.toml"
    assert proj_cfg.exists()
    assert "[modes.dst]" in proj_cfg.read_text()


def test_copy_default_builtin_handles_none_system_prompt(
    isolated_doxa_home: Path,
) -> None:  # TS06i (regression)
    """`default` builtin has system_prompt=None which tomlkit refuses to
    write. The per-key copy loop must skip None values, treating them as
    absent fields (their semantic meaning in BUILTIN_MODES)."""
    from doxa_research.modes_cmd import modes_command

    rc = modes_command("copy", ["default", "my_default"])
    assert rc == 0
    cfg = (Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml").read_text()
    assert "[modes.my_default]" in cfg
    assert "system_prompt" not in cfg


# ---------------------------------------------------------------------------
# Cross-cutting tests (TS07a, TS07b, TS07e)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op,args",
    [
        ("add", ["new_x", "--model", "gpt-4o-mini"]),
        ("set", ["x", "temperature", "0.2"]),
        ("unset", ["x", "temperature"]),
        ("remove", ["x"]),
        ("rename", ["x", "new_x"]),
        ("copy", ["x", "copy_x"]),
    ],
)
@pytest.mark.parametrize(
    "case_name,targeting",
    [
        ("user-base", []),
        ("user-profile", ["--profile", "dev"]),
        ("project-base", ["--project"]),
        ("project-profile", ["--project", "--profile", "dev"]),
        ("config-base", ["--config", "{custom_config}"]),
        ("config-profile", ["--config", "{custom_config}", "--profile", "dev"]),
    ],
)
def test_tomlkit_preserves_top_comment(  # TS07a
    isolated_doxa_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    args: list[str],
    case_name: str,
    targeting: list[str],
) -> None:
    """tomlkit round-trip preserves the file-level header comment across
    all 6 mutators × all 6 targeting combinations.

    The `add` op uses a fresh name (`new_x`) so it actually creates a
    new entry and writes to disk, exercising the tomlkit roundtrip
    just like the other 5 mutators.
    """
    from doxa_research.modes_cmd import modes_command

    custom_config = tmp_path / "custom.toml"
    resolved_targeting = [
        str(custom_config) if token == "{custom_config}" else token for token in targeting
    ]
    if "--project" in resolved_targeting:
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "doxa.config.toml"
    elif "--config" in resolved_targeting:
        cfg = custom_config
    else:
        cfg = Path(isolated_doxa_home) / "config" / "doxa" / "doxa.config.toml"

    cfg.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "# preserved comment\n"
        'version = "2.0"\n'
        "[modes.x]\n"
        'model = "gpt-4o-mini"\n'
        "temperature = 0.5\n"
    )
    if "--profile" in resolved_targeting:
        text += '\n[profiles.dev.modes.x]\nmodel = "gpt-4o-mini"\ntemperature = 0.5\n'
    cfg.write_text(text)

    rc = modes_command(op, args + resolved_targeting)
    assert rc == 0, f"op={op}, case={case_name}"
    text_after = cfg.read_text()
    assert "# preserved comment" in text_after, f"top comment lost by op={op}, case={case_name}"


def test_schema_version_constant_uniform() -> None:  # TS07b
    """SCHEMA_VERSION is the single shared constant; all 6 data functions
    are importable and callable. This is a regression tripwire: if the
    constant changes, every test calling it should be updated in lockstep.
    """
    from doxa_research.modes_cmd import (
        SCHEMA_VERSION,
        get_modes_add_data,
        get_modes_copy_data,
        get_modes_remove_data,
        get_modes_rename_data,
        get_modes_set_data,
        get_modes_unset_data,
    )

    assert SCHEMA_VERSION == "1"
    assert all(
        callable(fn)
        for fn in (
            get_modes_add_data,
            get_modes_set_data,
            get_modes_unset_data,
            get_modes_remove_data,
            get_modes_rename_data,
            get_modes_copy_data,
        )
    )


def test_layering_overlay_wins_when_active(isolated_doxa_home: Path) -> None:  # TS07e
    """When [modes.X] and [profiles.dev.modes.X] both exist:
    `get_modes_list_data` reflects base by default; profile-active reflects
    overlay. Validates that P12 mutators land in the right tier and the
    overlay reader resolves correctly across the full set of P12-introduced
    primitives.
    """
    from doxa_research.modes_cmd import get_modes_list_data, modes_command

    assert modes_command("add", ["mymode", "--model", "base-model"]) == 0
    assert modes_command("add", ["mymode", "--model", "overlay-model", "--profile", "dev"]) == 0

    base = get_modes_list_data(name="mymode", source="all", show_secrets=False)
    assert base["mode"] is not None
    assert base["mode"]["model"] == "base-model"

    overlay = get_modes_list_data(
        name="mymode",
        source="all",
        show_secrets=False,
        profile="dev",
    )
    assert overlay["mode"] is not None
    assert overlay["mode"]["model"] == "overlay-model"
