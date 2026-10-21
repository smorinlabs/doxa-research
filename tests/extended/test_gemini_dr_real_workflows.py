"""P28 Task 15: live-API CLI workflow tests for Gemini Deep Research.

Gated by `@pytest.mark.live_api`. Default `pytest` skips this entire module
(`addopts = "-m 'not extended and not live_api'"`); run explicitly with
`uv run pytest -m live_api` or `just test-live-api` after exporting
`GEMINI_API_KEY`.

Background cases use gemini_quick_research. Each submitting case creates a
separate job. The fixture retains saved IDs and requests best-effort cleanup,
including after timeout; this is not a hard dollar cap.

All tests are gated by the `live_gemini_env` fixture which skips automatically
when GEMINI_API_KEY is not set.
"""

from __future__ import annotations

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

pytestmark = [pytest.mark.live_api, pytest.mark.provider_gemini]

GEMINI_BACKGROUND_STATUSES = {
    "queued",
    "running",
    "completed",
    "cancelled",
    "in_progress",
}


def _submit_gemini_background_json(
    env: dict[str, str],
    *,
    prompt: str,
    extra_args: list[str] | None = None,
) -> tuple[str, Any, float]:
    """Submit a Gemini Deep Research job asynchronously and return op-id."""
    args = [
        "ask",
        prompt,
        "--mode",
        "gemini_quick_research",
        "--provider",
        "gemini",
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
    assert data["mode"] == "gemini_quick_research"
    assert data["provider"] == "gemini"
    assert elapsed < 120

    operation_id = data["operation_id"]
    assert isinstance(operation_id, str) and operation_id.startswith("research-")
    return operation_id, result, elapsed


def test_ext_gem_bg_submit_async_persists_job_id(
    live_gemini_env: tuple[dict[str, str], Path],
) -> None:
    """EXT-GEM-BG-SUBMIT: background ask --async --json persists and resumes.

    Verifies the upstream Gemini interaction_id lands in the checkpoint via
    the runner's checkpoint format, then runs `resume --async --json` in a
    fresh subprocess to prove the user can exit after submission and reconnect.

    Cost: one Gemini Deep Research background job (gemini_quick_research).
    """
    env, state_root = live_gemini_env
    operation_id, _result, _elapsed = _submit_gemini_background_json(
        env,
        prompt="Briefly summarize one recent advance in LLM inference efficiency.",
    )

    job_id = wait_for_provider_job_id(state_root, operation_id, provider="gemini", timeout=60.0)
    assert isinstance(job_id, str) and job_id, "expected non-empty Gemini interaction_id"

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    providers = checkpoint.get("providers", {})
    assert "gemini" in providers, f"checkpoint missing gemini entry: {checkpoint}"
    assert providers["gemini"].get("status") in GEMINI_BACKGROUND_STATUSES
    assert providers["gemini"].get("job_id") == job_id

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
    resumed_provider = resume_data["providers"]["gemini"]
    assert resumed_provider["job_id"] == job_id
    assert resumed_provider.get("status") in {"running", "completed", "in_progress"}

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    assert checkpoint["status"] in {"running", "completed", "in_progress"}
    assert checkpoint["providers"]["gemini"]["status"] in {
        "running",
        "completed",
        "in_progress",
    }


def test_ext_gem_bg_cancel_synthetic_id_produces_useful_status(
    live_gemini_env: tuple[dict[str, str], Path],
) -> None:
    """EXT-GEM-BG-CANCEL: doxa cancel on a synthetic ID surfaces a useful status.

    cancel() IS implemented for Gemini DR (P28). This test uses a synthetic local
    checkpoint with a fake job_id to exercise the cancel path without submitting a
    real Deep Research job (no API cost beyond the cancel attempt itself).

    Expected behavior with the new cancel() impl:
      - Fresh provider (no prior jobs dict), seeds a minimal DR entry.
      - Attempts upstream interactions.cancel on the synthetic ID.
      - Upstream returns 404 (NotFoundError) → provider returns {"status": "not_found"}.
      - cancel_operation records "not_found" in providers["gemini"].
      - Local checkpoint is still marked "cancelled" regardless.

    The key assertion: cancel no longer raises NotImplementedError (no
    upstream_unsupported), and a useful provider status is surfaced rather
    than a raw exception.
    """
    env, state_root = live_gemini_env
    operation_id = "research-20260513-120000-aaaaaaaaaaaaaaaa"
    checkpoint_file = checkpoint_path(state_root, operation_id)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_file.write_text(
        json.dumps(
            {
                "id": operation_id,
                "prompt": "synthetic Gemini cancel checkpoint",
                "mode": "gemini_quick_research",
                "status": "running",
                "created_at": "2026-05-13T12:00:00",
                "updated_at": "2026-05-13T12:00:00",
                "providers": {
                    "gemini": {
                        "status": "running",
                        "job_id": "gemini-interaction-synthetic-no-upstream-cancel",
                    }
                },
                "output_paths": {},
                "error": None,
                "progress": 0.0,
                "project": None,
                "input_files": [],
                "failure_type": None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    cancel_result, cancel_elapsed = run_doxa(["cancel", operation_id, "--json"], env, timeout=45)
    assert cancel_result.returncode == 0, cancel_result.stderr + cancel_result.stdout
    assert cancel_elapsed < 45
    assert_no_secret_leaked(cancel_result, env)

    envelope = payload(cancel_result)
    assert envelope["status"] == "ok", envelope
    data: dict[str, Any] = envelope["data"]
    assert data["operation_id"] == operation_id

    # cancel() is implemented: upstream 404 on synthetic ID → not_found status
    # (NOT upstream_unsupported which would require NotImplementedError).
    # "cancelled" is also valid: Gemini may return 5xx for a synthetic ID, which
    # maps to {"status": "cancelled", "best_effort": True}.
    gemini_status = data["providers"]["gemini"]["status"]
    assert gemini_status in {"not_found", "permanent_error", "cancelled"}, (
        f"expected not_found, permanent_error, or cancelled for synthetic cancel; got {gemini_status!r}"
    )
    # The provider status must NOT be upstream_unsupported (that would mean cancel() raises
    # NotImplementedError, but cancel IS implemented in P28).
    assert gemini_status != "upstream_unsupported", (
        "cancel() is implemented for Gemini DR; upstream_unsupported means it raised "
        "NotImplementedError which is wrong"
    )

    # Local checkpoint must reflect cancelled regardless of upstream result.
    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    assert checkpoint["status"] == "cancelled"


def test_ext_gem_bg_invalid_key_useful_error(
    live_gemini_env: tuple[dict[str, str], Path],
) -> None:
    """EXT-GEM-BG-INVALID-KEY: invalid GEMINI_API_KEY produces upgrade-URL message.

    A real Gemini API call with a deliberately bad key must produce the
    user-friendly `aistudio.google.com` suggestion rather than a raw 401
    traceback. Uses gemini_quick mode (immediate, cheap) to avoid the cost
    and latency of a background submission.
    """
    env, _ = live_gemini_env
    bad_env = env.copy()
    bad_env["GEMINI_API_KEY"] = "invalid-key-live-test"
    result, _elapsed = run_doxa(
        [
            "ask",
            "hi",
            "--mode",
            "gemini_quick",
            "--provider",
            "gemini",
        ],
        bad_env,
        timeout=60,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "aistudio.google.com" in combined or "api key" in combined.lower(), (
        f"expected upgrade URL or 'api key' in output; got:\n{combined[-1000:]}"
    )


@pytest.mark.extended_slow
def test_ext_gem_bg_blocking_resume_complete_lifecycle(
    live_gemini_env: tuple[dict[str, str], Path],
) -> None:
    """EXT-GEM-BG-LIFECYCLE: full async submit -> resume -> complete cycle.

    Gated by DOXA_EXTENDED_SLOW=1. Submits a Gemini Deep Research job,
    then calls `doxa resume <op_id>` which exercises GeminiProvider.reconnect()
    + the runner's polling loop until COMPLETED. Verifies the output file
    contains the answer.

    Cost: one Gemini Deep Research background job. Typical completion time is
    a few minutes. Set DOXA_EXTENDED_SLOW=1 to opt in.
    """
    if os.environ.get("DOXA_EXTENDED_SLOW") != "1":
        pytest.skip("set DOXA_EXTENDED_SLOW=1 to run the completion lifecycle test")

    env, state_root = live_gemini_env
    operation_id, _result, _elapsed = _submit_gemini_background_json(
        env,
        prompt=(
            "Write a single short paragraph (3-5 sentences) summarizing one "
            "concrete observation about Gemini deep research capabilities."
        ),
    )

    resume_result, _resume_elapsed = run_doxa(
        ["resume", operation_id, "--quiet"],
        env,
        timeout=1500,
    )
    assert resume_result.returncode == 0, resume_result.stderr + resume_result.stdout
    assert_no_secret_leaked(resume_result, env)

    checkpoint = json.loads(checkpoint_path(state_root, operation_id).read_text())
    assert checkpoint["status"] == "completed"
    assert checkpoint["providers"]["gemini"]["status"] == "completed"

    output_path = Path(checkpoint["output_paths"]["gemini"])
    assert output_path.exists()
    text = output_path.read_text(encoding="utf-8")
    assert text.strip()
    assert f"operation_id: {operation_id}" in text
    assert "provider: gemini" in text
    assert "mode: gemini_quick_research" in text
