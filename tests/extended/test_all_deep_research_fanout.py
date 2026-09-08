"""Live-API end-to-end test for the multi-provider `all_deep_research` mode.

Verifies that `doxa ask --mode all_deep_research "..."` fans out a single
prompt to OpenAI + Perplexity + Gemini concurrently, each running its own
Deep Research model, and writes three separate output files to disk —
one per provider — with the correct frontmatter.

This is the closest end-to-end test we have to the README's "parallel
Deep Research from one command" tagline. Earlier mock-based tests cover
the configuration and dispatch path (shape correctness), but only a real
three-provider run can verify that the per-provider namespace model
overrides land at the right upstream endpoints and the output files
actually appear under the configured directory.

Cost: one Deep Research job per provider — typically $1-$3 OpenAI,
$0.05-$0.50 Perplexity, $1-$3 Gemini. Wall-clock 5-15min per provider,
typically capped by the slowest. Gated by `@pytest.mark.extended_slow`
and requires `DOXA_EXTENDED_SLOW=1`. Runs in the weekly job, not in CI
default.

Requires all three of OPENAI_API_KEY, PERPLEXITY_API_KEY, GEMINI_API_KEY.
Skips gracefully if any is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.extended.conftest import (
    assert_no_secret_leaked,
    payload,
    require_gemini_key,
    require_openai_key,
    require_perplexity_key,
    run_doxa,
)

pytestmark = [pytest.mark.extended, pytest.mark.extended_slow]


@pytest.fixture
def all_three_keys_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Build a CLI environment that has all three real provider keys.

    Skips the test cleanly if any provider key is missing so partial-key
    contributors aren't blocked by a test they can't run.
    """
    require_openai_key()
    require_perplexity_key()
    require_gemini_key()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "PYTHONUNBUFFERED": "1",
            "COLUMNS": "200",
        }
    )
    return env, tmp_path


def test_all_deep_research_submits_three_providers_async(
    all_three_keys_env: tuple[dict[str, str], Path],
) -> None:
    """Async-submit path: `--async --json` returns one envelope with all three
    providers in `submitted` state. No file writes yet; that's the resume path.
    Gives us a fast (~30s) signal that the dispatch path is wired across the
    three providers without needing to wait for full DR completion.
    """
    env, _state_root = all_three_keys_env
    result, elapsed = run_doxa(
        [
            "ask",
            "Reply with the single word: ok.",
            "--mode",
            "all_deep_research",
            "--async",
            "--json",
        ],
        env,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert_no_secret_leaked(result, env)
    assert elapsed < 300

    envelope = payload(result)
    assert envelope["status"] == "ok", envelope
    data = envelope["data"]
    assert data["status"] == "submitted"
    assert data["mode"] == "all_deep_research"

    # Each provider should appear with a submitted job_id.
    providers = data.get("providers", {})
    expected_providers = {"openai", "perplexity", "gemini"}
    assert set(providers.keys()) == expected_providers, (
        f"expected exactly {expected_providers}, got {set(providers.keys())}"
    )
    for name, info in providers.items():
        assert info.get("status") in ("running", "submitted", "queued"), (
            f"{name} not in expected submitted state: {info}"
        )
        assert info.get("job_id"), f"{name} has no job_id: {info}"


def test_all_deep_research_blocking_lifecycle_writes_three_files(
    all_three_keys_env: tuple[dict[str, str], Path],
) -> None:
    """Full lifecycle: submit, poll to completion, three result files appear.

    This is the most expensive test in the suite. Submits to all three
    providers and waits for them all to finish (5-15 min wall-clock each;
    runs in parallel so total is the slowest provider). Verifies:
      * exit code 0
      * three result files under ./research-outputs/
      * each file's YAML frontmatter has the correct provider AND the
        per-provider Deep Research model from the namespace override
    """
    if os.environ.get("DOXA_EXTENDED_SLOW") != "1":
        pytest.skip("set DOXA_EXTENDED_SLOW=1 to run the full DR lifecycle")

    env, state_root = all_three_keys_env

    # Submit async first so we know the operation id and can poll the
    # checkpoint, then resume blocking. This mirrors the EXT-*-LIFECYCLE
    # pattern from the per-provider DR tests.
    submit_result, _elapsed = run_doxa(
        [
            "ask",
            "Briefly summarize one recent advance in distributed consensus.",
            "--mode",
            "all_deep_research",
            "--async",
            "--json",
        ],
        env,
        timeout=300,
    )
    assert submit_result.returncode == 0, submit_result.stderr + submit_result.stdout
    submit_envelope = payload(submit_result)
    operation_id = submit_envelope["data"]["operation_id"]
    assert operation_id.startswith("research-")

    # Resume blocking — wait up to 30 min per provider; the polling loop
    # writes files as each completes.
    resume_result, _resume_elapsed = run_doxa(
        ["resume", operation_id, "--quiet"],
        env,
        timeout=30 * 60,
    )
    assert resume_result.returncode == 0, resume_result.stderr + resume_result.stdout
    assert_no_secret_leaked(resume_result, env)

    # Verify the checkpoint reports all three providers completed.
    from tests.extended.conftest import checkpoint_path  # local import to keep top tidy

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    assert checkpoint["status"] == "completed", checkpoint
    for provider in ("openai", "perplexity", "gemini"):
        assert checkpoint["providers"][provider]["status"] == "completed", (
            f"{provider} did not complete: {checkpoint['providers'][provider]}"
        )

    # Each provider's output file must exist with the right model in frontmatter.
    expected_models = {
        "openai": "gpt-5.6-sol",
        "perplexity": "sonar-deep-research",
        "gemini": "deep-research-preview-04-2026",
    }
    for provider, expected_model in expected_models.items():
        output_path_str = checkpoint["output_paths"][provider]
        output_path = Path(output_path_str)
        assert output_path.exists(), f"{provider} output file missing: {output_path_str}"
        text = output_path.read_text(encoding="utf-8")
        assert text.strip(), f"{provider} output file is empty: {output_path_str}"
        assert f"provider: {provider}" in text, (
            f"{provider} frontmatter wrong; file content head:\n{text[:400]}"
        )
        assert f"model: {expected_model}" in text, (
            f"{provider} model in frontmatter doesn't match per-provider "
            f"namespace override (expected {expected_model}); file head:\n{text[:400]}"
        )
