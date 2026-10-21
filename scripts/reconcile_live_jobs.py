"""Observe saved upstream jobs once with GET only; never submit or cancel."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2

_HOSTS = {
    "openai": "https://api.openai.com/v1/responses/",
    "perplexity": "https://api.perplexity.ai/v1/agent/",
}
_KEY_NAMES = {"openai": "OPENAI_API_KEY", "perplexity": "PERPLEXITY_API_KEY"}
_TERMINAL = {"completed", "failed", "cancelled", "incomplete"}
_PENDING = {"queued", "in_progress"}


def parse_jobs(serialized: str) -> list[dict[str, str]]:
    try:
        values = json.loads(serialized)
    except ValueError as exc:
        raise ValueError("Invalid saved-job JSON") from exc
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ValueError("Supply 1 to 20 existing jobs")
    jobs = []
    seen = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"provider", "job_id"}:
            raise ValueError("Each saved job needs provider and job_id only")
        provider, saved_id = value["provider"], value["job_id"]
        if not isinstance(provider, str) or provider not in _HOSTS or not isinstance(saved_id, str):
            raise ValueError("Unsupported provider or invalid saved job ID")
        raw_id = saved_id.removeprefix("agent:") if provider == "perplexity" else saved_id
        if (
            not raw_id
            or len(raw_id) > 512
            or raw_id in {".", ".."}
            or any(ord(char) <= 32 or ord(char) == 127 for char in raw_id)
        ):
            raise ValueError("Invalid opaque saved job ID")
        identity = (provider, raw_id)
        if identity in seen:
            raise ValueError("Duplicate saved job ID")
        seen.add(identity)
        jobs.append({"provider": provider, "job_id": saved_id, "raw_id": raw_id})
    return jobs


def checkout_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def read_credentials(jobs: list[dict[str, str]]) -> dict[str, str]:
    return {job["provider"]: os.environ.get(_KEY_NAMES[job["provider"]], "") for job in jobs}


async def observe(
    jobs: list[dict[str, str]],
    credentials: dict[str, str],
    transport: httpx2.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if any(not credentials.get(job["provider"]) for job in jobs):
        raise ValueError("Missing credentials for an observation provider")
    rows = []
    async with httpx2.AsyncClient(
        transport=transport,
        timeout=httpx2.Timeout(10, connect=5),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for job in jobs:
            row: dict[str, Any] = {
                "provider": job["provider"],
                "job_id": job["job_id"],
                "classification": "unknown",
                "upstream_status": None,
            }
            rows.append(row)
            try:
                async with asyncio.timeout(12):
                    response = await client.get(
                        _HOSTS[job["provider"]] + quote(job["raw_id"], safe=""),
                        headers={"Authorization": "Bearer " + credentials[job["provider"]]},
                    )
                row["http_status"] = response.status_code
                if response.status_code != 200:
                    row["detail"] = "http_error"
                    continue
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("id") != job["raw_id"]:
                    row["detail"] = "invalid_or_mismatched_response_id"
                    continue
                status = payload.get("status")
                if status in _TERMINAL:
                    row.update(
                        classification="terminal",
                        upstream_status=status,
                        successful_research=status == "completed",
                    )
                elif status in _PENDING:
                    row.update(classification="pending", upstream_status=status)
                else:
                    row["detail"] = "unknown_upstream_status"
            except (httpx2.HTTPError, ValueError, TypeError, TimeoutError):
                # Exception strings and response bodies can contain credentials.
                row["detail"] = "transport_or_response_error"
    return {
        "jobs": rows,
        "all_terminal": all(row["classification"] == "terminal" for row in rows),
        "requests": len(rows),
        "request_method": "GET",
        "creates": 0,
        "cancels": 0,
    }


def write_report(output_dir: Path, report: dict[str, Any], credentials: dict[str, str]) -> None:
    serialized = json.dumps(report, indent=2)
    for secret in credentials.values():
        if secret:
            serialized = serialized.replace(secret, "[REDACTED]")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "observation.json").write_text(serialized + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    credentials: dict[str, str] = {}
    report: dict[str, Any] = {
        "all_terminal": False,
        "requests": 0,
        "creates": 0,
        "cancels": 0,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    code = 2
    try:
        reviewed = os.environ.get("REVIEWED_HEAD", "")
        head = checkout_head()
        if (
            not re.fullmatch(r"[0-9a-f]{40}", reviewed)
            or reviewed != head
            or reviewed != os.environ.get("GITHUB_SHA")
        ):
            raise ValueError("Source binding failed")
        if os.environ.get("MIGRATION_ACCEPTANCE", "false").lower() in {"true", "1"}:
            raise ValueError("Observer and migration modes cannot be combined")
        jobs = parse_jobs(os.environ.get("EXISTING_JOBS_JSON", ""))
        report.update(
            reviewed_head=reviewed,
            checkout_head=head,
            run_id=os.environ.get("GITHUB_RUN_ID"),
            run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
            jobs=[
                {"provider": job["provider"], "job_id": job["job_id"], "classification": "unknown"}
                for job in jobs
            ],
        )
        if args.preflight:
            report.update(
                preflight="passed",
                jobs=[{"provider": job["provider"], "job_id": job["job_id"]} for job in jobs],
            )
            code = 0
        else:
            credentials = read_credentials(jobs)
            report.update(asyncio.run(observe(jobs, credentials)))
            code = 0 if report["all_terminal"] else 1
    except (ValueError, OSError, subprocess.SubprocessError):
        report["detail"] = "preflight_or_credentials_failed"
    write_report(args.output_dir, report, credentials)
    print(
        json.dumps(
            {
                "all_terminal": report["all_terminal"],
                "requests": report["requests"],
                "exit_code": code,
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
