"""P18 Phase I: extended runtime contract tests.

For each model in `KNOWN_MODELS`, hit the REAL provider API and verify the
declared `kind` matches the model's actual API behavior:

  * `kind == "immediate"` → first `check_status(submit())` returns `"completed"`
    (single round-trip; no async polling required)
  * `kind == "background"` → first `check_status(submit())` returns one of
    `"running"`, `"queued"`, or `"completed"` (the latter only if the
    upstream API was very fast). For background hits, we then call
    `cancel()` as best-effort cleanup.

Gated by `@pytest.mark.extended`. Default `pytest` skips this entire
module (`addopts = "-m 'not extended and not live_api'"`); run explicitly
with `just test-extended` after exporting the required API keys.

Cost target: small prompts for all providers. Background providers with
upstream cancel stop after the kind check; providers without upstream cancel
may continue billing after the test records the live runtime behavior.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

from doxa_research.models import KNOWN_MODELS

# Skip the entire module unless explicit `extended` marker is selected. The
# `pytest.mark.extended` decorator ALSO does this, but the early skip avoids
# importing/instantiating providers when the user hasn't opted in.
pytestmark = pytest.mark.extended

PROVIDER_MARKS = {
    "openai": pytest.mark.provider_openai,
    "perplexity": pytest.mark.provider_perplexity,
    "gemini": pytest.mark.provider_gemini,
}


def _spec_param(spec):
    mark = PROVIDER_MARKS.get(spec.provider)
    marks = [] if mark is None else [mark]
    return pytest.param(spec, marks=marks, id=f"{spec.provider}/{spec.id}")


def _missing_keys_for(provider: str) -> list[str]:
    needs = {
        "openai": ["OPENAI_API_KEY"],
        "perplexity": ["PERPLEXITY_API_KEY"],
        "gemini": ["GEMINI_API_KEY"],
        "mock": [],
    }.get(provider, [])
    return [k for k in needs if not os.environ.get(k)]


def _runtime_check_skip_reason(spec) -> str | None:
    _ = spec
    return None


@pytest.mark.parametrize(
    "spec",
    [_spec_param(spec) for spec in KNOWN_MODELS],
)
def test_model_kind_matches_runtime_behavior(spec, tmp_path: Path) -> None:
    """Submit a tiny ping; assert kind contract holds against the live API."""
    skip_reason = _runtime_check_skip_reason(spec)
    if skip_reason:
        pytest.skip(skip_reason)

    missing = _missing_keys_for(spec.provider)
    if missing:
        message = f"{spec.provider}: required env vars missing: {missing}"
        if os.environ.get("LIVE_API_STRICT") == "1":
            pytest.fail(message)
        pytest.skip(message)

    from doxa_research.config import ConfigManager
    from doxa_research.providers import create_provider

    cm = ConfigManager()
    cm.load_all_layers({})

    mode_config: dict[str, Any] = {
        "provider": spec.provider,
        "model": spec.id,
        "kind": spec.kind,
    }
    if spec.provider == "perplexity" and spec.kind == "background":
        mode_config["perplexity"] = {"preset": "high", "max_steps": 10, "max_output_tokens": 8192}
    provider = create_provider(spec.provider, cm, mode_config=mode_config)

    async def _exercise() -> dict:
        job_id = None
        status = {}
        receipt_path = tmp_path / "runtime-receipt.json"
        receipt = {
            "provider": spec.provider,
            "model": spec.id,
            "kind": spec.kind,
            "logical_create_attempts": 1,
            "job_id": None,
            "submission_outcome": "unknown",
            "cleanup": None,
            "close_errors": [],
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        try:
            job_id = await provider.submit("ping", mode="_runtime_check_")
            receipt.update(job_id=job_id, submission_outcome="known_id")
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            status = await provider.check_status(job_id)
            receipt["observed_status"] = status.get("status")
            return status
        finally:
            if job_id and spec.kind == "background" and status.get("status") != "completed":
                try:
                    receipt["cleanup"] = await provider.cancel(job_id)
                except Exception as exc:
                    receipt["cleanup"] = {"status": "unknown", "error_class": type(exc).__name__}
            seen = set()
            client = getattr(provider, "client", None)
            for resource in (
                getattr(provider, "_async_http", None),
                getattr(client, "aio", None),
                client,
            ):
                if resource is None or id(resource) in seen:
                    continue
                seen.add(id(resource))
                close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
                if close is None:
                    continue
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await asyncio.wait_for(result, timeout=10)
                except Exception as exc:
                    receipt["close_errors"].append(type(exc).__name__)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    status = asyncio.run(_exercise())

    if spec.kind == "immediate":
        assert status.get("status") == "completed", (
            f"{spec.provider}/{spec.id} declared kind='immediate' but first "
            f"check_status returned {status!r} — drift between doxa_research's "
            f"KNOWN_MODELS registry and the upstream API."
        )
    else:  # background
        assert status.get("status") in ("running", "queued", "completed"), (
            f"{spec.provider}/{spec.id} declared kind='background' but first "
            f"check_status returned {status!r}; expected running/queued/completed."
        )
