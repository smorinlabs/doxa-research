# P45 - One macOS CI Job per PR, and Working Dependabot Grouping

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Audit:** CI speed review of 2026-11-22 (12 PR runs of `ci.yml`, 2026-10-21 to 2026-11-18)

**Status:** `[x]` PR #200 merged as `7528cea`; post-merge checks passed. The pytest
job is added in PR #202.

**Goal:** Cut PR CI queue time and stop Dependabot from opening several PRs at once.

- PR runs of `ci.yml` run the Ubuntu test matrix (3.11, 3.12, 3.13) plus one macOS
  job (3.13). Pushes to `main` keep the full 3 x 2 matrix.
- `.github/dependabot.yml` produces one weekly `all-dependencies` PR.
- The default pytest selection (1,862 tests) runs on every PR and push, in one
  Ubuntu job on Python 3.13.

**Out of scope**
- The same Dependabot defect in other smorinlabs repositories.

**TDD: not applicable.** CI configuration only. Verification is actionlint,
yamllint, and observed CI and Dependabot runs.

## Tests and tasks

- [x] [P45-T01] Check required status checks: no branch protection, and the only
      ruleset (`merge-methods: no squash`) requires no checks.
- [x] [P45-T02] Build the `test` matrix from Ubuntu x 3 plus an event-dependent
      `include` (one macOS cell on PRs, three on push).
- [x] [P45-T03] Find why the multi-ecosystem group still opened separate PRs, and
      remove `open-pull-requests-limit` from both grouped entries.
- [x] [P45-TS01] actionlint and yamllint pass; both `include` JSON literals parse.
- [x] [P45-TS02] The PR run shows 3 Ubuntu test jobs and 1 macOS test job.
- [x] [P45-TS03] After merge: the `main` push run shows all 6 test jobs.
      Run 45220683797 on `7528cea`: 6 test jobs green, 117 s wall-clock.
- [x] [P45-TS04] After merge: the next Dependabot updater job definition shows
      `"multi-ecosystem-update": true` and the `all-dependencies` group, and the
      weekly run opens one PR. Confirmed on 2026-11-23: both updater jobs ran
      with the new definition and opened one grouped PR, #201.
- [x] [P45-T04] Close the ten old-style Dependabot PRs that #201 supersedes
      (#185, #187, #190-#193, #196-#199), each with a comment.
- [x] [P45-T05] Add a `pytest` job to `ci.yml`: Ubuntu, Python 3.13,
      `uv run --frozen pytest -n auto -q`, outside the macOS matrix.
- [x] [P45-TS05] The pytest job passes in PR CI with 1,862 tests; record its
      duration and whether it lengthens the critical path. PR #202 run
      45222325939: 1,862 passed in 51.4 s; the job took 65 s. The macOS test
      job (88 s) stays the critical path. The first run exposed a missing
      `just` binary on Ubuntu, fixed by `extractions/setup-just`.
