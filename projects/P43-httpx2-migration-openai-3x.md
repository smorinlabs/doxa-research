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

**httpx v1 stays, in the lock file and in one module, by design.**
`google-genai` 2.22.0 declares `httpx>=0.27.0` (`uv.lock`), so httpx cannot
leave the dependency graph while the Gemini SDK is in use. It also cannot leave
`gemini.py`: that provider translates google-genai's *transport* exceptions,
catching `httpx.TimeoutException`, `httpx.ConnectError`,
`httpx.RemoteProtocolError` and `httpx.RequestError` at `gemini.py:312-338`.
Those objects are raised by the SDK's own httpx client, so the import must
remain or Gemini error handling silently stops matching.

The goal is therefore narrower than "no httpx imports": **no first-party module
constructs an httpx client or hands httpx types to the OpenAI SDK.**
`gemini.py` keeps httpx solely for exception translation.

**Out of Scope**
- Adopting the `perplexityai` SDK.
- Replacing or forking `google-genai` to drop its transitive `httpx`.
- `google-genai` version work: already current at 2.22.0 as of 2026-09-08.

## Tests & Tasks

Test design comes first: each migration task is driven by the regression tests
written in the task above it.

- [ ] [P43-T00] Add `httpx2` to `pyproject.toml` and sync, **before** any
      consumer is migrated. T02 onwards import httpx2, so declaring it only at
      the dependency-swap step would leave every intermediate commit unable to
      run. Removing `httpx` and bumping `openai` stays in T06, once nothing
      first-party imports the old transport.
- [ ] [P43-TS00] Write the characterisation test for Gemini's retained httpx
      exception boundary (`gemini.py:312-338`) before anything moves, so the
      one module that must keep catching google-genai's transport errors has a
      regression test proving it still does.
- [ ] [P43-TS01] Write the httpx2 equivalence tests for the Perplexity client:
      status mapping, timeout behaviour, and each exception type currently
      caught (`HTTPStatusError`, `TimeoutException`, `ConnectError`).
- [ ] [P43-T01] Survey every `httpx` import and type reference across `src/`
      and `tests/`; record the httpx2 equivalent for each. Includes the
      existing Perplexity suite, which asserts on httpx types directly
      (`tests/test_provider_perplexity_async.py:407-419`).
- [ ] [P43-T02] Migrate `providers/perplexity.py` to `httpx2`, driven by TS01.
- [ ] [P43-TS02] Write the equivalence tests for the Gemini helper client, the
      interactive flow's retry predicates, and the timeout object handed to
      `AsyncOpenAI` — the type swap there is the concrete collision, so it is
      tested before T04 and T05 rather than alongside them.
- [ ] [P43-T03] Migrate the `providers/gemini.py` helper client calls.
- [ ] [P43-T04] Migrate `interactive.py`: the import, the retry predicate, and
      the `httpx.Timeout` passed into `AsyncOpenAI`.
- [ ] [P43-T05] Replace the `httpx.Timeout` passed into `AsyncOpenAI` at
      `providers/openai.py:244`.
- [ ] [P43-T06] Bump `openai>=3.9.0` and swap `httpx` for `httpx2` in
      `pyproject.toml`; re-lock, and regenerate the tracked `requirements.txt`
      export so it does not keep advertising the old transport.
- [ ] [P43-T08] Move the Dependabot `openai-stack` group to httpx2
      (`.github/dependabot.yml:36-41` still patterns on `httpx`), so the group
      keeps updating the transport in lock-step with the SDK.
- [ ] [P43-T09] Update the provider transport guidance in
      `src/doxa_research/providers/CLAUDE.md:150-155`, which documents the
      current raw-httpx contract for Perplexity.
- [ ] [P43-T07] Update the `doxa` PEP 723 launcher manifest (lines 4-15), which
      carries its own pins. The v3.2.1 release nearly shipped with a fixed
      package and an unfixed launcher; `doxa_test` executes `./doxa`, so both
      manifests must move together.
- [ ] [P43-TS03] Full suite green, plus `tests/extended/` — see the runner note
      below; the default `pytest` invocation deselects them.
- [ ] [P43-TS04] Live smoke test per provider: one background OpenAI call, one
      Perplexity async call, one Gemini Deep Research call.

## Automated Verification

```bash
make env-check          # dependency preflight
just all                # format, lint, typecheck, security, test
just test-lint          # the test suite has its own lint/typecheck gates
just test-typecheck
just test-extended      # NOT `pytest tests/extended/`: pyproject.toml sets
                        # addopts = "-m 'not extended and not live_api'", which
                        # deselects every test in that directory and exits 5.
# The live suite needs real credentials. Without them it deselects or skips
# and passes vacuously, which is not verification — so assert they exist first.
for k in OPENAI_API_KEY PERPLEXITY_API_KEY GEMINI_API_KEY; do
  [ -n "${!k:-}" ] || { echo "FAIL: $k unset; live verification cannot run"; exit 1; }
done
uv run pytest -m live_api -v   # test-extended runs `-m "extended and not
                        # extended_slow"` only, so the live_api suite needs its
                        # own invocation; this migration changes the transport
                        # every live call rides on.
```

No first-party module imports httpx (httpx itself stays in the lock via
`google-genai`):

```bash
# \b after httpx is required: rg takes a regular expression, so a bare
# `httpx` also matches every `httpx2` import and the check would fail at
# exactly the moment the migration succeeds.
offenders=$(rg -l '^\s*(import|from) httpx\b' src/doxa_research/ \
             --glob '!**/providers/gemini.py' || true)
if [ -n "$offenders" ]; then
  echo "FAIL: first-party httpx clients remain:"; echo "$offenders"; exit 1
else
  echo "OK: only gemini.py retains httpx, for google-genai exception translation"
fi
```

Three things this expression gets right that the obvious form does not. The
word boundary stops `httpx` matching `httpx2`. The exclusion glob needs the
`**/` prefix: `!providers/gemini.py` does not match when the search path is
`src/doxa_research/`, verified by running both forms. The exclusion itself is
deliberate, per the scope note above. The explicit if/else matters too, because
`grep -c` exits nonzero when it finds nothing and would fail the gate exactly
when the goal is met.

## Manual Verification

```bash
./doxa ask -m all_deep_research -q "What changed in HTTP client libraries in 2026?"
```

The `ask` subcommand requires a prompt (`cli_subcommands/ask.py:132-133`
rejects an invocation without one before any provider is contacted), so the
`-q` argument is required for this to exercise anything.
