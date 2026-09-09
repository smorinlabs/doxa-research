# Projects

Index of every project in this repo. Per-project files live in
`projects/`. Skills in the `project-harness` plugin operate on this
file plus those.

## Status legend

| Glyph | Meaning                | File state                              |
|-------|------------------------|-----------------------------------------|
| `[?]` | Idea / exploratory     | stub file, trailing-hyphen filename     |
| `[ ]` | Scoped, not started    | full scope + tasks                      |
| `[~]` | In progress            | full + some `[x]` tasks                 |
| `[x]` | Completed              | all tasks `[x]`                         |
| `[-]` | Decided not to do      | reason at top of file                   |
| `[>]` | Proceeded to successor | redirects to successor's file           |

## Conventions

- Filename: `projects/P<NN>-<kebab-slug>.md` for scoped/active/done;
  `projects/P<NN>-<roughName>-.md` (trailing hyphen) for ideas.
- Trunk row: `- [<glyph>] **P<NN>** — [<title>](projects/<filename>)`.
  The title is a standard markdown link to the per-project file —
  external tools (markdown linters, doc generators, AI agents
  reading the repo without project-harness loaded) can parse the
  schema without prior knowledge of the conventions.
- Every non-`[?]` per-project file has a `**References**` block at
  the top whose first bullet is `- **Trunk:** [PROJECTS.md](../PROJECTS.md)`,
  the back-pointer to this file. Bidirectional schema.
- One trunk row per file; one file per trunk row.
- Status glyph mirrors the rolled-up state of the per-project file.
- Flip task checkboxes immediately as each task lands (no batching).
- TDD bias: every project has at least one `TS` task before its first
  `T` task. Opt out with `**TDD: not applicable**` in the body.
- New projects are added near the top in descending P-number order
  unless the user requests a specific ordering.

### Task ID key

- `P##` — Project number, for example `P21`
- `P##-TS##` — Test/design task for that project
- `P##-T##` — Implementation, documentation, or verification task
- Suffix letters such as `P21-T09a` — inserted follow-up task that
  preserves existing numbering

### Reference labels (used inside per-project files)

```markdown
**References**
- **Trunk:** [PROJECTS.md](../PROJECTS.md)
- **Spec:** `docs/superpowers/specs/...`
- **Plan:** `docs/superpowers/plans/...`
- **Research:** `research/...`
- **Audit:** `planning/...`
- **External:** https://...
```

Older entries may use `**Primary spec**`, `**Plan**`, or
`**Research basis**`; when editing those, normalize to the
`**References**` block above.

### Project workflow skills (plugin: project-harness)

- `using-project-harness` — bootstrap: when to use which skill below
- `project-next` — orient: what's in progress, what's next, what's recently touched
- `project-add` — capture an idea (≤3 questions, reserves the ID with a commit)
- `project-refine` — flesh out / scope / decompose an existing project
- `project-audit` — verify state matches conventions; fix per finding

## Project index

<!-- Descending P-number order. Scoped / in-progress / done first;
     ideas last. One row per file; one file per row. -->

