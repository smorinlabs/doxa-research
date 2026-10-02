# P44 - Perplexity Agent API Migration

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Related:** [P43](P43-httpx2-migration-openai-3x.md), merged PR #172.
- **External:** https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview
- **External:** https://docs.perplexity.ai/openapi.json

**Status:** `[~]` Implementation is published in [PR #176](https://github.com/smorinlabs/doxa-research/pull/176).
The hosted live batch on `4cd777f` passed 53 of 54 cases. The failed offline
guard was repaired and passed separately with HTTP forbidden. Final repaired-head
checks and review remain distinct from that original live run.

**Goal:** Move background Perplexity research from retired async Sonar to the
supported Agent API using raw HTTPX2. Preserve immediate/streaming Sonar.

**Owner authorization:** Separate migration and live testing approved after
PR #172 merged. Publication does not authorize merging this new PR.

## Tests and tasks

- [x] [P44-TS01] Write failing raw Agent wire and lifecycle contracts first.
- [x] [P44-T01] Implement flat background create, poll, typed final output and cancellation.
- [x] [P44-TS02] Verify single-create behavior, malformed responses, transient cache,
      legacy checkpoints and fresh-process CLI resume/cancel without resubmission.
- [x] [P44-T02] Preserve IDs with the `agent:` discriminator and record actual upstream models.
- [x] [P44-T03] Map supported legacy config, reject incompatible fields and document defaults.
- [x] [P44-T04] Pass required whole-source quality, unit, mock integration and normal hooks.
      The migration passed 92 mock cases; the token-alias repair passed 1,754
      unit cases and normal commit hooks at `7c80962`.
- [ ] [P44-TS03] Run the existing non-slow extended and live_api selections on the
      reviewed head with hosted credentials. Carry P43-TS03's live gate forward.
- [x] [P44-TS04] Complete the three existing OpenAI, Perplexity and Gemini
      lifecycle smoke nodes. Carry P43-TS04's live gate forward.
- [x] [P44-T05] Review exact source, publish a separate PR and record hosted outcomes.

## Observed acceptance evidence

[Run 36979307601](https://github.com/smorinlabs/doxa-research/actions/runs/36979307601)
executed once against `4cd777f`: extended 20/20, live_api 30/31, and completed
OpenAI, Perplexity and Gemini lifecycle cases 1/1 each. No case skipped or
xfailed. The workflow conclusion was failure and remains failure.

`test_mismatch_defense_no_http` selected `gpt-5.6-sol`, which supports immediate
submission despite its background default. The repaired guard selects the
background-only `o3-deep-research` local validation fixture, forbids HTTP,
checks the mismatch fields and closes the client. Its separate offline rerun
passed. This does not convert the original run or the later source head into
a fully green hosted live batch; P44-TS03 remains unchecked for that reason.

The saved-operation audit found seven IDs without independent terminal-status
receipts. A separate GET-only observation mode queries those existing IDs once;
it never submits replacement research or cancels jobs. Its sanitized receipts
and the complete case ledger belong in the PR outcome report. Pending or
unknown results must remain visible until subsequent evidence settles them.

Agent preset `high` supplies upstream model/reasoning defaults. Product defaults
are not capped. Live fixtures use max_steps=10/max_output_tokens=8192, retain
known IDs and request best-effort cleanup after failures/timeouts. Cancellation
acknowledgment remains distinct from terminal confirmation. Unknown create
outcomes never trigger an automatic second POST.
