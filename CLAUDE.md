## Quick Reference

| Need to… | Where to look |
|---|---|
| Run the right test loop | [Fast Iteration Loop](#fast-iteration-loop) > Shortest loop first |
| Commit and pass hooks | [Hook discipline](#hook-discipline) |
| Ship a release | [Release Coordination](#release-coordination) |
| Recover from a broken state | [Recovery Recipes](#recovery-recipes) |
| Start feature work | [Worktree Discipline](#worktree-discipline) |
| Look up API/UV docs | [API and UV References](#api-and-uv-references) |

## Development Principles

- When planning features, design patterns help keep code consistent.
- Shared agent instructions live in `AGENTS.md`; keep this file aligned with it.
- **Test-driven development**: design tests as part of the plan; write tests first or as commit-pairs, then implementation.

## Fast Iteration Loop

The pre-commit hook runs ruff + ty + bandit + gitleaks + codespell + the full `./doxa_test` integration suite (~60s) on every staged `.py` change. **Don't commit between edits just to trigger checks** — run targeted tools directly and commit once when actually done.

### Shortest loop first

Pick the narrowest feedback that can detect your change's problem, then widen:

1. **Inner (1-5s)** — rerun the ONE test you're iterating on:
   - Pytest single test: `uv run pytest tests/test_foo.py::test_bar -x -v`
   - Pytest keyword: `uv run pytest tests/ -k "cancelled" -x`
   - doxa_test by ID: `./doxa_test -r -t M8T-03 -v` (`-t` is substring match on id/description)
2. **Module (5-20s)** — `uv run pytest tests/test_foo.py -v`
3. **Lint/type only (~5s)** — `just check`
4. **Full gate (~60s)** — once, right before commit; the pre-commit hook runs it anyway.

### Finding test IDs to rerun

- **pytest** prints `tests/test_foo.py::test_bar FAILED` — copy the `::`-path.
- **doxa_test** prints `Test M8T-03:` headers in failure blocks and a "To rerun failed tests:" hint at run-end.

Quiet mode for clean failure output (no 64-row noise table):
```bash
./doxa_test -r --provider mock --skip-interactive -q
./doxa_test -r --last-failed -q
./doxa_test -r --id M8T-03 -v
```

### Discovering tests
- TSV list: `./doxa_test --list`
- JSON list: `./doxa_test --list-json`
- Preview filter: `./doxa_test --list --provider mock`
- Run report: `.doxa_test_cache/last_run.json` (schema_version 1; `--report-json PATH` to copy elsewhere)

### Flaky-test retry policy

Network-dependent doxa_test cases can time out at 10s on slow connections (exit code -1). Rerun only the failed one:
```bash
./doxa_test -r --last-failed -q
```

Two consecutive failures of the same test = real bug. One failure = noise.

### Hook discipline

- **Never** `git commit --no-verify` or `LEFTHOOK=0` in normal iteration. The pain is a signal you're committing too often; keep changes uncommitted and run targeted tools until green.
- **Exception**: short-lived WIP commits you'll squash before push are OK with `LEFTHOOK=0` IF you've already run `just check` + `ruff format --check src/ tests/` + targeted tests manually. The **last commit before `git push` MUST** go through full hooks.
- ⚠️ `just check` does NOT run `ruff format --check`. If you bypass hooks, run `ruff format --check` yourself or CI will fail.
- If hooks stop firing entirely (no lefthook output on commit), suspect a stale `core.hooksPath` — see [Recovery Recipes > Hooks stopped firing](#hooks-stopped-firing).

### Periodic full-gate runs

Don't save the full gate for the last commit of a multi-commit task. Run it scaled to commit complexity:

- **Complex commits** (many files, refactor, runtime changes): full gate immediately after.
- **Simple commits** (test additions, doc tweaks): batch at most 2-3 commits between gate runs.
- **Always**: the last commit before `git push` runs the full hook set.

Run via normal `git commit` (which fires lefthook), or manually:
```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run ty check src/
uv run pytest -q
./doxa_test -r --skip-interactive -q
```

## Code Quality Assurance Workflow

For iteration, see [Fast Iteration Loop](#fast-iteration-loop). This is the final pre-commit gate, not for between-edit runs:

1. `make env-check && just fix && just check` — main executable
2. `./doxa_test -r` — tests
3. `just test-fix && just test-lint && just test-typecheck` — test suite

A change is complete only when all of: `make env-check`, `just check`, all tests, `just test-lint`, `just test-typecheck` pass.

### Real-API test suites (gated; NOT in the pre-commit gate)

Two pytest markers gate live-API tests; both deselected by default via `addopts = "-m 'not extended and not live_api'"`.

| Marker | Trigger | Schedule | Purpose |
|---|---|---|---|
| `extended` | `just test-extended` | nightly (09:00 UTC) | model-kind drift vs upstream APIs |
| `live_api` | `just test-live-api` | weekly (Sat 7pm PDT) | CLI workflow regression |

Provider-scoped variants: `just test-extended-{openai,perplexity,gemini}` and `just test-live-api-{openai,perplexity,gemini}`. Manual trigger: `gh workflow run "Extended Contract Tests (nightly)"` or `... "Live-API Workflow Tests (weekly)"`. Both workflows run `continue-on-error: true` (informational) and need `OPENAI_API_KEY`, `PERPLEXITY_API_KEY`, `GEMINI_API_KEY` repo secrets.

## Planning Documents Management

Planning docs live in `planning/`. Versioning: `doxa-research.prd.vNN.md` and `doxa-research.plan.vNN.md` — increment from the highest existing.

When creating a new version, `git mv` the previous one to `archive/` in the same commit:
```bash
git mv planning/doxa-research.prd.v22.md archive/
git add planning/doxa-research.prd.v23.md
git commit -m "Archive v22 PRD and create v23 PRD"
```

API references cache at `planning/references.md`.

## Git Best Practices

**Conventional Commits enforced.** Every commit message: `<type>[scope]: <subject>`. Allowed types: `feat`, `fix`, `perf`, `refactor`, `deps`, `docs`, `test`, `ci`, `chore`, `build`, `style`, `revert`. Use `feat!:` or `BREAKING CHANGE:` footer for major bumps. `deps` is reserved for dependabot bumps (configured via `.github/dependabot.yml`); humans use `chore` for routine maintenance.

**Releases automated by release-please.** Don't hand-edit `pyproject.toml`, `src/doxa_research/__init__.py`, `CHANGELOG.md`, or `.release-please-manifest.json` versions. Land conventional commits on `main` → release-please opens a Release PR → merging tags `vX.Y.Z` and triggers `publish.yml`. See `RELEASE.md`.

Never include in commits:
- `🤖 Generated with [Claude Code](https://claude.ai/code)`
- `Co-Authored-By: Claude <noreply@anthropic.com>`

## Release Coordination

Releases publish to PyPI via `publish.yml` when a `vX.Y.Z` tag is pushed. Chain: Release PR merge → release-please tags → tag push → publish.yml runs → PyPI gate awaits approval → upload. **Direct commits to main never publish.**

### Full release flow

1. Push a release-worthy commit (`feat:`, `fix:`, etc. — NOT `chore:`, which is hidden) → release-please opens a Release PR.
2. Review the PR's CHANGELOG diff for honesty (release-please can propose stale content if the manifest drifted; see CHANGELOG entry for v3.0.6 for a recovery example).
3. Merge the Release PR → release-please tags `vX.Y.Z` via the GitHub App token.
4. `publish.yml` runs: build → TestPyPI (auto OIDC) → PyPI (required-reviewer gate; maintainer approves in Actions UI).
5. On a clean local `main`: `git pull --ff-only origin main` to absorb the release commit; `uv sync` should be a no-op (CI's `sync-uv-lock` job already committed the lock update).

Why the App token vs `GITHUB_TOKEN`: only the App token can retrigger downstream workflows after the tag push. See `RELEASE-PLEASE-APP.md`.

### Main is for releases only

Direct commits to main are reserved for:
- Hot-fix releases sized for a single commit
- Dependabot merges (when CI is green)
- release-please's own bump commits (via Release PR merge)
- Documentation-only changes too small to justify a PR

Everything else: branch → PR → merge. See [Worktree Discipline](#worktree-discipline).

### Tag protection

`publish.yml` verifies the tagged commit is reachable from `origin/main`. Defense in depth — release-please always tags from main so the check is invisible in normal flow but blocks attacker- or accident-tagged non-main commits from triggering a release.

## Worktree Discipline

### Default to worktrees for feature work

For any change beyond a 1-2 line fix, work from a feature branch in a separate worktree, NOT on main directly. You get:

- A working directory immune to release-please activity on main
- Your own remote-tracking branch with `[ahead/behind]` counters against the feature branch
- The main worktree stays at HEAD-of-main, ready for quick sanity checks

```bash
# Setup (from /Users/stevemorin/c/doxa-research):
git worktree add ../doxa-research-worktrees/feat-my-thing -b feat/my-thing
cd ../doxa-research-worktrees/feat-my-thing
# Work, commit, push -u origin feat/my-thing; open PR via `gh pr create`

# Cleanup after merge:
cd /Users/stevemorin/c/doxa-research
git worktree remove ../doxa-research-worktrees/feat-my-thing
git branch -D feat/my-thing                     # if still local
git fetch --prune                               # clear gone remote branches
```

### When NOT to use a worktree

- Single-line typo fixes
- README / CHANGELOG-only edits where conflict probability is near zero
- Emergency hot-fixes where the overhead of branch/PR/merge is too much
- Active session where main is clean AND main hasn't moved since you fetched

Trust your judgment — but when in doubt, branch.

### Session-start prelude

ALWAYS at the top of any commit-producing session:
```bash
git fetch origin
git status -sb     # check [ahead N, behind M]
```

If `[behind M]` with `M > 0`, STOP and `git pull --ff-only origin main` before committing or pushing. If `--ff-only` refuses, you have local commits and origin has moved — that refusal is the signal to use `git pull --rebase origin main` instead. Pushing a stale branch fails with "fetch first" — and any local commits may need re-doing if release-please bumped versions in the meantime.

### After every release-please tag, `uv sync` locally

release-please bumps `pyproject.toml` + `__init__.py` + manifest when it tags. The CI `sync-uv-lock` job in `release-please.yml` auto-commits the new `uv.lock` on the CI side; your local won't have it until pull.

```bash
git switch main
git pull --ff-only origin main     # pulls release bump + CI's lock-sync
uv sync                            # idempotent if CI already synced
```

If `git diff uv.lock` shows changes after `uv sync`, something's out of sync; investigate before committing.

### Don't batch chore-only commits with substantive work

Each chore commit pushes a new origin/main commit, which may race against release-please's PR updates or maintainer merges. Squash multiple chore-style fixes (uv.lock sync, config tweaks) into a single commit at the end of the session.

## Recovery Recipes

### uv.lock conflict during rebase

```bash
git checkout --theirs uv.lock      # origin has the canonical lock (latest dependabot + release-please)
uv sync                            # re-apply your local pyproject changes on top
git add uv.lock
git rebase --continue              # or git commit if not in rebase
```

Why `--theirs`: origin's `uv.lock` incorporates merged dependabot/release-please bumps. Yours is from your local edit session. Origin's is canonical for DEPENDENCIES; we re-apply YOUR pyproject change on top via `uv sync`.

### "Fetch first" push rejection

Origin moved while you were working and you have local commits, so
`--ff-only` cannot apply — this is the documented fallback case for
`--rebase`. Don't force-push.
```bash
git fetch origin
git pull --rebase origin main
git push
```

Repeat if origin moves again during rebase (happens when multiple Release PRs cascade or dependabot is merging).

### Hooks stopped firing

If commits go through with no lefthook output, `core.hooksPath` likely points at a now-missing path (most common after a directory rename):
```bash
git config --get core.hooksPath
ls -d "$(git config --get core.hooksPath)" 2>&1   # path must exist
git config --unset core.hooksPath                  # fall back to in-tree .git/hooks/
# or: lefthook install --force
```

See CONTRIBUTING.md → "If Hooks Stop Firing".

## API and UV References

See `@planning/references.md` for OpenAI, Perplexity, Gemini, and UV documentation URLs.

## OpenAI API Key

Get the OpenAI API key from `@openai.env`.