- [ ] **P43** — [httpx2 Migration (Unblocks the openai 3.x Upgrade)](projects/P43-httpx2-migration-openai-3x.md)
- [~] **P42** — [Shorten the Doxa Skill Description to Action-Scoped Triggers](projects/P42-doxa-skill-description.md)
- [ ] **P41** — [`--no-validate` Redesign (Make the Flag Mean What It Says)](projects/P41-no-validate-redesign.md)
- [x] **P40** — [On-Disk Starter Template (Reverse P33 Generation)](projects/P40-on-disk-starter-template.md)
- [?] **P39** — [Pluggy-Based Plugin System for Providers & Commands](projects/P39-pluggy-plugin-system-.md)
- [ ] **P38** — [Perplexity Sync VCR Replay](projects/P38-perplexity-sync-vcr-replay.md)
- [?] **P37** — [Review starter-profile selection](projects/P37-starter-profile-review-.md)
- [x] **P36** — [Uniform `--help` + Useful API-Key Errors](projects/P36-help-uniform-errors.md)
- [x] **P35** — [Modes Set-Default / Unset-Default Commands](projects/P35-modes-set-default.md)
- [?] **P34** — [Offline testing for OpenAIProvider.stream() (VCR or alternative)](projects/P34-offline-testing-openai-stream-.md)
- [x] **P33** — [Schema-Driven Config Defaults](projects/P33-schema-driven-config-defaults.md)
- [ ] **P32** — [Interactive Prompt Refiner](projects/P32-interactive-prompt-refiner.md)
- [x] **P31** — [Interactive Init Command](projects/P31-interactive-init-command.md)
- [ ] **P30** — [Claude Code Skills Support](projects/P30-claude-code-skills-support.md)
- [ ] **P29** — [Architecture Review & Cleanup — Background Deep Research Providers](projects/P29-arch-review-background-deep-research.md)
- [x] **P28** — [Gemini — Background Deep Research](projects/P28-gemini-background-deep-research.md)
- [x] **P27** — [Perplexity — Background Deep Research](projects/P27-perplexity-background-deep-research.md)
- [x] **P26** — [OpenAI — Background Deep Research](projects/P26-openai-background-deep-research.md)
- [ ] **P25** — [Architecture Review & Cleanup — Immediate Providers](projects/P25-arch-review-immediate-providers.md)
- [x] **P24** — [Gemini — Immediate (Synchronous) Calls](projects/P24-gemini-immediate-sync.md)
- [x] **P23** — [Perplexity — Immediate (Synchronous) Calls](projects/P23-perplexity-immediate-sync.md)
- [x] **P22** — [OpenAI — Immediate (Synchronous) Calls](projects/P22-openai-immediate-sync.md)
- [x] **P21c** — [Config Filename Standardization](projects/P21c-config-filename-standardization.md)
- [x] **P21b** — [Configuration Profile CRUD Commands](projects/P21b-configuration-profile-crud.md)
- [x] **P21** — [Configuration Profile Resolution & Overlay](projects/P21-configuration-profile-resolution.md)
- [x] **P20** — [Live-API Workflow Regression Suite](projects/P20-live-api-workflow.md)
- [x] **P18** — [Immediate vs Background — Explicit `kind`, Runtime Mismatch, Path Split, Streaming, Cancel](projects/P18-immediate-vs-background-kind.md)
- [x] **P17** — [doxa-research-ergonomics-v1 Spec Round-Trip — Annotate Implementation Status](projects/P17-ergonomics-spec-round-trip.md)
- [x] **P16 PR3** — [Automation Polish — `completion` subcommand + universal `--json`](projects/P16-PR3-automation-polish.md)
- [x] **P16 PR2** — [Remove Legacy Shims, Add resume + ask Subcommands](projects/P16-PR2-remove-legacy-shims.md)
- [x] **P16 PR1** — [Click-Native CLI Refactor — Subcommand Migration & Parity Gate](projects/P16-PR1-click-native-cli-refactor.md)
- [x] **P15** — [P14 Bug Fixes — pick-model gating, spinner-progress conflict, prompt-file caps](projects/P15-p14-bug-fixes.md)
- [x] **P14** — [Doxa Research CLI Ergonomics v1](projects/P14-doxa-research-cli-ergonomics-v1.md)
- [x] **P13** — [P11 Follow-up — is_background_model overload + shared secrets + regression tests](projects/P13-p11-followup-is-background-model.md)
- [x] **P12** — [CLI Mode Editing — `doxa-research modes` mutations](projects/P12-cli-mode-editing.md)
- [x] **P11** — [`doxa-research modes` Discovery Command](projects/P11-doxa-research-modes-discovery.md)
- [x] **P10** — [Config Subcommand + XDG Layout](projects/P10-config-subcommand-xdg.md)
- [x] **P09** — [Decompose __main__.py + AppContext DI + Provider Registry](projects/P09-decompose-main-appcontext-di.md)
- [x] **P08** — [Typed Exceptions, Unified API Key Resolution, Drop Legacy Config Shim](projects/P08-typed-exceptions-api-key-resolution.md)
- [x] **P06** — [Hybrid Transient/Permanent Error Handling with Resumable Recovery](projects/P06-hybrid-error-handling-resumable.md)
- [x] **P05** — [VCR Cassette Replay Tests](projects/P05-vcr-cassette-replay-tests.md)
- [x] **P04** — [GAP-01 — max_tool_calls safeguard and tool-selection config](projects/P04-gap-01-max-tool-calls.md)
- [x] **P03** — [Fix BUG-03 OpenAI Poll Interval Scheduling](projects/P03-bug-03-openai-poll-interval.md)
- [x] **P02** — [Fix BUG-01 OpenAI Background Status Handling](projects/P02-bug-01-openai-background-status.md)
- [x] **P01** — [Developer Tooling & Automation](projects/P01-developer-tooling.md)
