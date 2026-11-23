# P45 - One macOS CI Job per PR, and Working Dependabot Grouping

**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Audit:** CI speed review of 2026-11-22 (12 PR runs of `ci.yml`, 2026-10-21 to 2026-11-18)

**Status:** `[~]` Changes are in the PR. Two checks can only run after merge.

**Goal:** Cut PR CI queue time and stop Dependabot from opening several PRs at once.

- PR runs of `ci.yml` run the Ubuntu test matrix (3.11, 3.12, 3.13) plus one macOS
  job (3.13). Pushes to `main` keep the full 3 x 2 matrix.
- `.github/dependabot.yml` produces one weekly `all-dependencies` PR.

**Out of scope**
- Adding the default pytest suite to CI (investigated separately; owner decision).
- Closing the open per-package and old-group Dependabot PRs.
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
- [ ] [P45-TS02] The PR run shows 3 Ubuntu test jobs and 1 macOS test job.
- [ ] [P45-TS03] After merge: the `main` push run shows all 6 test jobs.
- [ ] [P45-TS04] After merge: the next Dependabot updater job definition shows
      `"multi-ecosystem-update": true` and the `all-dependencies` group, and the
      weekly run opens one PR.
