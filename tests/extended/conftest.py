"""Shared helpers for live extended provider tests."""

from __future__ import annotations

import asyncio
import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OP_ID_RE = re.compile(r"Operation ID:\s*(research-\d{8}-\d{6}-[a-f0-9]{16})")
OPENAI_BACKGROUND_MODE = {
    "provider": "openai",
    "model": "gpt-5.6-sol",
    "kind": "background",
}


# In CI (live-api.yml / extended.yml), LIVE_API_STRICT=1 turns missing-key
# skips into hard failures so a dropped/rotated secret can't silently zero
# out a provider's coverage. Local dev leaves it unset → skip preserved.
def _require_key(env_var: str, provider_label: str) -> None:
    if os.environ.get(env_var):
        return
    message = f"{env_var} is required for {provider_label} extended tests"
    if os.environ.get("LIVE_API_STRICT") == "1":
        pytest.fail(f"{message} (LIVE_API_STRICT=1)")
    else:
        pytest.skip(message)


def require_openai_key() -> None:
    _require_key("OPENAI_API_KEY", "OpenAI")


def require_perplexity_key() -> None:
    _require_key("PERPLEXITY_API_KEY", "Perplexity")


def require_gemini_key() -> None:
    _require_key("GEMINI_API_KEY", "Gemini")


@pytest.fixture
def live_cli_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Return an isolated environment for live CLI subprocesses."""
    require_openai_key()
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
    env.setdefault("UV_CACHE_DIR", str(REPO_ROOT / ".uv-cache"))
    return env, tmp_path


@pytest.fixture
def live_perplexity_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Return an isolated environment for live Perplexity CLI subprocesses."""
    require_perplexity_key()
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
    env.setdefault("UV_CACHE_DIR", str(REPO_ROOT / ".uv-cache"))
    return env, tmp_path


@pytest.fixture
def live_gemini_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Return an isolated environment for live Gemini CLI subprocesses."""
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
    env.setdefault("UV_CACHE_DIR", str(REPO_ROOT / ".uv-cache"))
    return env, tmp_path


def run_doxa(
    args: list[str],
    env: dict[str, str],
    *,
    timeout: float = 90.0,
) -> tuple[subprocess.CompletedProcess[str], float]:
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "doxa_research", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result, time.monotonic() - start


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(result.stdout)


def assert_no_secret_leaked(result: subprocess.CompletedProcess[str], env: dict[str, str]) -> None:
    for env_var in ("OPENAI_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY"):
        secret = env.get(env_var)
        if secret:
            assert secret not in result.stdout
            assert secret not in result.stderr


def assert_nonempty_file(path: Path) -> None:
    assert path.exists(), f"expected file at {path}, none found"
    assert path.stat().st_size > 0, f"file at {path} is empty"


def assert_metadata_present(
    path: Path,
    *,
    prompt_fragment: str,
    mode: str,
    provider: str,
) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"expected YAML front-matter at {path}"
    assert f"mode: {mode}" in text, f"missing mode: {mode} in {path}"
    assert f"provider: {provider}" in text, f"missing provider: {provider} in {path}"
    assert prompt_fragment in text, f"missing prompt fragment in {path}"


def assert_metadata_absent(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not text.startswith("---\n"), f"unexpected YAML front-matter at {path}"
    assert "operation_id:" not in text, f"unexpected operation_id metadata in {path}"
    assert "### Prompt" not in text, f"unexpected ### Prompt section in {path}"


def checkpoint_path(state_root: Path, operation_id: str) -> Path:
    return state_root / "state" / "doxa" / "checkpoints" / f"{operation_id}.json"


def load_checkpoint(state_root: Path, operation_id: str) -> dict[str, Any]:
    return json.loads(checkpoint_path(state_root, operation_id).read_text())


def maybe_load_checkpoint(state_root: Path, operation_id: str | None) -> dict[str, Any] | None:
    if not operation_id:
        return None
    path = checkpoint_path(state_root, operation_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def provider_job_id(state_root: Path, operation_id: str | None) -> str | None:
    checkpoint = maybe_load_checkpoint(state_root, operation_id)
    if not checkpoint:
        return None
    job_id = checkpoint.get("providers", {}).get("openai", {}).get("job_id")
    return str(job_id) if job_id else None


def operation_completed(state_root: Path, operation_id: str | None) -> bool:
    checkpoint = maybe_load_checkpoint(state_root, operation_id)
    return bool(checkpoint and checkpoint.get("status") == "completed")


def cancel_operation(operation_id: str | None, env: dict[str, str]) -> None:
    if not operation_id:
        return
    run_doxa(["cancel", operation_id, "--json"], env, timeout=45)


def cancel_openai_job_direct(job_id: str | None) -> None:
    """Best-effort cleanup for jobs whose checkpoint command path could not cancel."""
    if not job_id:
        return

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    config = ConfigManager()
    config.load_all_layers({})
    provider = create_provider("openai", config, mode_config=OPENAI_BACKGROUND_MODE)
    try:
        asyncio.run(provider.cancel(job_id))
    except Exception:
        pass


def cancel_background_operation(
    operation_id: str | None,
    env: dict[str, str],
    state_root: Path,
) -> None:
    if not operation_id or operation_completed(state_root, operation_id):
        return
    job_id = provider_job_id(state_root, operation_id)
    cancel_operation(operation_id, env)
    cancel_openai_job_direct(job_id)


def read_until_operation_id(
    proc: subprocess.Popen[str],
    *,
    timeout: float = 45.0,
) -> tuple[str, list[str]]:
    assert proc.stdout is not None
    assert proc.stderr is not None
    streams = [proc.stdout, proc.stderr]
    seen: list[str] = []
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            remaining_out, remaining_err = proc.communicate(timeout=1)
            seen.extend([remaining_out, remaining_err])
            pytest.fail(
                "doxa_research exited before printing an operation id:\n" + "".join(seen)[-2000:]
            )

        readable, _, _ = select.select(streams, [], [], 0.2)
        for stream in readable:
            line = stream.readline()
            if not line:
                continue
            seen.append(line)
            match = OP_ID_RE.search(line)
            if match:
                return match.group(1), seen

    proc.kill()
    pytest.fail("timed out waiting for operation id:\n" + "".join(seen)[-2000:])


def wait_for_provider_job_id(
    state_root: Path,
    operation_id: str,
    *,
    provider: str = "openai",
    timeout: float = 45.0,
) -> str:
    """Poll the checkpoint until the named provider records a non-empty job_id.

    P27 added the `provider` parameter — previously hardcoded to "openai" —
    so Perplexity's extended tests can wait on `providers.perplexity.job_id`.
    Default stays "openai" to keep the existing OpenAI-arc tests intact.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = checkpoint_path(state_root, operation_id)
        if path.exists():
            try:
                checkpoint = json.loads(path.read_text())
            except json.JSONDecodeError:
                time.sleep(0.1)
                continue
            job_id = checkpoint.get("providers", {}).get(provider, {}).get("job_id")
            if job_id:
                return str(job_id)
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {provider} job id in checkpoint for {operation_id}")
