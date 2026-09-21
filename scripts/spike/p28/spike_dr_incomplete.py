#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.74.0"]
# ///
"""P28 Task 6b spike: investigate incomplete recoverability.

Two probes:
1. Surface probe — methods on client.aio.interactions (look for continue/resume).
2. Trigger probe — near-budget DR prompt; up to 65-min wait.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path

from google import genai

AGENT = "deep-research-preview-04-2026"
OUT_DIR = Path(__file__).parent.parent.parent.parent / "research"


async def surface_probe(client) -> dict:
    print("=== Surface probe: methods on client.aio.interactions ===")
    methods = [m for m in dir(client.aio.interactions) if not m.startswith("_")]
    print(f"  methods: {methods}")
    candidates = [
        m
        for m in methods
        if any(s in m.lower() for s in ("continue", "resume", "refetch", "retry"))
    ]
    print(f"  recovery candidates: {candidates or 'NONE'}")
    sigs = {}
    for m in methods:
        attr = getattr(client.aio.interactions, m)
        try:
            sigs[m] = str(inspect.signature(attr))
        except (TypeError, ValueError):
            sigs[m] = "<no signature>"
    return {"methods": methods, "recovery_candidates": candidates, "signatures": sigs}


async def trigger_probe(client) -> dict:
    print("\n=== Trigger probe: attempt to elicit 'incomplete' ===")
    prompt = (
        "Provide an exhaustive analysis of every published consensus algorithm "
        "from 1980 to 2025, including formal correctness proofs, network "
        "assumptions, performance benchmarks across at least 10 deployments "
        "each, and a comparative matrix with citations. Be maximally thorough."
    )
    try:
        resp = await client.aio.interactions.create(
            agent=AGENT, input=prompt, background=True, store=True
        )
    except Exception as exc:
        return {"create_error": f"{type(exc).__name__}: {exc}"}
    interaction_id = getattr(resp, "id", None)
    print(f"  submitted; id={interaction_id}")
    deadline = time.monotonic() + 65 * 60
    transitions: list[dict] = []
    last = None
    final_interaction = None
    while time.monotonic() < deadline:
        await asyncio.sleep(15)
        try:
            interaction = await client.aio.interactions.get(id=interaction_id)
        except Exception as exc:
            transitions.append({"t": time.monotonic(), "error": f"{type(exc).__name__}: {exc}"})
            continue
        status = str(getattr(interaction, "status", "?"))
        if status != last:
            print(f"    status -> {status!r}")
            transitions.append({"t": time.monotonic(), "status": status})
            last = status
        if status in {"completed", "failed", "cancelled", "incomplete"}:
            final_interaction = interaction
            break
    payload = repr(final_interaction)[:5000] if final_interaction is not None else None
    return {
        "interaction_id": interaction_id,
        "transitions": transitions,
        "final_status": last,
        "final_payload_preview": payload,
    }


async def main_async() -> int:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set", file=sys.stderr)
        return 2
    client = genai.Client(api_key=api_key)
    results = {
        "surface": await surface_probe(client),
        "trigger": await trigger_probe(client),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "_dr_spike_incomplete.json").write_text(json.dumps(results, indent=2, default=str))
    print("\n=== SUMMARY ===")
    print(f"recovery_candidates: {results['surface']['recovery_candidates']}")
    print(f"trigger final_status: {results['trigger'].get('final_status')}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
