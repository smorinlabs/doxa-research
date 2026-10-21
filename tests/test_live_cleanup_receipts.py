"""Saved-job cleanup distinguishes confirmed failure from cancellation uncertainty."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            {
                "status": "permanent_error",
                "upstream_status": "failed",
                "error": "Capacity exhausted",
            },
            "confirmed_terminal",
        ),
        (
            {"status": "permanent_error", "upstream_status": "incomplete", "error": "Output limit"},
            "confirmed_terminal",
        ),
        ({"status": "permanent_error", "error": "HTTP failure"}, "unknown"),
        ({"status": "permanent_error", "upstream_status": "future_status"}, "unknown"),
        ({"status": "permanent_error", "upstream_status": "in_progress"}, "unknown"),
        ({"status": "unknown", "upstream_status": "failed"}, "unknown"),
        (
            {"status": "permanent_error", "upstream_status": "failed", "best_effort": True},
            "unknown",
        ),
    ],
)
def test_cleanup_retains_terminal_failure_and_rejects_uncertain_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict, expected: str
) -> None:
    import doxa_research.config
    import doxa_research.providers

    path = Path(__file__).parent / "extended" / "conftest.py"
    spec = importlib.util.spec_from_file_location("terminal_cleanup_fixture", path)
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)

    checkpoint = fixture.checkpoint_path(tmp_path, "research-terminal-failure")
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
            {
                "id": "research-terminal-failure",
                "status": "cancelled",
                "providers": {"perplexity": {"job_id": "agent:existing-id", "status": "running"}},
            }
        )
    )
    calls = []
    closed = []

    class Config:
        def load_all_layers(self, *args):
            pass

        def get_mode_config(self, *args):
            return {"kind": "background", "provider": "perplexity"}

    class Client:
        async def close(self):
            closed.append("closed")

    class Provider:
        client = Client()

        async def cancel(self, job_id):
            calls.append(job_id)
            return result

        async def submit(self, *args, **kwargs):
            raise AssertionError("Saved-job cleanup must not submit a replacement")

    monkeypatch.setattr(doxa_research.config, "ConfigManager", Config)
    monkeypatch.setattr(doxa_research.providers, "create_provider", lambda *a, **kw: Provider())
    fixture.cleanup_saved_provider_jobs({}, tmp_path, "perplexity")
    receipt = json.loads((tmp_path / "cleanup-receipt-perplexity.json").read_text())[0]
    assert receipt["job_id"] == "agent:existing-id"
    assert receipt["result"] == result
    assert receipt["cleanup_status"] == expected
    assert calls == ["agent:existing-id"]
    assert closed == ["closed"]
