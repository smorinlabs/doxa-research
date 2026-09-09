# P43 - httpx2 Migration (Unblocks the openai 3.x Upgrade)

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Related:** PR #147 `feat(openai): migrate to gpt-5.6-sol` — the model
  migration deliberately excluded this work to keep the retired-model repair
  reviewable on its own.
- **Related:** `src/doxa_research/providers/perplexity.py` — hand-rolled
  `httpx.AsyncClient` (line 416) plus exception handling bound to httpx types
  at lines 277, 365, 372, 615, 636, 770.
- **Related:** `src/doxa_research/providers/gemini.py` — helper
  `httpx.AsyncClient` use at lines 1019, 1029, 1050, 1054.
- **Related:** `src/doxa_research/interactive.py` — imports httpx (line 16),
  retries on `httpx.TimeoutException`/`httpx.ConnectError` (line 893), and
  passes `httpx.Timeout` into `AsyncOpenAI` (line 897).
- **Related:** `src/doxa_research/providers/openai.py:244` — passes
  `httpx.Timeout(...)` directly into `AsyncOpenAI`.
- **Related:** `doxa` (PEP 723 launcher, lines 4-15) — an independent
  dependency manifest still pinning `openai>=1.14.0` and `httpx>=0.27.0`.

**Status:** `[ ]` Scoped, not started.

**Goal**: Migrate every first-party HTTP client from `httpx` to `httpx2`, then
upgrade the OpenAI SDK from 2.37.0 to 3.x.

These are one project, not two. `openai` 3.0.0's single documented breaking
change is that **HTTPX2 became the default HTTP client and `httpx` is no longer
installed automatically**; `openai` 3.9.0 declares `httpx2>=2.7.0,<3` and does
not depend on `httpx` at all. Doxa's Perplexity provider is hand-rolled on
`httpx`, its Gemini provider and interactive flow use `httpx` helpers, and
`openai.py:244` hands an `httpx.Timeout` object to a client that will expect the
httpx2 equivalent. Upgrading the SDK alone therefore leaves two HTTP stacks in
one process plus a concrete type collision.

**Owner direction (2026-09-08)**: keep a raw HTTP client for Perplexity — do
**not** adopt the official `perplexityai` SDK (0.43.5) — and move that client to
httpx2 the way OpenAI did.

**httpx v1 will remain in the lock file, by design.** `google-genai` 2.22.0
itself declares `httpx>=0.27.0` (`uv.lock`), so httpx cannot leave the
dependency graph while the Gemini SDK is in use. The goal is that **no
first-party Doxa module imports `httpx`**, not that the package is absent.

**Out of Scope**
- Adopting the `perplexityai` SDK.
- Replacing or forking `google-genai` to drop its transitive `httpx`.
- `google-genai` version work: already current at 2.22.0 as of 2026-09-08.

### Tests & Tasks

Test design comes first: each migration task is driven by the regression tests
written in the task above it.

- [ ] [P43-TS01] Write the httpx2 equivalence tests for the Perplexity client:
      status mapping, timeout behaviour, and each exception type currently
      caught (`HTTPStatusError`, `TimeoutException`, `ConnectError`).
- [ ] [P43-T01] Survey every `httpx` import and type reference across `src/`
      and `tests/`; record the httpx2 equivalent for each.
- [ ] [P43-T02] Migrate `providers/perplexity.py` to `httpx2`, driven by TS01.
- [ ] [P43-TS02] Write the equivalence tests for the Gemini helper client and
      the interactive flow's retry predicates.
- [ ] [P43-T03] Migrate the `providers/gemini.py` helper client calls.
- [ ] [P43-T04] Migrate `interactive.py`: the import, the retry predicate, and
      the `httpx.Timeout` passed into `AsyncOpenAI`.
- [ ] [P43-T05] Replace the `httpx.Timeout` passed into `AsyncOpenAI` at
      `providers/openai.py:244`.
- [ ] [P43-T06] Bump `openai>=3.9.0` and swap `httpx` for `httpx2` in
      `pyproject.toml`; re-lock.
- [ ] [P43-T07] Update the `doxa` PEP 723 launcher manifest (lines 4-15), which
      carries its own pins. The v3.2.1 release nearly shipped with a fixed
      package and an unfixed launcher; `doxa_test` executes `./doxa`, so both
      manifests must move together.
- [ ] [P43-TS03] Full suite green, plus `tests/extended/` — see the runner note
      below; the default `pytest` invocation deselects them.
- [ ] [P43-TS04] Live smoke test per provider: one background OpenAI call, one
      Perplexity async call, one Gemini Deep Research call.

### Automated Verification

```bash
make env-check          # dependency preflight
just all                # format, lint, typecheck, security, test
just test-lint          # the test suite has its own lint/typecheck gates
just test-typecheck
just test-extended      # NOT `pytest tests/extended/`: pyproject.toml sets
                        # addopts = "-m 'not extended and not live_api'", which
                        # deselects every test in that directory and exits 5.
```

No first-party module imports httpx (httpx itself stays in the lock via
`google-genai`):

```bash
if rg -l '^import httpx|^from httpx' src/doxa_research/ | grep -q .; then
  echo "FAIL: first-party httpx imports remain"; exit 1
else
  echo "OK: no first-party httpx imports"
fi
```

The explicit if/else matters: `grep -c` exits 1 when it finds nothing, so a
bare count pipeline would fail the gate at exactly the moment the goal is met.

### Manual Verification

```bash
./doxa ask -m all_deep_research -q "What changed in HTTP client libraries in 2026?"
```

The `ask` subcommand requires a prompt (`cli_subcommands/ask.py:132-133`
rejects an invocation without one before any provider is contacted), so the
`-q` argument is required for this to exercise anything.
