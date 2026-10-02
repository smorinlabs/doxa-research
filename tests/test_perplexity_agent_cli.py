"""Fresh-process CLI checkpoint/resume/cancel against a scoped local Agent server."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests._fixture_helpers import run_doxa, write_test_checkpoint
from tests.test_provider_perplexity_agent import _payload


@contextmanager
def _server():
    state: dict[str, Any] = {"status": "in_progress", "initial_status": "queued", "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["calls"].append(("POST", self.path))
            if self.path == "/v1/agent":
                self.reply(_payload(state["initial_status"]))
            elif self.path == "/v1/agent/opaque-id/cancel":
                state["status"] = "cancelled"
                self.reply({"response_id": "opaque-id", "status": "cancelling"})
            else:
                self.send_error(404)

        def do_GET(self):
            state["calls"].append(("GET", self.path))
            if self.path == "/v1/agent/opaque-id":
                self.reply(_payload(state["status"]))
            else:
                self.send_error(404)

        def log_message(self, format, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", state
        finally:
            server.shutdown()
            thread.join(timeout=1)


def _config(tmp_path: Path, checkpoint_dir: Path, endpoint: str) -> Path:
    path = tmp_path / "doxa.config.toml"
    path.write_text(f"""version = "2.0"
[providers.perplexity]
api_key = "offline-key"
base_url = {json.dumps(endpoint)}
[paths]
checkpoint_dir = {json.dumps(str(checkpoint_dir))}
base_output_dir = {json.dumps(str(tmp_path / "output"))}
[execution]
poll_interval = 1
max_wait = 1
""")
    return path


def _wait_checkpoint(path: Path, condition) -> dict:
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if path.exists():
            data = json.loads(path.read_text())
            if condition(data):
                return data
        time.sleep(0.05)
    raise AssertionError(f"checkpoint did not reach expected state: {path}")


def test_cli_agent_submit_fresh_resume_saves_actual_model_and_never_resubmits(
    isolated_doxa_home, checkpoint_dir, tmp_path
):
    with _server() as (endpoint, state):
        config = _config(tmp_path, checkpoint_dir, endpoint)
        code, output, error = run_doxa(
            [
                "ask",
                "query",
                "--mode",
                "perplexity_deep_research",
                "--provider",
                "perplexity",
                "--config",
                str(config),
                "--async",
                "--json",
            ],
            cwd=tmp_path,
        )
        assert code == 0, output + error
        op_id = json.loads(output)["data"]["operation_id"]
        path = checkpoint_dir / f"{op_id}.json"
        checkpoint = _wait_checkpoint(path, lambda cp: cp["providers"]["perplexity"].get("job_id"))
        assert checkpoint["providers"]["perplexity"]["job_id"] == "agent:opaque-id"
        code, output, error = run_doxa(
            ["resume", op_id, "--config", str(config), "--async", "--json"], cwd=tmp_path
        )
        assert code == 0, output + error
        assert json.loads(output)["data"]["providers"]["perplexity"]["job_id"] == "agent:opaque-id"
        state["status"] = "completed"
        code, output, error = run_doxa(
            ["resume", op_id, "--config", str(config), "--quiet"], cwd=tmp_path
        )
        assert code == 0, output + error
        checkpoint = _wait_checkpoint(path, lambda cp: cp["status"] == "completed")
        text = Path(checkpoint["output_paths"]["perplexity"]).read_text()
        assert "model: test/model" in text
        assert "Answer." in text
        assert state["calls"].count(("POST", "/v1/agent")) == 1


def test_cli_valid_agent_id_is_preserved_when_initial_status_and_poll_are_unknown(
    isolated_doxa_home, checkpoint_dir, tmp_path
):
    with _server() as (endpoint, state):
        state["initial_status"] = state["status"] = "future_status"
        config = _config(tmp_path, checkpoint_dir, endpoint)
        code, output, error = run_doxa(
            [
                "ask",
                "query",
                "--mode",
                "perplexity_deep_research",
                "--provider",
                "perplexity",
                "--config",
                str(config),
                "--async",
                "--json",
            ],
            cwd=tmp_path,
        )
        assert code == 0, output + error
        op_id = json.loads(output)["data"]["operation_id"]
        path = checkpoint_dir / f"{op_id}.json"
        checkpoint = _wait_checkpoint(path, lambda cp: cp["providers"]["perplexity"].get("job_id"))
        assert checkpoint["providers"]["perplexity"]["job_id"] == "agent:opaque-id"
        code, output, error = run_doxa(
            ["resume", op_id, "--config", str(config), "--async", "--json"], cwd=tmp_path
        )
        assert code != 0
        assert "Malformed or unknown Agent response" in output + error
        saved = json.loads(path.read_text())
        assert saved["providers"]["perplexity"]["job_id"] == "agent:opaque-id"
        assert ("GET", "/v1/agent/opaque-id") in state["calls"]
        assert state["calls"].count(("POST", "/v1/agent")) == 1


@pytest.mark.parametrize(
    "job_id,expected", [("agent:opaque-id", "cancelling"), ("legacy-request-id", "permanent_error")]
)
def test_cli_cancel_checkpoint_preserves_identity_and_reports_pending_or_legacy(
    isolated_doxa_home, checkpoint_dir, tmp_path, job_id, expected
):
    with _server() as (endpoint, state):
        config = _config(tmp_path, checkpoint_dir, endpoint)
        op_id = "research-20261002-agent-cancel"
        write_test_checkpoint(
            checkpoint_dir,
            op_id,
            status="running",
            providers={"perplexity": {"status": "running", "job_id": job_id}},
            mode="perplexity_deep_research",
        )
        code, output, error = run_doxa(
            ["cancel", op_id, "--config", str(config), "--json"], cwd=tmp_path
        )
        assert code == 0, output + error
        assert json.loads(output)["data"]["providers"]["perplexity"]["status"] == expected
        saved = json.loads((checkpoint_dir / f"{op_id}.json").read_text())
        assert saved["status"] == "cancelled"
        assert saved["providers"]["perplexity"]["job_id"] == job_id
        assert all(path != "/v1/agent" for _, path in state["calls"])
        if job_id.startswith("agent:"):
            assert state["calls"] == [("POST", "/v1/agent/opaque-id/cancel")]
        else:
            assert state["calls"] == []


@pytest.mark.parametrize("status", ["running", "completed"])
def test_cli_legacy_checkpoint_resume_never_creates_agent_job(
    isolated_doxa_home, checkpoint_dir, tmp_path, status
):
    with _server() as (endpoint, state):
        config = _config(tmp_path, checkpoint_dir, endpoint)
        op_id = "research-20261002-legacy-resume"
        write_test_checkpoint(
            checkpoint_dir,
            op_id,
            status=status,
            providers={"perplexity": {"status": status, "job_id": "legacy-request-id"}},
            mode="perplexity_deep_research",
        )
        code, output, error = run_doxa(
            ["resume", op_id, "--config", str(config), "--async", "--json"], cwd=tmp_path
        )
        if status == "completed":
            assert code == 0, output + error
        else:
            assert code != 0
            assert "legacy" in (output + error).lower()
        assert not state["calls"]
        saved = json.loads((checkpoint_dir / f"{op_id}.json").read_text())
        assert saved["providers"]["perplexity"]["job_id"] == "legacy-request-id"


def test_cli_initial_blocking_agent_ask_persists_completed_id_and_actual_model(
    isolated_doxa_home, checkpoint_dir, tmp_path
):
    with _server() as (endpoint, state):
        state["initial_status"] = state["status"] = "completed"
        config = _config(tmp_path, checkpoint_dir, endpoint)
        code, output, error = run_doxa(
            [
                "ask",
                "query",
                "--mode",
                "perplexity_deep_research",
                "--provider",
                "perplexity",
                "--config",
                str(config),
                "--quiet",
            ],
            cwd=tmp_path,
        )
        assert code == 0, output + error
        paths = list(checkpoint_dir.glob("research-*.json"))
        assert len(paths) == 1
        saved = json.loads(paths[0].read_text())
        assert saved["status"] == "completed"
        assert saved["providers"]["perplexity"]["job_id"] == "agent:opaque-id"
        text = Path(saved["output_paths"]["perplexity"]).read_text()
        assert "model: test/model" in text and "Answer." in text
        assert state["calls"].count(("POST", "/v1/agent")) == 1
