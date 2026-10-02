"""Offline proof that existing-job observation never creates or changes a job."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx2
import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_live_jobs.py"
_SPEC = importlib.util.spec_from_file_location("reconcile_live_jobs", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
observer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(observer)


@pytest.mark.parametrize(
    "provider,job_id,host,path",
    [
        ("openai", "resp_a/b?c#d%2F", "api.openai.com", "/v1/responses/resp_a%2Fb%3Fc%23d%252F"),
        (
            "perplexity",
            "agent:opaque/a?b#c%2F",
            "api.perplexity.ai",
            "/v1/agent/opaque%2Fa%3Fb%23c%252F",
        ),
    ],
)
def test_observer_is_one_get_to_fixed_host_encoded_segment(provider, job_id, host, path):
    requests = []
    raw_id = job_id.removeprefix("agent:") if provider == "perplexity" else job_id

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json={"id": raw_id, "status": "completed"})

    jobs = observer.parse_jobs(json.dumps([{"provider": provider, "job_id": job_id}]))
    report = asyncio.run(
        observer.observe(jobs, {provider: "secret-key"}, httpx2.MockTransport(respond))
    )
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.host == host
    assert requests[0].url.raw_path.decode() == path
    assert requests[0].headers["authorization"] == "Bearer secret-key"
    assert report["all_terminal"] is True
    assert "secret-key" not in json.dumps(report)


@pytest.mark.parametrize(
    "status,terminal",
    [
        ("completed", True),
        ("failed", True),
        ("cancelled", True),
        ("queued", False),
        ("in_progress", False),
        ("incomplete", True),
        ("secret-key", False),
    ],
)
def test_pending_unknown_and_terminal_are_reported_truthfully(status, terminal):
    jobs = observer.parse_jobs('[{"provider":"openai","job_id":"resp_saved"}]')
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(200, json={"id": "resp_saved", "status": status})
    )
    report = asyncio.run(observer.observe(jobs, {"openai": "secret-key"}, transport))
    assert report["all_terminal"] is terminal
    assert "secret-key" not in json.dumps(report)


@pytest.mark.parametrize("code", [301, 400, 401, 403, 404, 429, 500])
def test_http_failure_never_retries_redirects_or_creates(code):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            code, headers={"location": "https://evil.invalid/"}, text="secret-key"
        )

    jobs = observer.parse_jobs('[{"provider":"perplexity","job_id":"agent:saved"}]')
    report = asyncio.run(
        observer.observe(jobs, {"perplexity": "secret-key"}, httpx2.MockTransport(respond))
    )
    assert len(requests) == 1 and requests[0].method == "GET"
    assert report["all_terminal"] is False
    assert report["jobs"][0]["http_status"] == code
    assert "secret-key" not in json.dumps(report)


def test_network_exception_does_not_leak_key_or_retry():
    requests = []

    def respond(request):
        requests.append(request)
        raise httpx2.ReadError("secret-key", request=request)

    jobs = observer.parse_jobs('[{"provider":"openai","job_id":"resp_saved"}]')
    report = asyncio.run(
        observer.observe(jobs, {"openai": "secret-key"}, httpx2.MockTransport(respond))
    )
    assert len(requests) == 1 and report["all_terminal"] is False
    assert "secret-key" not in json.dumps(report)


@pytest.mark.parametrize("job_id", ["", ".", "..", "agent:.", "agent:..", "a\n", "a\x00", " a"])
def test_invalid_job_ids_fail_before_transport(job_id):
    with pytest.raises(ValueError):
        observer.parse_jobs(json.dumps([{"provider": "perplexity", "job_id": job_id}]))


@pytest.mark.parametrize(
    "payload", [None, [], {"id": "different", "status": "completed"}, {"id": "saved"}]
)
def test_bad_response_remains_unknown(payload):
    jobs = observer.parse_jobs('[{"provider":"openai","job_id":"saved"}]')
    report = asyncio.run(
        observer.observe(
            jobs,
            {"openai": "key"},
            httpx2.MockTransport(lambda request: httpx2.Response(200, json=payload)),
        )
    )
    assert not report["all_terminal"]


def test_missing_credentials_prevents_every_request():
    jobs = observer.parse_jobs(
        '[{"provider":"openai","job_id":"saved"},{"provider":"perplexity","job_id":"agent:saved"}]'
    )
    requests = []

    def unexpected(request):
        requests.append(request)
        return httpx2.Response(500)

    transport = httpx2.MockTransport(unexpected)
    with pytest.raises(ValueError, match="Missing credentials"):
        asyncio.run(observer.observe(jobs, {"openai": "key"}, transport))
    assert requests == []


def test_head_and_mode_guards_run_before_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEWED_HEAD", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("EXISTING_JOBS_JSON", '[{"provider":"openai","job_id":"saved"}]')
    monkeypatch.setattr(observer, "checkout_head", lambda: "a" * 40)
    monkeypatch.setattr(
        observer,
        "read_credentials",
        lambda jobs: pytest.fail("Credentials accessed before source validation"),
    )
    assert observer.main(["--output-dir", str(tmp_path)]) == 2
    assert json.loads((tmp_path / "observation.json").read_text())["all_terminal"] is False
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("MIGRATION_ACCEPTANCE", "true")
    assert observer.main(["--output-dir", str(tmp_path)]) == 2


def test_report_redacts_credentials_even_from_saved_id(tmp_path):
    observer.write_report(tmp_path, {"jobs": [{"job_id": "secret-key"}]}, {"openai": "secret-key"})
    assert "secret-key" not in (tmp_path / "observation.json").read_text()


def test_per_request_timeout_continues_and_retains_all_saved_ids(monkeypatch):
    jobs = observer.parse_jobs(
        '[{"provider":"openai","job_id":"first"},{"provider":"openai","job_id":"second"}]'
    )
    requests = []

    async def delayed(request):
        requests.append(request)
        await asyncio.sleep(0.02)
        return httpx2.Response(200, json={"id": "unused", "status": "completed"})

    actual_timeout = asyncio.timeout
    monkeypatch.setattr(observer.asyncio, "timeout", lambda seconds: actual_timeout(0.001))
    report = asyncio.run(observer.observe(jobs, {"openai": "key"}, httpx2.MockTransport(delayed)))
    assert len(requests) == 2
    assert [row["job_id"] for row in report["jobs"]] == ["first", "second"]
    assert all(row["classification"] == "unknown" for row in report["jobs"])
    assert not report["all_terminal"]


def test_missing_credentials_main_retains_saved_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEWED_HEAD", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("MIGRATION_ACCEPTANCE", "false")
    monkeypatch.setenv("EXISTING_JOBS_JSON", '[{"provider":"openai","job_id":"saved"}]')
    monkeypatch.setattr(observer, "checkout_head", lambda: "a" * 40)
    monkeypatch.setattr(observer, "read_credentials", lambda jobs: {"openai": ""})
    assert observer.main(["--output-dir", str(tmp_path)]) == 2
    report = json.loads((tmp_path / "observation.json").read_text())
    assert report["requests"] == 0
    assert report["jobs"][0]["job_id"] == "saved"
    assert report["jobs"][0]["classification"] == "unknown"


def test_valid_preflight_never_reads_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEWED_HEAD", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("MIGRATION_ACCEPTANCE", "false")
    monkeypatch.setenv("EXISTING_JOBS_JSON", '[{"provider":"openai","job_id":"saved"}]')
    monkeypatch.setattr(observer, "checkout_head", lambda: "a" * 40)
    monkeypatch.setattr(
        observer, "read_credentials", lambda jobs: pytest.fail("Preflight read credentials")
    )
    assert observer.main(["--preflight", "--output-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "observation.json").read_text())
    assert report["requests"] == 0 and report["preflight"] == "passed"
