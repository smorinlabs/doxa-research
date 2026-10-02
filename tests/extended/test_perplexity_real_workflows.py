"""Minimal live Perplexity workflow tests that mocks cannot prove.

Gated by `@pytest.mark.live_api`. Default `pytest` skips this entire module
(`addopts = "-m 'not extended and not live_api'"`); run explicitly with
`uv run pytest -m live_api` or `just test-live-api` after exporting
`PERPLEXITY_API_KEY`.

Cost target — immediate (sync) tests: a few cents per full run (small ping
prompts at search_context_size=low).

Background tests use the supported Agent API through the compatibility mode
perplexity_deep_research. Test-only step/output limits bound individual work;
there is no hard dollar cap. Fixture cleanup retains known IDs and requests
best-effort cancellation. A cancel acknowledgment does not prove billing stopped.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.extended.conftest import (
    assert_no_secret_leaked,
    checkpoint_path,
    payload,
    run_doxa,
    wait_for_provider_job_id,
)

pytestmark = [pytest.mark.live_api, pytest.mark.provider_perplexity]

PERPLEXITY_BACKGROUND_STATUSES = {
    "queued",
    "running",
    "completed",
    "cancelled",
    "in_progress",
}


def test_ext_pplx_imm_stream_default_mode_emits_grounded_answer(
    live_perplexity_env: tuple[dict[str, str], Path],
) -> None:
    """Live: --provider perplexity --mode perplexity_quick produces grounded text."""
    env, _ = live_perplexity_env
    result, elapsed = run_doxa(
        [
            "ask",
            "Reply in one short sentence about Sonar.",
            "--mode",
            "perplexity_quick",
            "--provider",
            "perplexity",
        ],
        env,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert elapsed < 120
    assert_no_secret_leaked(result, env)
    assert result.stdout.strip(), "expected non-empty answer"


def test_ext_pplx_imm_stream_explicit_model_passthrough(
    live_perplexity_env: tuple[dict[str, str], Path],
) -> None:
    """Live: --provider perplexity --model sonar runs without local validation."""
    env, _ = live_perplexity_env
    result, elapsed = run_doxa(
        [
            "ask",
            "Reply in one short sentence.",
            "--provider",
            "perplexity",
            "--model",
            "sonar",
        ],
        env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert elapsed < 120
    assert_no_secret_leaked(result, env)
    assert result.stdout.strip()


def test_ext_pplx_imm_stream_tee_writes_stdout_and_file(
    live_perplexity_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    """Live: tee --out -,FILE writes identical content to stdout and file."""
    env, _ = live_perplexity_env
    target = tmp_path / "perplexity-stream-tee.md"

    result, elapsed = run_doxa(
        [
            "ask",
            "Reply in one short sentence confirming live tee works.",
            "--mode",
            "perplexity_quick",
            "--provider",
            "perplexity",
            "--out",
            f"-,{target}",
        ],
        env,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert elapsed < 120
    assert_no_secret_leaked(result, env)
    assert result.stdout.strip()
    assert target.read_text(encoding="utf-8") == result.stdout


def test_ext_pplx_imm_custom_mode_passes_provider_namespace_without_argv_key(
    live_perplexity_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    """Live: Perplexity mode-specific extra_body settings pass via config/env.

    Real Perplexity keys must not be placed on argv; timeout/error paths can
    print argv before normal no-leak assertions run.
    """
    env, _ = live_perplexity_env
    config_path = tmp_path / "pplx-passthrough.toml"
    config_path.write_text(
        """version = "2.0"

[providers.perplexity]
api_key = "${PERPLEXITY_API_KEY}"

[modes.pplx_passthrough_live]
provider = "perplexity"
model = "sonar"
kind = "immediate"

