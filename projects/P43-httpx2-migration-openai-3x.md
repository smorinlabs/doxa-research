# P43 - httpx2 Migration (Unblocks the openai 3.x Upgrade)

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Blocked by nothing; blocks:** any `openai>=3` upgrade.
- **Related:** P147 PR `feat(openai): migrate to gpt-5.6-sol` — the model
  migration deliberately excluded this work to keep the retired-model repair
  reviewable on its own.
- **Related:** `src/doxa_research/providers/perplexity.py` — hand-rolled
  `httpx.AsyncClient` (line 416) plus exception handling bound to httpx
  types at lines 277, 365, 372, 615, 636, 770.
- **Related:** `src/doxa_research/providers/gemini.py` — helper
  `httpx.AsyncClient` use at lines 1019, 1029, 1050, 1054.
- **Related:** `src/doxa_research/providers/openai.py:244` — passes
  `httpx.Timeout(...)` directly into `AsyncOpenAI`.

**Status:** `[ ]` Scoped, not started.

**Goal**: Migrate every HTTP client in the codebase from `httpx` to
`httpx2`, then upgrade the OpenAI SDK from 2.37.0 to 3.x.

These are one project, not two. `openai` 3.0.0's single documented breaking
change is that **HTTPX2 became the default HTTP client and `httpx` is no
longer installed automatically**; `openai` 3.9.0 declares
`httpx2>=2.7.0,<3` and does not depend on `httpx` at all. Doxa's Perplexity
provider is hand-rolled on `httpx` and its Gemini provider uses `httpx`
helpers, so upgrading the SDK without migrating them leaves two HTTP stacks
in one process and a concrete type collision at `openai.py:244`, which hands
an `httpx.Timeout` object to a client that will expect the httpx2 equivalent.

**Owner direction (2026-09-08)**: stay on a raw HTTP client for Perplexity —
do **not** adopt the official `perplexityai` SDK (0.43.5) — and move that
client to httpx2 the way OpenAI did.

**Out of Scope**
- Adopting the `perplexityai` SDK.
- `google-genai`, already current at 2.22.0 as of 2026-09-08; the Gemini work
  here is only its `httpx` helper calls, not the SDK.

### Tests & Tasks
- [ ] [P43-T01] Survey every `httpx` import and type reference across `src/`
      and `tests/`; record the httpx2 equivalent for each.
- [ ] [P43-T02] Migrate `providers/perplexity.py` to `httpx2`, including the
      six exception-handling sites bound to `httpx.HTTPStatusError`,
      `httpx.TimeoutException` and `httpx.ConnectError`.
- [ ] [P43-T03] Migrate the `providers/gemini.py` helper client calls.
- [ ] [P43-T04] Replace the `httpx.Timeout` passed into `AsyncOpenAI` at
      `providers/openai.py:244`.
- [ ] [P43-T05] Bump `openai>=3.9.0`, swap `httpx` for `httpx2` in
      `pyproject.toml`, and re-lock.
- [ ] [P43-TS01] Full suite green, including `tests/extended/` — which the
      default `pytest` run deselects, so it must be invoked explicitly.
- [ ] [P43-TS02] Live smoke test per provider: one background OpenAI call,
      one Perplexity async call, one Gemini Deep Research call.
- [ ] [P43-TS03] Confirm no `httpx` (v1) remains in `uv.lock`.

### Automated Verification
- `just all` passes.
- `uv run pytest tests/extended/` passes with live credentials.
- `uv tree | grep -c '^.*httpx '` returns 0.

### Manual Verification
- `./doxa ask -m all_deep_research` completes across all three providers.
