# P44 - Perplexity Agent API Migration

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Related:** [P43](P43-httpx2-migration-openai-3x.md), merged PR #172.
- **External:** https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview
- **External:** https://docs.perplexity.ai/openapi.json

**Status:** `[~]` Implementation is published in [PR #176](https://github.com/smorinlabs/doxa-research/pull/176).
The fresh hosted batch passed all 54 tests on commit
`b55ef98f6533f224b8adda94755f2898c14941a6`. Its saved-job follow-up confirmed
terminal upstream states for all six observed IDs. Implementation and live
acceptance are complete; the PR remains open without merge authorization.
The original 53/54 failed run is preserved below as superseded evidence.

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
      The tested `b55ef98f6533f224b8adda94755f2898c14941a6` passed 1,789 unit
      tests, with 56 gated live tests deselected. Earlier whole-mock validation
      passed all 92 cases including 17 interactive cases; normal commit hooks
      passed the 75 non-interactive mock cases.
- [x] [P44-TS03] Run the existing non-slow extended and live_api selections on the
      reviewed head with hosted credentials. Carry P43-TS03's live gate forward.
- [x] [P44-TS04] Complete the three existing OpenAI, Perplexity and Gemini
      lifecycle smoke nodes. Carry P43-TS04's live gate forward.
- [x] [P44-T05] Review exact source, publish a separate PR and record hosted outcomes.

## Observed acceptance evidence

The fresh [run 37029071053](https://github.com/smorinlabs/doxa-research/actions/runs/37029071053)
attempt 1 concluded success on commit
`b55ef98f6533f224b8adda94755f2898c14941a6`, tree
`30484bf3db216fcaeac55d021b4c68d446c82303`.
Exact test identities, process exits, source, SDK and shared-selection
provenance were independently verified.

| Selection | Passed / selected |
| --- | --- |
| Non-slow extended | 20/20 |
| Non-slow live_api | 31/31 |
| Completed OpenAI lifecycle | 1/1 |
| Completed Perplexity lifecycle | 1/1 |
| Completed Gemini lifecycle | 1/1 |
| Total | 54/54 |

There were no failed, skipped, xfailed or unknown test outcomes. This closes
P44-TS03/P44-TS04 and the carried P43-TS03/P43-TS04 live gates on that commit.
The Gemini lifecycle test verified a nonempty output file and its provenance
in the runner. Its output file was saved outside the collector's temporary
directory and is absent from the sanitized artifact; retained answer text or
cost inspection is not claimed.

The saved-operation audit of this fresh batch identified six IDs needing
independent terminal-status receipts. [GET-only observation run 37030325743](https://github.com/smorinlabs/doxa-research/actions/runs/37030325743)
attempt 1 checked the same commit and received HTTP 200 for all six:

| Provider / saved job | Confirmed upstream status |
| --- | --- |
| OpenAI, four saved jobs | cancelled |
| Perplexity `agent:resp_48c1116d-ae0d-4303-b9ad-bdf17724d708` | completed |
| Perplexity `agent:resp_f4493e92-9fad-4f1f-a5c6-e87e208654b5` | cancelled |

The observer issued six GET requests, created no jobs and cancelled no jobs.
No saved job in this follow-up remains pending or unknown. The test ledger and
sanitized receipts preserve the individual OpenAI IDs and request outcomes.

This follow-up changes only the P43/P44 tracking documents. Runtime, test,
SDK lock and workflow bytes remain those tested at
`b55ef98f6533f224b8adda94755f2898c14941a6`. PR #176 remains open, and merging
it has not been authorized.

Agent preset `high` supplies upstream model/reasoning defaults. Product defaults
are not capped. Live fixtures use max_steps=10/max_output_tokens=8192, retain
known IDs and request best-effort cleanup after failures/timeouts. Cancellation
acknowledgment remains distinct from terminal confirmation. Unknown create
outcomes never trigger an automatic second POST.

## Superseded failed run

[Run 36979307601](https://github.com/smorinlabs/doxa-research/actions/runs/36979307601)
executed once against `4cd777f`: extended 20/20, live_api 30/31, and completed
OpenAI, Perplexity and Gemini lifecycle cases 1/1 each. No case skipped or
xfailed. The workflow conclusion was failure and remains failure.

`test_mismatch_defense_no_http` selected `gpt-5.6-sol`, which supports immediate
submission despite its background default. The repaired guard selects the
background-only `o3-deep-research` local validation fixture, forbids HTTP,
checks the mismatch fields and closes the client. Its separate offline rerun
passed. The fresh 54/54 run on `b55ef98f6533f224b8adda94755f2898c14941a6`
supersedes this run for acceptance. The original workflow conclusion remains
failure; its supplemental offline repair did not itself satisfy the live gate.

That earlier run's saved-operation audit found seven IDs needing independent
terminal-status receipts. [GET-only run 36983115085](https://github.com/smorinlabs/doxa-research/actions/runs/36983115085)
confirmed all seven terminal without creating or cancelling jobs. This
historical reconciliation is separate from the six-ID observation of the
fresh batch above.
