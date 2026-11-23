"""Regression test: `thinking` mode must be immediate (not deep-research)."""

from __future__ import annotations

from doxa_research.config import BUILTIN_MODES, is_background_mode


def test_thinking_mode_is_immediate() -> None:
    cfg = BUILTIN_MODES["thinking"]
    assert cfg["model"] == "gpt-5.6-sol", (
        "thinking mode's description claims 'quick analysis' — the model must "
        "be a non-deep-research model so it runs immediately"
    )
    assert is_background_mode(cfg) is False
