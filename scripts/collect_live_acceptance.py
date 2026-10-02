"""Summarize and sanitize existing pytest acceptance results; never calls APIs."""

from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

PROVIDER_KEY_NAMES = ("OPENAI_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY")


def redact(text: str) -> str:
    for name in PROVIDER_KEY_NAMES:
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def collect(source: Path, destination: Path, expected_count: int) -> dict:
    if expected_count < 1:
        raise ValueError("Expected test count must be positive")
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "pytest.log",
        "junit.xml",
        "collected.txt",
        "source.json",
        "process-exit-code.txt",
    ):
        path = source / name
        if path.exists():
            (destination / name).write_text(redact(path.read_text(errors="replace")))
    code_path = source / "process-exit-code.txt"
    try:
        code = int(code_path.read_text().strip()) if code_path.exists() else None
    except ValueError:
        code = None
    report = destination / "junit.xml"
    report_error = None
    try:
        cases = ET.parse(report).getroot().findall(".//testcase") if report.exists() else []
    except ET.ParseError:
        cases = []
        report_error = "malformed_junit"
    results = []
    for case in cases:
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        state = "passed"
        detail = None
        if failure is not None or error is not None:
            state = "failed"
            problem = failure if failure is not None else error
            detail = problem.get("message") if problem is not None else None
        elif skipped is not None:
            state = "xfail" if skipped.get("type") == "pytest.xfail" else "skipped"
            detail = skipped.get("message")
        results.append(
            {
                "class": case.get("classname"),
                "name": case.get("name"),
                "status": state,
                "detail": detail,
            }
        )
    counts = {
        state: sum(row["status"] == state for row in results)
        for state in ("passed", "failed", "skipped", "xfail")
    }
    operations = []
    for path in (source / "tmp").rglob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if (
            isinstance(data, dict)
            and isinstance(data.get("providers"), dict)
            and str(data.get("id", "")).startswith("research-")
        ):
            operations.append(
                {
                    "operation_id": data["id"],
                    "status": data.get("status"),
                    "providers": data["providers"],
                    "error": data.get("error"),
                    "output_paths": data.get("output_paths"),
                }
            )
    (destination / "operations.json").write_text(redact(json.dumps(operations, indent=2)) + "\n")
    # Output markdown has final text and optional reported provider cost.
    output_dir = destination / "outputs"
    for index, path in enumerate((source / "tmp").rglob("*.md")):
        output_dir.mkdir(exist_ok=True)
        (output_dir / f"{index}-{path.name}").write_text(redact(path.read_text(errors="replace")))
    receipt_files = []
    unknown_submissions = 0
    for index, path in enumerate((source / "tmp").rglob("*receipt*.json")):
        receipt_dir = destination / "receipts"
        receipt_dir.mkdir(exist_ok=True)
        name = f"{index}-{path.name}"
        sanitized = redact(path.read_text(errors="replace"))
        (receipt_dir / name).write_text(sanitized)
        receipt_files.append(name)
        try:
            data = json.loads(sanitized)
            if isinstance(data, dict) and data.get("submission_outcome") == "unknown":
                unknown_submissions += 1
        except ValueError:
            pass
    source_manifest = {}
    if (destination / "source.json").exists():
        try:
            source_manifest = json.loads((destination / "source.json").read_text())
        except ValueError:
            source_manifest = {}
    if not isinstance(source_manifest, dict):
        source_manifest = {}
    head_bound = bool(source_manifest.get("reviewed_head")) and source_manifest.get(
        "reviewed_head"
    ) == source_manifest.get("checkout_head")
    selected = []
    selection_path = destination / "collected.txt"
    if selection_path.exists():
        selected = [
            line.strip()
            for line in selection_path.read_text().splitlines()
            if line.startswith("tests/") and "::" in line
        ]
    selected_identities = []
    for node in selected:
        address, bracket, parameters = node.partition("[")
        parts = address.split("::")
        module = parts[0].removesuffix(".py").replace("/", ".")
        classname = ".".join([module, *parts[1:-1]])
        name = parts[-1] + (bracket + parameters if bracket else "")
        selected_identities.append((classname, name))
    result_identities = [(row["class"], row["name"]) for row in results]
    selection_matches = (
        len(selected) == expected_count
        and len(set(selected_identities)) == len(selected_identities)
        and len(set(result_identities)) == len(result_identities)
        and set(result_identities) == set(selected_identities)
    )
    outcome = {
        "selection_matches": selection_matches,
        "selected_nodes": selected,
        "receipt_files": receipt_files,
        "unknown_submission_receipts": unknown_submissions,
        "expected_tests": expected_count,
        "observed_testcases": len(results),
        "process_exit_code": code,
        "timeout_or_unknown_process_exit": code not in range(6) if code is not None else True,
        "junit_error": report_error,
        "head_bound": head_bound,
        "counts": counts,
        "cases": results,
        "checkpoint_operations": len(operations),
        "accepted": head_bound
        and selection_matches
        and code == 0
        and len(results) == expected_count
        and counts["passed"] == expected_count,
    }
    (destination / "outcome.json").write_text(redact(json.dumps(outcome, indent=2)) + "\n")
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    args = parser.parse_args()
    if args.expected_count < 1:
        parser.error("Expected test count must be positive")
    result = collect(args.source, args.destination, args.expected_count)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "expected_tests",
                    "observed_testcases",
                    "process_exit_code",
                    "counts",
                    "accepted",
                )
            },
            indent=2,
        )
    )
    raise SystemExit(0 if result["accepted"] else 1)


if __name__ == "__main__":
    main()