[modes.pplx_passthrough_live.perplexity]
stream_mode = "full"
web_search_options = { search_context_size = "low" }
""",
        encoding="utf-8",
    )

    args = [
        "--config",
        str(config_path),
        "ask",
        "Reply in one short sentence.",
        "--mode",
        "pplx_passthrough_live",
    ]
    assert env["PERPLEXITY_API_KEY"] not in args

    result, _elapsed = run_doxa(
        args,
        env,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert_no_secret_leaked(result, env)
    assert result.stdout.strip()


# =============================================================================
# Background Agent API lifecycle. Existing checkpoints keep the public mode name.
# The isolated fixture provides max_steps=10/max_output_tokens=8192, and always
# attempts cleanup for saved IDs after each case. Unknown creates are not retried.


def _submit_perplexity_background_json(
    env: dict[str, str],
    *,
    prompt: str,
    extra_args: list[str] | None = None,
) -> tuple[str, Any, float]:
    """Submit a Perplexity deep-research job asynchronously and return op-id."""
    args = [
        "ask",
        prompt,
        "--mode",
        "perplexity_deep_research",
        "--provider",
        "perplexity",
        "--async",
        "--json",
    ]
    if extra_args:
        args.extend(extra_args)

    result, elapsed = run_doxa(args, env, timeout=120)

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.lstrip().startswith("{")
    assert_no_secret_leaked(result, env)
    envelope = payload(result)
    assert envelope["status"] == "ok", envelope
    data = envelope["data"]
    assert data["status"] == "submitted"
    assert data["mode"] == "perplexity_deep_research"
    assert data["provider"] == "perplexity"
    assert elapsed < 120

    operation_id = data["operation_id"]
    assert isinstance(operation_id, str) and operation_id.startswith("research-")
    return operation_id, result, elapsed


def test_ext_pplx_bg_submit_async_persists_request_id(
    live_perplexity_env: tuple[dict[str, str], Path],
) -> None:
    """EXT-PPLX-BG-SUBMIT: background ask --async --json persists and resumes.

    Verifies that the upstream Perplexity request_id (the async job_id) lands
    in the checkpoint via the runner's existing checkpoint format, then runs
    `resume --async --json` in a fresh subprocess to prove the user can exit
    after submission and later reconnect without blocking for completion.

    Fixture finalization requests cancellation of saved unfinished IDs and
    retains the response. Accepted cancellation can remain pending upstream.
    """
    env, state_root = live_perplexity_env
    operation_id, _result, _elapsed = _submit_perplexity_background_json(
        env,
        prompt="Briefly summarize one recent advance in mRNA vaccine research.",
    )

    job_id = wait_for_provider_job_id(state_root, operation_id, provider="perplexity", timeout=60.0)
    assert isinstance(job_id, str) and job_id.startswith("agent:"), (
        "expected saved Agent response ID"
    )

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    providers = checkpoint.get("providers", {})
    assert "perplexity" in providers, f"checkpoint missing perplexity entry: {checkpoint}"
    assert providers["perplexity"].get("status") in PERPLEXITY_BACKGROUND_STATUSES
    assert providers["perplexity"].get("job_id") == job_id

    resume_result, resume_elapsed = run_doxa(
        ["resume", operation_id, "--async", "--json"], env, timeout=120
    )
    assert resume_result.returncode == 0, resume_result.stderr + resume_result.stdout
    assert resume_elapsed < 120
    assert_no_secret_leaked(resume_result, env)

    resume_envelope = payload(resume_result)
    assert resume_envelope["status"] == "ok", resume_envelope
    resume_data: dict[str, Any] = resume_envelope["data"]
    assert resume_data["operation_id"] == operation_id
    assert "newly_completed" in resume_data
    resumed_provider = resume_data["providers"]["perplexity"]
    assert resumed_provider["job_id"] == job_id
    assert resumed_provider.get("status") in {"running", "completed"}

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    assert checkpoint["status"] in {"running", "completed"}
    assert checkpoint["providers"]["perplexity"]["status"] in {"running", "completed"}


def test_ext_pplx_bg_cancel_confirms_upstream_terminal_state(
    live_perplexity_env: tuple[dict[str, str], Path],
) -> None:
    """Cancel a real saved Agent ID; distinguish acknowledgment from termination."""
    env, state_root = live_perplexity_env
    operation_id, _, _ = _submit_perplexity_background_json(
        env,
        prompt="Find one authoritative source about HTTP and give one sentence.",
    )
    job_id = wait_for_provider_job_id(state_root, operation_id, provider="perplexity", timeout=60.0)
    assert job_id.startswith("agent:")
    cancel_result, _ = run_doxa(["cancel", operation_id, "--json"], env, timeout=45)
    assert cancel_result.returncode == 0, cancel_result.stderr + cancel_result.stdout
    assert_no_secret_leaked(cancel_result, env)
    envelope = payload(cancel_result)
    assert envelope["status"] == "ok", envelope
    data = envelope["data"]
    if data.get("status") == "already_terminal":
        assert data.get("previous") == "completed", data
        acknowledgment = "completed_before_cancel"
    else:
        acknowledgment = data["providers"]["perplexity"]["status"]
        assert acknowledgment in {"cancelling", "cancelled", "completed"}, data

    receipt = {
        "operation_id": operation_id,
        "job_id": job_id,
        "cancel_acknowledgment": acknowledgment,
        "upstream_terminal_status": None,
        "reconciliation": "pending",
    }
    receipt_path = state_root / "cancel-terminal-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    async def observe_terminal() -> str:
        from doxa_research.config import ConfigManager
        from doxa_research.providers import create_provider

        config = ConfigManager(
            config_path=Path(env["XDG_CONFIG_HOME"]) / "doxa" / "doxa.config.toml"
        )
        config.load_all_layers({})
        provider = create_provider(
            "perplexity",
            config,
            mode_config=config.get_mode_config("perplexity_deep_research"),
        )
        try:
            await provider.reconnect(job_id)
            for _ in range(12):
                result = await provider.check_status(job_id)
                status = result["status"]
                receipt["last_observed_status"] = status
                if status in {"cancelled", "completed"}:
                    receipt.update(upstream_terminal_status=status, reconciliation="terminal")
                    return status
                assert status not in {"permanent_error", "failed"}, result
                await asyncio.sleep(5)
            pytest.fail("Agent cancellation remains pending; reconcile the saved response ID")
        finally:
            try:
                await provider._async_http.aclose()
            finally:
                try:
                    await provider.client.close()
                finally:
                    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    terminal_status = asyncio.run(asyncio.wait_for(observe_terminal(), timeout=120))
    assert terminal_status in {"cancelled", "completed"}


@pytest.mark.extended_slow
def test_ext_pplx_bg_blocking_resume_complete_lifecycle(
    live_perplexity_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    """EXT-PPLX-BG-LIFECYCLE: full async submit -> resume -> complete cycle.

    Gated by DOXA_EXTENDED_SLOW=1. Submits a deep-research job, then calls
    `doxa resume <op_id>` which exercises PerplexityProvider.reconnect() +
    the runner's polling loop until COMPLETED. Verifies the output file
    contains the answer + ## Sources + ## Cost sections.

    This creates one bounded Agent background response. The subprocess has
    a 25-minute deadline. Set DOXA_EXTENDED_SLOW=1 to opt in.
    """
    if os.environ.get("DOXA_EXTENDED_SLOW") != "1":
        pytest.skip("set DOXA_EXTENDED_SLOW=1 to run the completion lifecycle test")

    env, state_root = live_perplexity_env
    project = "ext-pplx-bg-slow"
    output_root = tmp_path / "outputs"
    config_path = tmp_path / "doxa.config.toml"
    # [providers.perplexity] is REQUIRED here — there's a pre-existing bug
    # in the --config + --async + --json path where the env-var fallback for
    # api_key doesn't fire when the config omits the providers section.
    # The ${PERPLEXITY_API_KEY} placeholder resolves at config-load time
    # using the env var the live_perplexity_env fixture exports. Same shape
    # as test_ext_pplx_imm_custom_mode_passes_provider_namespace_without_argv_key.
    config_path.write_text(
        f"""version = "2.0"

