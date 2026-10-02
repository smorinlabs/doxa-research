"""P20: Live-API workflow regression suite.

Slim scope (TS03-TS08): real-API CLI behaviors that mocks cannot catch across
OpenAI, Perplexity, and Gemini immediate modes — streaming, file output,
append, no-metadata, secret masking, and mismatch defense. Sibling to the
`extended` marker suite (`test_model_kind_runtime.py`,
`test_openai_real_workflows.py`) but distinct in cadence and purpose: this
suite watches user-visible CLI workflow drift, not model-kind contracts.

Gated by `@pytest.mark.live_api`; default `pytest` skips this
module via `addopts = "-m 'not extended and not live_api'"`. Run
explicitly with `just test-live-api` or weekly via
`.github/workflows/live-api.yml` (Saturday 7pm PDT).

Cost target: short prompts and immediate-mode calls for each provider, plus one
no-HTTP mismatch-defense test.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.extended.conftest import (
    assert_metadata_absent,
    assert_metadata_present,
    assert_no_secret_leaked,
    assert_nonempty_file,
    require_gemini_key,
    require_perplexity_key,
    run_doxa,
)

pytestmark = pytest.mark.live_api


@dataclass(frozen=True)
class ImmediateProviderCase:
    provider: str
    mode: str
    env_var: str
    api_key_flag: str
    fixture_name: str


IMMEDIATE_PROVIDER_CASES = [
    # Symmetric `*_quick` per provider — see docs/COMMANDS.md "Provider-pinned variants".
    ImmediateProviderCase(
        provider="openai",
        mode="openai_quick",
        env_var="OPENAI_API_KEY",
        api_key_flag="--api-key-openai",
        fixture_name="live_cli_env",
    ),
    ImmediateProviderCase(
        provider="perplexity",
        mode="perplexity_quick",
        env_var="PERPLEXITY_API_KEY",
        api_key_flag="--api-key-perplexity",
        fixture_name="live_perplexity_env",
    ),
    ImmediateProviderCase(
        provider="gemini",
        mode="gemini_quick",
        env_var="GEMINI_API_KEY",
        api_key_flag="--api-key-gemini",
        fixture_name="live_gemini_env",
    ),
]

PROVIDER_MARKS = {
    "openai": pytest.mark.provider_openai,
    "perplexity": pytest.mark.provider_perplexity,
    "gemini": pytest.mark.provider_gemini,
}


def _case_param(case: ImmediateProviderCase):
    return pytest.param(case, marks=[PROVIDER_MARKS[case.provider]], id=case.provider)


@pytest.fixture(params=[_case_param(case) for case in IMMEDIATE_PROVIDER_CASES])
def live_immediate_case(
    request: pytest.FixtureRequest,
) -> tuple[ImmediateProviderCase, dict[str, str], Path]:
    case = request.param
    env, state_root = request.getfixturevalue(case.fixture_name)
    return case, env, state_root


def test_immediate_streaming_smoke(
    live_immediate_case: tuple[ImmediateProviderCase, dict[str, str], Path],
) -> None:
    """P20-TS03: immediate `doxa ask` streams to stdout, no result file, no bg hints."""
    case, env, tmp_path = live_immediate_case
    result, elapsed = run_doxa(
        [
            "ask",
            "Reply with the word ok.",
            "--mode",
            case.mode,
            "--provider",
            case.provider,
        ],
        env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert elapsed < 120
    assert_no_secret_leaked(result, env)
    assert result.stdout.strip(), "expected streamed output to stdout"

    # Immediate path skips background plumbing entirely.
    assert "Operation ID:" not in result.stdout
    assert "Resume with:" not in result.stdout

    # No project / no --out → no result file written under tmp_path.
    result_files = list(tmp_path.rglob("*.md"))
    assert not result_files, f"expected no result files, found {result_files}"


def test_immediate_out_file_writes_and_silences_stdout(
    live_immediate_case: tuple[ImmediateProviderCase, dict[str, str], Path],
    tmp_path,
) -> None:
    """P20-TS04: `--out FILE` writes non-empty file; stdout suppressed."""
    case, env, _state_root = live_immediate_case
    target = tmp_path / "answer.md"
    result, _elapsed = run_doxa(
        [
            "ask",
            "Reply with the word ok.",
            "--mode",
            case.mode,
            "--provider",
            case.provider,
            "--out",
            str(target),
        ],
        env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert_no_secret_leaked(result, env)
    assert_nonempty_file(target)
    # Streamed answer goes to file, not stdout, when --out is the only sink.
    assert not result.stdout.strip(), (
        f"expected empty stdout when streaming to file; got: {result.stdout!r}"
    )


def test_append_grows_file_and_preserves_prefix(
    live_immediate_case: tuple[ImmediateProviderCase, dict[str, str], Path],
    tmp_path,
) -> None:
    """P20-TS05: `--append` grows the file; first run's prefix is preserved."""
    case, env, _state_root = live_immediate_case
    target = tmp_path / "appended.md"
    cmd = [
        "ask",
        "Reply with one short word.",
        "--mode",
        case.mode,
        "--provider",
        case.provider,
        "--out",
        str(target),
        "--append",
    ]

    r1, _ = run_doxa(cmd, env, timeout=120)
    assert r1.returncode == 0, r1.stderr + r1.stdout
    assert_no_secret_leaked(r1, env)
    assert_nonempty_file(target)
    size_after_first = target.stat().st_size
    prefix_after_first = target.read_bytes()

    r2, _ = run_doxa(cmd, env, timeout=120)
    assert r2.returncode == 0, r2.stderr + r2.stdout
    assert_no_secret_leaked(r2, env)
    size_after_second = target.stat().st_size
    assert size_after_second > size_after_first, "expected --append to grow the file"
    assert target.read_bytes().startswith(prefix_after_first), (
        "first run's content was lost — --append truncated instead of appending"
    )


