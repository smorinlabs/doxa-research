"""P18 Phase D: `mini_research` → `quick_research` rename with deprecation alias.

The old name keeps working for one minor; resolution emits a one-time
DeprecationWarning per process. Removed in v4.0.0 (future P19).
"""

from __future__ import annotations

import warnings

from doxa_research.config import BUILTIN_MODES, ConfigManager


def test_quick_research_is_a_real_builtin() -> None:
    assert "quick_research" in BUILTIN_MODES
    cfg = BUILTIN_MODES["quick_research"]
    assert "_deprecated_alias_for" not in cfg
    assert cfg["model"] == "gpt-5.6-sol"
    assert cfg["kind"] == "background"


def test_mini_research_is_an_alias_stub() -> None:
    assert "mini_research" in BUILTIN_MODES
    cfg = BUILTIN_MODES["mini_research"]
    assert cfg.get("_deprecated_alias_for") == "quick_research"


def test_get_mode_config_resolves_alias_to_target() -> None:
    cm = ConfigManager()
    cm.load_all_layers({})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = cm.get_mode_config("mini_research")
    # Resolved cfg matches quick_research
    target = BUILTIN_MODES["quick_research"]
    assert cfg["model"] == target["model"]
    assert cfg["kind"] == target["kind"]
    # Deprecation warning emitted
    deprecation_msgs = [
        str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert any("mini_research" in m and "quick_research" in m for m in deprecation_msgs), (
        f"Expected DeprecationWarning mentioning the rename; got: {deprecation_msgs}"
    )


def test_get_mode_config_target_does_not_warn() -> None:
    """Calling with the new name `quick_research` must not warn."""
    cm = ConfigManager()
    cm.load_all_layers({})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cm.get_mode_config("quick_research")
    deprecation_msgs = [
        str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert not any("mini_research" in m for m in deprecation_msgs)
