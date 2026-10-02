"""Artifact-only regressions against actual collector and fixture source."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HEAD = "a" * 40


def module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    return imported


def collect_packet(tmp_path: Path, nodes: list[str], cases: list[tuple[str, str]]) -> dict:
    collector = module("actual_identity_collector", "scripts/collect_live_acceptance.py")
    source = tmp_path / "raw"
    source.mkdir()
    (source / "source.json").write_text(json.dumps({"reviewed_head": HEAD, "checkout_head": HEAD}))
    (source / "collected.txt").write_text("\n".join(nodes) + "\n")
    (source / "process-exit-code.txt").write_text("0")
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for classname, name in cases:
        ET.SubElement(suite, "testcase", classname=classname, name=name)
    (source / "junit.xml").write_text(ET.tostring(root, encoding="unicode"))
    return collector.collect(source, tmp_path / "sanitized", len(nodes))


@pytest.mark.parametrize(
    "nodes,cases",
    [
        (["tests/a.py::test_same"], [("tests.b", "test_same")]),
        (["tests/a.py::ClassA::test_same"], [("tests.a.ClassB", "test_same")]),
        (
            ["tests/a.py::test_same", "tests/b.py::test_same"],
            [("tests.a", "test_same"), ("tests.a", "test_same")],
        ),
        (
            ["tests/a.py::test_same", "tests/b.py::test_same"],
            [("tests.c", "test_same"), ("tests.d", "test_same")],
        ),
    ],
)
def test_wrong_full_identity_or_duplicate_cannot_accept(
    tmp_path: Path, nodes: list[str], cases: list[tuple[str, str]]
) -> None:
    result = collect_packet(tmp_path, nodes, cases)
    assert result["selection_matches"] is False
    assert result["accepted"] is False


@pytest.mark.parametrize(
    "nodes,cases",
    [
        (
            ["tests/a.py::test_same", "tests/b.py::test_same"],
            [("tests.a", "test_same"), ("tests.b", "test_same")],
        ),
        (["tests/a.py::ClassA::test_same[x]"], [("tests.a.ClassA", "test_same[x]")]),
        (["tests/a.py::test_same[x::y]"], [("tests.a", "test_same[x::y]")]),
    ],
)
def test_exact_module_class_and_parameter_identity_is_accepted(
    tmp_path: Path, nodes: list[str], cases: list[tuple[str, str]]
) -> None:
    result = collect_packet(tmp_path, nodes, cases)
    assert result["selection_matches"] is True
    assert result["accepted"] is True


@pytest.mark.parametrize(
    "provider,local_status",
    [
        ("perplexity", "failed"),
        ("perplexity", "cancelled"),
        ("gemini", "failed"),
        ("gemini", "cancelled"),
    ],
)
def test_local_terminal_checkpoint_still_cancels_same_saved_upstream_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, local_status: str
) -> None:
    import doxa_research.config
    import doxa_research.providers

    fixture = module("actual_cleanup_fixture", "tests/extended/conftest.py")
    job_id = "agent:known" if provider == "perplexity" else "interaction-known"
    checkpoint = fixture.checkpoint_path(tmp_path, "research-known")
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
            {
                "id": "research-known",
                "status": local_status,
                "mode": "perplexity_deep_research"
                if provider == "perplexity"
                else "gemini_quick_research",
                "providers": {provider: {"job_id": job_id, "status": "running"}},
            }
        )
    )
    cli_calls = []
    direct_calls = []
    closed = []
    key_name = "PERPLEXITY_API_KEY" if provider == "perplexity" else "GEMINI_API_KEY"
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        key_name: "offline-provider-key",
    }
    monkeypatch.setenv("UNRELATED_PARENT_ONLY", "must-not-enter-fixture")
    environment_before = dict(os.environ)

    class Config:
        def __init__(self, *args, **kwargs):
            assert dict(os.environ) == env

        def load_all_layers(self, *args):
            assert os.environ["HOME"] == str(tmp_path / "home")
            assert os.environ["XDG_CONFIG_HOME"] == str(tmp_path / "config")
            assert os.environ[key_name] == "offline-provider-key"
            assert "UNRELATED_PARENT_ONLY" not in os.environ

        def get_mode_config(self, *args):
            return {"provider": provider, "kind": "background"}

    class Resource:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(self.name)

    class Client:
        def __init__(self):
            self.aio = Resource("sdk_aio")

        async def close(self):
            closed.append("sdk")

    class Provider:
        def __init__(self):
            assert dict(os.environ) == env
            self.client = Client()
            self._async_http = Resource("raw_http")

        async def cancel(self, saved_id):
            assert dict(os.environ) == env
            direct_calls.append(saved_id)
            return {"status": "cancelling"}

        async def submit(self, *args, **kwargs):
            raise AssertionError("Cleanup must never create a replacement job")

    def cli(args, env, **kwargs):
        cli_calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "status": "ok",
                    "data": {"status": "already_terminal", "previous": local_status},
                }
            ),
            "",
        ), 0.0

    monkeypatch.setattr(fixture, "run_doxa", cli)
    monkeypatch.setattr(doxa_research.config, "ConfigManager", Config)
    monkeypatch.setattr(
        doxa_research.providers, "create_provider", lambda *args, **kwargs: Provider()
    )

    fixture.cleanup_saved_provider_jobs(env, tmp_path, provider)
    assert not cli_calls, "Direct cleanup must not rely on terminal-skipping CLI cancellation"
    assert sorted(closed) == ["raw_http", "sdk", "sdk_aio"]
    assert dict(os.environ) == environment_before
    assert direct_calls == [job_id], (
        f"local {local_status} prevented upstream cleanup; CLI calls: {cli_calls}"
    )
    receipt = json.loads((tmp_path / f"cleanup-receipt-{provider}.json").read_text())[0]
    assert receipt["job_id"] == job_id
    assert receipt["checkpoint_status_before_cleanup"] == local_status


@pytest.mark.parametrize(
    "result,raises,expected",
    [
        ({"status": "cancelling"}, False, "pending"),
        ({"status": "cancelled", "best_effort": True}, False, "unknown"),
        ({"status": "cancelled"}, False, "confirmed_terminal"),
        ({}, True, "unknown"),
    ],
)
def test_direct_cleanup_receipts_preserve_uncertainty_and_close_after_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: dict,
    raises: bool,
    expected: str,
) -> None:
    import doxa_research.config
    import doxa_research.providers

    fixture = module("actual_cleanup_status_fixture", "tests/extended/conftest.py")
    path = fixture.checkpoint_path(tmp_path, "research-known")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "research-known",
                "status": "failed",
                "providers": {"perplexity": {"job_id": "agent:known", "status": "running"}},
            }
        )
    )
    calls = []
    closed = []

    class Config:
        def load_all_layers(self, *args):
            pass

        def get_mode_config(self, *args):
            return {"provider": "perplexity", "kind": "background"}

    class Client:
        async def close(self):
            closed.append("sdk")

    class RawClient:
        async def aclose(self):
            closed.append("raw")

    class Provider:
        def __init__(self):
            self.client = Client()
            self._async_http = RawClient()

        async def cancel(self, job_id):
            calls.append(job_id)
            if raises:
                raise TimeoutError("offline cancellation timeout")
            return result

        async def submit(self, *args, **kwargs):
            raise AssertionError("No new create during cleanup")

    monkeypatch.setattr(doxa_research.config, "ConfigManager", Config)
    monkeypatch.setattr(
        doxa_research.providers, "create_provider", lambda *args, **kwargs: Provider()
    )
    monkeypatch.setattr(
        fixture,
        "run_doxa",
        lambda *args, **kwargs: pytest.fail("Cleanup must use saved ID directly"),
    )
    before = dict(os.environ)
    fixture.cleanup_saved_provider_jobs({"HOME": str(tmp_path / "home")}, tmp_path, "perplexity")
    receipt = json.loads((tmp_path / "cleanup-receipt-perplexity.json").read_text())[0]
    assert calls == ["agent:known"]
    assert sorted(closed) == ["raw", "sdk"]
    assert dict(os.environ) == before
    assert receipt["job_id"] == "agent:known"
    assert receipt["cleanup_status"] == expected
    if raises:
        assert receipt["error_class"] == "TimeoutError"