def test_no_metadata_immediate_smoke(
    live_immediate_case: tuple[ImmediateProviderCase, dict[str, str], Path],
    tmp_path,
) -> None:
    """P20-TS06: `--no-metadata` is innocuous on the immediate `--out` path.

    Note: in immediate mode without `--project`, `OutputManager` is not
    invoked, so YAML metadata never appears in `--out` files regardless of
    this flag. This test asserts the flag doesn't break the path and locks
    in metadata-free output as a regression guard.
    """
    case, env, _state_root = live_immediate_case
    target = tmp_path / "no-metadata.md"
    result, _ = run_doxa(
        [
            "ask",
            "Reply with one short word.",
            "--mode",
            case.mode,
            "--provider",
            case.provider,
            "--out",
            str(target),
            "--no-metadata",
        ],
        env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert_no_secret_leaked(result, env)
    assert_nonempty_file(target)
    assert_metadata_absent(target)


def test_cli_api_key_does_not_leak(
    live_immediate_case: tuple[ImmediateProviderCase, dict[str, str], Path],
) -> None:
    """P20-TS07: provider CLI API-key flags work without env var and do not echo."""
    case, env, _state_root = live_immediate_case
    secret = env[case.env_var]
    env_no_key = env.copy()
    env_no_key.pop(case.env_var, None)

    result, _ = run_doxa(
        [
            "ask",
            "Reply with the word ok.",
            "--mode",
            case.mode,
            "--provider",
            case.provider,
            case.api_key_flag,
            secret,
        ],
        env_no_key,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip(), "expected streamed output to stdout"
    # `assert_no_secret_leaked` reads the secret from `env`; here we passed
    # `env_no_key` which has it stripped, so check explicitly.
    assert secret not in result.stdout, "API key leaked to stdout"
    assert secret not in result.stderr, "API key leaked to stderr"


def test_combined_report_live_all_providers(
    live_cli_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    """Live all-provider smoke: --combined writes provider files plus combined report.

    This intentionally has no provider-specific marker. It requires OpenAI,
    Perplexity, and Gemini together, so provider-scoped live targets should
    not pick it up independently.
    """
    require_perplexity_key()
    require_gemini_key()
    env, _state_root = live_cli_env
    output_root = tmp_path / "outputs"
    config_path = tmp_path / "combined-live.toml"
    config_path.write_text(
        f"""version = "2.0"

[paths]
base_output_dir = "{output_root}"

[providers.openai]
api_key = "${{OPENAI_API_KEY}}"

[providers.perplexity]
api_key = "${{PERPLEXITY_API_KEY}}"

[providers.gemini]
api_key = "${{GEMINI_API_KEY}}"

[execution]
poll_interval = 1
max_wait = 5

[modes.live_combined_smoke]
kind = "background"
providers = ["openai", "perplexity", "gemini"]
system_prompt = "Answer tersely."

[modes.live_combined_smoke.openai]
kind = "immediate"
model = "o3"

[modes.live_combined_smoke.perplexity]
kind = "immediate"
model = "sonar"
web_search_options = {{ search_context_size = "low" }}

[modes.live_combined_smoke.gemini]
kind = "immediate"
model = "gemini-2.5-flash-lite"
thinking_budget = 0
""",
        encoding="utf-8",
    )

    result, elapsed = run_doxa(
        [
            "ask",
            "Reply with one short sentence confirming this combined live smoke ran.",
            "--mode",
            "live_combined_smoke",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_root),
            "--combined",
        ],
        env,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert elapsed < 180
    assert_no_secret_leaked(result, env)

    provider_paths = {
        "openai": list(output_root.glob("*_live_combined_smoke_openai_*.md")),
        "perplexity": list(output_root.glob("*_live_combined_smoke_perplexity_*.md")),
        "gemini": list(output_root.glob("*_live_combined_smoke_gemini_*.md")),
    }
    for provider, paths in provider_paths.items():
        assert len(paths) == 1, f"expected one {provider} output file, found {paths}"
        assert_nonempty_file(paths[0])
        assert_metadata_present(
            paths[0],
            prompt_fragment="combined live smoke",
            mode="live_combined_smoke",
            provider=provider,
        )

    combined_paths = list(output_root.glob("*_live_combined_smoke_combined_*.md"))
    assert len(combined_paths) == 1, f"expected one combined output file, found {combined_paths}"
    combined_path = combined_paths[0]
    assert_nonempty_file(combined_path)
    assert_metadata_present(
        combined_path,
        prompt_fragment="combined live smoke",
        mode="live_combined_smoke",
        provider="combined",
    )
    combined_text = combined_path.read_text(encoding="utf-8")
    assert "# Combined Research Report:" in combined_text
    for heading in ("## Openai Results", "## Perplexity Results", "## Gemini Results"):
        assert heading in combined_text


@pytest.mark.provider_openai
def test_mismatch_defense_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """P20-TS08: immediate-declared deep-research model raises pre-HTTP.

    Constructs a real `OpenAIProvider` with a deliberately mismatched
    `mode_config` (`kind="immediate"` but a background-only model) and asserts
    `submit()` raises `ModeKindMismatchError` before any HTTP request.

    Uses a fake API key for provider construction; the mismatch check
    fires before the key is ever sent to OpenAI.
    """
    import httpx2

    from doxa_research.config import ConfigManager, requires_background_submission
    from doxa_research.errors import ModeKindMismatchError
    from doxa_research.providers import create_provider
    from doxa_research.providers.openai import OpenAIProvider

    requests: list[httpx2.Request] = []

    async def forbid_http(self, request, *args, **kwargs):
        requests.append(request)
        pytest.fail("Mismatch validation attempted an HTTP request")

    monkeypatch.setattr(httpx2.AsyncClient, "send", forbid_http)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-mismatch-defense-key")

    # This retired ID remains a local validation fixture. Registry background
    # defaults such as gpt-5.6-sol can also run immediately and are not mismatches.
    bg_model = "o3-deep-research"
    assert requires_background_submission(bg_model)
    cm = ConfigManager()
    cm.load_all_layers({})
    mode_config = {"provider": "openai", "model": bg_model, "kind": "immediate"}
    provider = create_provider("openai", cm, mode_config=mode_config)
    assert isinstance(provider, OpenAIProvider)

    async def submit_and_close() -> None:
        try:
            with pytest.raises(ModeKindMismatchError) as exc:
                await provider.submit("ping", mode="_mismatch_check_")
            assert exc.value.model == bg_model
            assert exc.value.declared_kind == "immediate"
            assert exc.value.required_kind == "background"
        finally:
            await provider.client.close()

    asyncio.run(submit_and_close())
    assert not requests
    assert provider.client.is_closed()