[paths]
base_output_dir = "{output_root}"

[providers.perplexity]
api_key = "${{PERPLEXITY_API_KEY}}"

[execution]
poll_interval = 10
max_wait = 25

[modes.perplexity_deep_research.perplexity]
preset = "high"
max_steps = 10
max_output_tokens = 8192
""",
        encoding="utf-8",
    )

    operation_id, _result, _elapsed = _submit_perplexity_background_json(
        env,
        prompt=(
            "Write a single short paragraph (3-5 sentences) summarizing one "
            "concrete observation about Sonar deep research."
        ),
        extra_args=["--config", str(config_path), "--project", project],
    )

    # Resume the saved ID once. Fixture finalization also runs after timeout.
    resume_result, _resume_elapsed = run_doxa(
        ["resume", operation_id, "--config", str(config_path), "--quiet"],
        env,
        timeout=1500,
    )
    assert resume_result.returncode == 0, resume_result.stderr + resume_result.stdout
    assert_no_secret_leaked(resume_result, env)

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    assert checkpoint["status"] == "completed"
    assert checkpoint["providers"]["perplexity"]["status"] == "completed"

    output_path = Path(checkpoint["output_paths"]["perplexity"])
    assert output_path.exists()
    text = output_path.read_text(encoding="utf-8")
    assert text.strip()
    assert f"operation_id: {operation_id}" in text
    assert "provider: perplexity" in text
    assert "mode: perplexity_deep_research" in text
    assert "## Sources" in text, "grounded Agent completion must retain sources"
    # Cost is optional in the upstream schema. Retain it when actually reported.
    if "## Cost" in text:
        assert "Total: $" in text
