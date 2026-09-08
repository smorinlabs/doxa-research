"""Tests for the is_background_mode helper."""

from __future__ import annotations

from doxa_research.config import is_background_mode, is_background_model


def test_explicit_async_true_overrides_missing_model() -> None:
    assert is_background_mode({"async": True}) is True


def test_explicit_async_false_overrides_deep_research_model() -> None:
    assert is_background_mode({"async": False, "model": "o3-deep-research"}) is False


def test_model_contains_deep_research_is_background() -> None:
    assert is_background_mode({"model": "o3-deep-research"}) is True


def test_model_contains_mini_deep_research_is_background() -> None:
    assert is_background_mode({"model": "o4-mini-deep-research"}) is True


def test_model_without_deep_research_is_immediate() -> None:
    assert is_background_mode({"model": "o3"}) is False


def test_missing_model_key_is_immediate() -> None:
    assert is_background_mode({}) is False


def test_model_none_is_immediate() -> None:
    assert is_background_mode({"model": None}) is False


def test_empty_model_string_is_immediate() -> None:
    assert is_background_mode({"model": ""}) is False


def test_substring_check_is_case_sensitive() -> None:
    # "deep-research" is lowercase by convention for OpenAI models; the check
    # is intentionally case-sensitive so "Deep-Research" does NOT match.
    assert is_background_mode({"model": "o3-Deep-Research"}) is False


def test_is_background_model_none_is_false() -> None:
    assert is_background_model(None) is False


def test_is_background_model_empty_string_is_false() -> None:
    assert is_background_model("") is False


def test_is_background_model_plain_o3_is_false() -> None:
    assert is_background_model("o3") is False


def test_is_background_model_deep_research_is_true() -> None:
    assert is_background_model("o3-deep-research") is True


def test_is_background_model_mini_deep_research_is_true() -> None:
    assert is_background_model("o4-mini-deep-research") is True


def test_is_background_model_is_case_sensitive() -> None:
    assert is_background_model("o3-Deep-Research") is False


def test_is_background_mode_truthy_async_is_background() -> None:
    assert is_background_mode({"async": 1}) is True
    assert is_background_mode({"async": "yes"}) is True


def test_is_background_mode_falsy_async_is_immediate() -> None:
    assert is_background_mode({"async": 0, "model": "o3-deep-research"}) is False
    assert is_background_mode({"async": "", "model": "o3-deep-research"}) is False


def test_is_background_model_gpt_5_6_sol_is_true() -> None:
    """Registry entry: the replacement model's ID encodes no capability."""
    assert is_background_model("gpt-5.6-sol") is True


def test_is_background_model_other_gpt5_is_false() -> None:
    """Only registered IDs qualify; the gpt-5 family at large does not."""
    assert is_background_model("gpt-5.4") is False


def test_supports_temperature_matrix() -> None:
    """Verified live 2026-09-08; rejection does not follow the model family.

    gpt-5 and gpt-5.5 reject temperature while gpt-5.1, gpt-5.2 and gpt-5.4
    accept it, so no `gpt-5*` prefix rule is correct.
    """
    from doxa_research.config import supports_temperature

    for accepts in ("gpt-4.1-mini", "gpt-4o", "gpt-5.1", "gpt-5.2", "gpt-5.4"):
        assert supports_temperature(accepts) is True, accepts
    for rejects in ("o3", "o1", "gpt-5", "gpt-5.5", "gpt-5.6-sol", "gpt-6-astra"):
        assert supports_temperature(rejects) is False, rejects
    # Unknown models default to sending it: a loud API error beats silently
    # discarding a sampling setting the user configured.
    assert supports_temperature("some-future-model") is True
    assert supports_temperature(None) is True
