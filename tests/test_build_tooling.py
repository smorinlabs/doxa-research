"""Build-tooling contract tests — migrated from doxa_test P07-M4-01/02.

Runs `make -n` and `just --dry-run` to pin the expected targets without
actually executing the underlying commands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=PROJECT_ROOT)
    return result.returncode, result.stdout, result.stderr


def test_make_env_check_remains_bootstrap_entrypoint() -> None:
    """P07-M4-01: `make env-check` remains the bootstrap entrypoint."""
    exit_code, stdout, stderr = _run(["make", "-n", "env-check"])
    assert exit_code == 0, f"make -n env-check failed: stdout={stdout}\nstderr={stderr}"
    assert "Checking development environment..." in stdout, (
        f"expected bootstrap banner in make env-check, got: {stdout!r}"
    )
    assert "python3 --version" in stdout, f"expected python version check, got: {stdout!r}"
    assert "uv --version" in stdout, f"expected uv version check, got: {stdout!r}"
    assert "just --version" in stdout, f"expected just version check, got: {stdout!r}"


def test_make_workflow_targets_removed_and_just_check_is_quality() -> None:
    """P07-M4-02: make workflow targets are removed and `just check` runs code-quality tools."""
    removed_targets = ["check", "fix", "test-check", "test-fix", "check-all", "fix-all", "clean"]
    for target in removed_targets:
        exit_code, stdout, stderr = _run(["make", "-n", target])
        assert exit_code != 0, f"make -n {target} unexpectedly succeeded: {stdout!r}"
        assert "No rule to make target" in stderr, (
            f"expected removed target failure for {target}: stdout={stdout!r} stderr={stderr!r}"
        )

    expectations = {
        "check": [
            "uv run ruff check src/doxa_research/ --fix",
            "uv run ty check src/doxa_research/",
        ],
        "check-all": [
            "uv run ruff check src/doxa_research/ --fix",
            "uv run ty check src/doxa_research/",
            "uv tool run ruff check doxa_test",
            "uv run --frozen ty check --python",
            "import sys; print(sys.executable)",
            "doxa_test",
        ],
    }
    for target, patterns in expectations.items():
        exit_code, stdout, stderr = _run(["just", "--dry-run", target])
        assert exit_code == 0, f"just --dry-run {target} failed: stdout={stdout}\nstderr={stderr}"
        output = stdout + stderr
        for pattern in patterns:
            assert pattern in output, f"expected {pattern!r} in just --dry-run {target}: {output!r}"
