"""VCR cassette replay tests for OpenAI provider."""

from __future__ import annotations

import asyncio

from tests.conftest import CASSETTE_DIR, doxa_vcr

OPENAI_CASSETTE = str(CASSETTE_DIR / "openai" / "happy-path.yaml")

# The prompt recorded in the cassette — must match for submit() to work,
# though VCR matching ignores bodies (match_on=["uri", "method"]).
CASSETTE_PROMPT = (
    "What are the three most significant recent breakthroughs"
    " in solid-state battery technology? Provide specific company"
    " names, dates, and technical details with citations."
)

# The response ID baked into the cassette.
CASSETTE_RESPONSE_ID = "resp_0c13a25ee1a48f780069df079e0a58819cbde29d8a2906c590"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_provider():
    """Create an OpenAIProvider configured for deep-research cassette replay."""
    from doxa_research.__main__ import OpenAIProvider

    return OpenAIProvider(
        api_key="sk-replay-dummy",
        config={"model": "gpt-5.6-sol", "background": True},
    )


class TestOpenAISubmit:
    """Replay the happy-path cassette through OpenAIProvider.submit()."""

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_submit_returns_response_id(self):
        """submit() should return a response ID starting with 'resp_'."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))
        assert job_id.startswith("resp_"), f"unexpected job_id: {job_id}"

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_submit_returns_expected_id(self):
        """submit() should return the exact ID from the cassette."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))
        assert job_id == CASSETTE_RESPONSE_ID

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_submit_stores_job_info(self):
        """submit() should populate self.jobs with the job ID."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))
        assert job_id in provider.jobs
        assert provider.jobs[job_id]["background"] is True


class TestOpenAIPolling:
    """Replay the full polling sequence to completion."""

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_first_status_is_in_progress_or_queued(self):
        """First check_status() after submit should be queued or in_progress."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))
        status = _run(provider.check_status(job_id))
        assert status["status"] in ("queued", "running"), f"unexpected: {status}"

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_poll_to_completed(self):
        """Polling check_status() through the cassette should reach 'completed'."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))

        final_status = None
        for _ in range(60):
            info = _run(provider.check_status(job_id))
            if info["status"] == "completed":
                final_status = info
                break
        assert final_status is not None, "never reached 'completed'"
        assert final_status["status"] == "completed"
        assert final_status["progress"] == 1.0


class TestOpenAIResult:
    """Verify get_result() returns substantial research output."""

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_get_result_returns_text(self):
        """After polling to completed, get_result() should return research text."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))

        for _ in range(60):
            info = _run(provider.check_status(job_id))
            if info["status"] == "completed":
                break

        # Force get_result() to use the cached response instead of issuing
        # another live retrieve; this exercises the cache fallback path
        # deterministically without relying on cassette exhaustion.
        async def _fail_retrieve(*args, **kwargs):
            raise RuntimeError("cached-path test: retrieve disabled")

        provider.client.responses.retrieve = _fail_retrieve
        result = _run(provider.get_result(job_id))
        assert len(result) > 100, f"expected substantial output, got {len(result)} chars"

    @doxa_vcr.use_cassette(OPENAI_CASSETTE)
    def test_get_result_contains_research_content(self):
        """Result text should contain domain-relevant content from the cassette."""
        provider = _make_provider()
        job_id = _run(provider.submit(prompt=CASSETTE_PROMPT, mode="deep-research"))

        for _ in range(60):
            info = _run(provider.check_status(job_id))
            if info["status"] == "completed":
                break

        async def _fail_retrieve(*args, **kwargs):
            raise RuntimeError("cached-path test: retrieve disabled")

        provider.client.responses.retrieve = _fail_retrieve
        result = _run(provider.get_result(job_id))
        # The cassette output discusses solid-state batteries
        assert "solid" in result.lower() or "battery" in result.lower(), (
            "expected research content about batteries"
        )
