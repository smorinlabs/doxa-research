"""P18 Phase G: provider.cancel() implementations.

Mock cancel hermetically; OpenAI cancel against monkeypatched client (no
real HTTP). PerplexityProvider keeps the inherited NotImplementedError.
"""

from __future__ import annotations

import asyncio

import pytest

from doxa_research.providers.mock import MockProvider
from doxa_research.providers.openai import OpenAIProvider
from doxa_research.providers.perplexity import PerplexityProvider


def test_mock_cancel_pops_job_and_returns_cancelled() -> None:
    mock = MockProvider(name="mock", delay=0.0, api_key="mock-test")

    async def _flow():
        job_id = await mock.submit("ping", mode="default")
        result = await mock.cancel(job_id)
        return job_id, result

    job_id, result = asyncio.run(_flow())
    assert result["status"] == "cancelled"
    assert "cancelled by user" in result["error"]
    assert job_id not in mock.jobs


def test_openai_cancel_calls_responses_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the cancel call hits client.responses.cancel and maps the result."""
    p = OpenAIProvider(api_key="sk-test", config={"model": "o3-deep-research"})
    p.jobs["job-abc"] = {"response": None, "background": True, "created_at": None}

    fake_response = type("FakeResp", (), {"status": "cancelled", "id": "job-abc"})()

    captured: dict = {}

    async def fake_cancel(job_id: str):
        captured["job_id"] = job_id
        return fake_response

    # Replace the client.responses.cancel method
    monkeypatch.setattr(p.client.responses, "cancel", fake_cancel)

    result = asyncio.run(p.cancel("job-abc"))
    assert captured["job_id"] == "job-abc"
    assert result["status"] == "cancelled"


def test_openai_cancel_completed_job_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the upstream job already completed, cancel returns 'completed' not error."""
    p = OpenAIProvider(api_key="sk-test", config={"model": "o3-deep-research"})
    p.jobs["job-done"] = {"response": None, "background": True, "created_at": None}

    fake_response = type("FakeResp", (), {"status": "completed", "id": "job-done"})()

    async def fake_cancel(job_id: str):
        return fake_response

    monkeypatch.setattr(p.client.responses, "cancel", fake_cancel)

    result = asyncio.run(p.cancel("job-done"))
    assert result["status"] == "completed"


def test_openai_cancel_handles_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    p = OpenAIProvider(api_key="sk-test", config={"model": "o3-deep-research"})
    p.jobs["job-broken"] = {"response": None, "background": True, "created_at": None}

    async def fake_cancel(job_id: str):
        raise RuntimeError("upstream API broke")

    monkeypatch.setattr(p.client.responses, "cancel", fake_cancel)

    result = asyncio.run(p.cancel("job-broken"))
    assert result["status"] == "permanent_error"
    assert "upstream API broke" in result["error"]


def test_perplexity_legacy_cancel_is_actionable_without_http() -> None:
    p = PerplexityProvider(api_key="key", config={"model": "sonar-deep-research"})
    from doxa_research.errors import ProviderError

    with pytest.raises(ProviderError, match="Legacy Sonar"):
        asyncio.run(p.cancel("any-legacy-job"))
