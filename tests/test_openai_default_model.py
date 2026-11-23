"""Keep default immediate modes off the retiring o3 model."""

from pathlib import Path

import pytest

from doxa_research.config import BUILTIN_MODES
from doxa_research.providers.openai import OpenAIProvider


@pytest.mark.parametrize("mode", ["default", "clarification", "thinking", "openai_reasoning"])
def test_immediate_modes_use_supported_replacement(mode: str) -> None:
    assert BUILTIN_MODES[mode]["model"] == "gpt-5.6-sol"
    assert BUILTIN_MODES[mode]["kind"] == "immediate"


def test_provider_fallback_uses_supported_replacement(isolated_doxa_home: Path) -> None:
    assert OpenAIProvider(api_key="dummy").model == "gpt-5.6-sol"


def test_explicit_model_override_is_preserved(isolated_doxa_home: Path) -> None:
    assert OpenAIProvider(api_key="dummy", config={"model": "custom-model"}).model == "custom-model"
