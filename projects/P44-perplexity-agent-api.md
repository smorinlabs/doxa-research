# P44 - Perplexity Agent API Migration

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Related:** [P43](P43-httpx2-migration-openai-3x.md), merged PR #172.
- **External:** https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview
- **External:** https://docs.perplexity.ai/openapi.json

**Status:** `[~]` Implementation and offline validation in progress. Live
acceptance remains pending on the reviewed, published commit.

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
- [ ] [P44-T04] Pass required whole-source quality, unit, mock integration and normal hooks.
- [ ] [P44-TS03] Run the existing non-slow extended and live_api selections on the
      reviewed head with hosted credentials. Carry P43-TS03's live gate forward.
- [ ] [P44-TS04] Complete the three existing OpenAI, Perplexity and Gemini
      lifecycle smoke nodes. Carry P43-TS04's live gate forward.
- [ ] [P44-T05] Review exact source, publish a separate PR and record hosted outcomes.

Agent preset `high` supplies upstream model/reasoning defaults. Product defaults
are not capped. Live fixtures use max_steps=10/max_output_tokens=8192, retain
known IDs and request best-effort cleanup after failures/timeouts. Cancellation
acknowledgment remains distinct from terminal confirmation. Unknown create
outcomes never trigger an automatic second POST.
