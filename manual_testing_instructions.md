# Manual Testing Guide

> Updated for P14 (Doxa Research CLI Ergonomics v1) — the most recent batch of CLI changes. Earlier sections covering response-storage and per-config timeouts have been archived.

## Prerequisites

```bash
# 1. Bootstrap environment (one-time)
make env-check                  # Confirms uv, python3, just, bun present

# 2. Install dependencies
just install                    # Or: just install-dev (also installs git hooks)

# 3. Ensure API keys (env-vars are recommended)
export OPENAI_API_KEY=sk-...
# Optional: source openai.env if you keep it in a file
```

---

## Smoke Tests (run first)

```bash
# 1. Help text renders the new structure
uv run doxa-research --help | head -60
# Expected: top "Commands:" section, "Research Modes:" with the workflow chain
#           line "clarification → exploration → deep_dive → tutorial → solution → prd → tdd"
#           and Examples: a quick prompt, a chained --auto run, --resume, and a -v debug example.

# 2. Version
uv run doxa-research --version
# Expected: v2.5.0 (or current)

# 3. Authentication help (P14 feature)
uv run doxa-research --help auth
# Expected: 3-section block: Environment variables → Config file (with [providers.openai] visible) → CLI flags.

# 4. The other in-CLI help topic still works
uv run doxa-research help modes
# Expected: per-mode listing with provider/model/kind columns.

# 5. End-to-end mock run (no real API calls)
uv run doxa-research "smoke test prompt" --provider mock
# Expected: writes a file like 2026-04-25_HHMMSS_default_mock_*.md in the cwd.
```

---

## P14 Feature Tests

### A. `doxa-research providers` subcommand group

```bash
# A1. New `list` subcommand
uv run doxa-research providers list
# Expected: "Configured providers:" then "openai", "perplexity" with "key set" or "no key"
#           depending on whether $OPENAI_API_KEY / $PERPLEXITY_API_KEY are exported.

# A2. New `models` subcommand
uv run doxa-research providers models
# Expected: per-provider sections listing the models referenced in BUILTIN_MODES.

# A3. New `check` subcommand
uv run doxa-research providers check
# Expected: exit 0 + "All providers have keys set" if both env vars are set,
#           OR exit 2 + "Missing keys for: <names>" if any are missing.
echo "exit=$?"

# A4. Deprecation shim — old form still works with a warning
uv run doxa-research providers -- --list 2>&1 | head -5
# Expected: first line on stderr is
#   warning: 'doxa-research providers -- ...' is deprecated; use 'doxa-research providers list|models|check'
# followed by the legacy provider table.
```

### B. Workflow chain + worked examples in `--help`

```bash
# B1. Verify the workflow chain line appears
uv run doxa-research --help | grep -F "clarification → exploration → deep_dive"

# B2. Verify the chained-with-async example appears
uv run doxa-research --help | grep -F 'doxa-research deep_research --auto --project k8s --async'

# B3. Verify the resume example appears
uv run doxa-research --help | grep -F 'doxa-research --resume op_abc123'

# B4. Verify the -v debug example appears
uv run doxa-research --help | grep -F 'Debug API issues'
```

### C. `--input-file` / `--auto` clearer help

```bash
# C1. --auto mentions "happy path"
uv run doxa-research --help | grep -F "happy path for chaining modes"

# C2. --input-file mentions "non-doxa-research document"
uv run doxa-research --help | grep -F "non-doxa-research document"
```

### D. API-key documentation pass

```bash
# D1. CLI-flag help is softened (now says "not recommended")
uv run doxa-research --help | grep -F "not recommended"
# Expected: at least three matches (--api-key-openai, --api-key-perplexity, --api-key-mock).

# D2. README authentication section
sed -n '/^## Authentication/,/^## /p' README.md | head -25
# Expected: 3-step ranked list (env vars > config file > CLI flags last) plus a
#           pointer to `doxa-research help auth`.

# D3. `doxa-research help auth` matches `doxa-research --help auth`
diff <(uv run doxa-research help auth) <(uv run doxa-research --help auth)
# Expected: no diff — both routes produce the same output.
```

### E. `APIKeyError` surfaces config path

```bash
# E1. Trigger a missing-key error (unset OPENAI_API_KEY for this test)
unset OPENAI_API_KEY
uv run doxa-research "test" --provider openai 2>&1 | head -10
# Expected: error message mentions
#   - "openai API key not found"
#   - "Set OPENAI_API_KEY (or edit /Users/<you>/.config/doxa-research/doxa-research.config.toml)"
#   - "Config file: /Users/<you>/.config/doxa-research/doxa-research.config.toml  (does not exist|exists)"
#   - "Env checked: OPENAI_API_KEY (unset)"
echo "exit=$?"
# Expected exit: 2

# Restore for later tests
export OPENAI_API_KEY=sk-...
```

### F. Progress spinner (sync background-mode runs)

> Requires a real OpenAI key. The spinner is gated to TTY + sync + non-verbose + background model.

```bash
# F1. Spinner shows during sync deep-research (interactive terminal)
uv run doxa-research deep_research "explain DNS in 50 words" --provider openai
# Expected: live spinner reading
#   "<label> · ~20 min expected · Ctrl-C to background"
# completes when the result is written, then "Deep research running complete".

# F2. Spinner suppressed in --async mode
uv run doxa-research deep_research "explain TLS in 50 words" --provider openai --async
# Expected: prints an operation ID immediately, no spinner.

# F3. Spinner suppressed in -v verbose mode
uv run doxa-research deep_research "explain HTTPS" --provider openai -v 2>&1 | head -5
# Expected: raw [doxa-research] log lines, no spinner.

# F4. Spinner suppressed for non-TTY (piped output)
uv run doxa-research deep_research "x" --provider openai | head -5
# Expected: completes without ANSI cursor noise; just plain output.

# F5. Spinner suppressed for quick (non-background) modes
uv run doxa-research "quick test" --provider openai
# Expected: default mode is o3 (immediate); no spinner appears.
```

### G. SIGINT "Resume later" hint

```bash
# G1. Start a slow run and Ctrl-C it
uv run doxa-research deep_research "long topic" --provider openai
# Press Ctrl-C after a few seconds.
# Expected output lines:
#   - "Checkpoint saved. Resume with: doxa-research --resume op_<id>"   (existing, Rich green ✓)
#   - "Resume later: doxa-research --resume op_<id>"                    (new in P14)

# G2. Resume the cancelled job
uv run doxa-research --resume op_<id>
# Expected: picks up where it left off and finishes.
```

### H. `--pick-model` / `-M` flag

> Picker is gated to immediate (non-background) modes only.

```bash
# H1. Rejected on background-mode modes
uv run doxa-research --pick-model deep_research "test" 2>&1 | head -6
echo "exit=$?"
# Expected: 5-line error mentioning "only supported for quick (non-deep-research)",
#           the offending model name, and the config-file remediation hint.
# Expected exit: 2

# H2. Rejected on exploration mode (also background)
uv run doxa-research -M exploration "test" 2>&1 | head -3
echo "exit=$?"
# Expected exit: 2

# H3. Picker shown for default (immediate) mode
uv run doxa-research --pick-model default "smoke test"
# Expected: numbered list of OpenAI immediate models (o3, gpt-4o, gpt-4o-mini, …),
#           prompt "Pick a model", and after you type a number, the run proceeds
#           with that model overriding the mode default.

# H4. Auto-pick via stdin (non-interactive)
echo 1 | uv run doxa-research --pick-model default "noninteractive smoke" --provider mock
# Expected: picks the first listed model and runs against mock provider.
```

---

## P18 Feature Tests

> Covers the immediate-vs-background path split, streaming output, `doxa-research cancel`,
> the `doxa-research modes --kind` filter, runtime kind/model mismatch detection, the
> `mini_research` → `quick_research` rename, and the user-mode missing-`kind`
> warn-once at config load.

### I. Immediate vs background path

```bash
# I1. Immediate mode emits no operation ID, no spinner, no resume hint
uv run doxa-research ask "what is X" --mode thinking --provider mock 2>&1 | tee /tmp/doxa-research-i1.txt
# Expected: streams the answer to stdout in seconds, NO line beginning
#           "Operation ID:", NO "doxa-research resume", NO spinner.
grep -E "Operation ID|doxa-research resume" /tmp/doxa-research-i1.txt && echo "FAIL: leakage" || echo "ok"

# I2. Background mode keeps the existing UX
uv run doxa-research ask "topic" --mode deep_research --async --provider mock 2>&1 | head -8
# Expected: prints "Operation ID: op_<id>" and submits without blocking.

# I3. Immediate mode WITH --project still persists + emits op-id
uv run doxa-research ask "test" --mode thinking --project demo --provider mock 2>&1 | grep -E "Operation ID"
# Expected: an Operation ID line is present (persistence escape hatch).
```

### J. Streaming output (`--out`)

```bash
# J1. Stdout sink (default)
uv run doxa-research ask "hello" --mode thinking --out - --provider mock
# Expected: streamed chunks land on stdout; nothing written to disk.

# J2. File sink
uv run doxa-research ask "hello" --mode thinking --out /tmp/doxa-research-j2.md --provider mock
test -s /tmp/doxa-research-j2.md && echo "ok: file written"
# Expected: /tmp/doxa-research-j2.md exists and is non-empty.

# J3. Tee sink (stdout + file)
uv run doxa-research ask "hello" --mode thinking --out -,/tmp/doxa-research-j3.md --provider mock | head -5
test -s /tmp/doxa-research-j3.md && echo "ok: file also written"
# Expected: output appears on stdout AND in /tmp/doxa-research-j3.md.

# J4. Append vs default-truncate
uv run doxa-research ask "first"  --mode thinking --out /tmp/doxa-research-j4.md --provider mock
uv run doxa-research ask "second" --mode thinking --out /tmp/doxa-research-j4.md --append --provider mock
wc -l /tmp/doxa-research-j4.md
# Expected: file contains both runs concatenated. Without --append, second run truncates.

# J5. Lazy file open — aborted submit must not leave an empty file
rm -f /tmp/doxa-research-j5.md
uv run doxa-research ask "x" --mode quick_research --model o3 --out /tmp/doxa-research-j5.md --provider mock 2>&1 | head -3
# Expected: ModeKindMismatchError (see section M), and /tmp/doxa-research-j5.md does NOT exist.
test -e /tmp/doxa-research-j5.md && echo "FAIL: empty file left behind" || echo "ok: lazy open"
```

### K. `doxa-research cancel <op-id>`

```bash
# K1. Cancel an in-flight background op
OPID=$(uv run doxa-research ask "long topic" --mode deep_research --async --provider mock --json | jq -r .operation_id)
uv run doxa-research cancel "$OPID"
# Expected: exit 0, summary line says "cancelled", local checkpoint updated.

# K2. Status after cancel
uv run doxa-research status "$OPID" --json | jq -r .state
# Expected: "cancelled"

# K3. Missing op-id exits 6 (matches `doxa-research resume`)
uv run doxa-research cancel op_does_not_exist
echo "exit=$?"
# Expected exit: 6

# K4. Provider that doesn't implement upstream cancel
# (Perplexity / Gemini, when configured) — local checkpoint still flips to cancelled,
# stderr notes "upstream cancel not supported, local checkpoint marked cancelled".

# K5. Ctrl-C during a sync background run cancels upstream (P18-T27)
# Default behavior: --cancel-on-interrupt is on per [execution].cancel_upstream_on_interrupt.
uv run doxa-research ask "long topic" --mode deep_research --provider mock
# (Wait a few seconds, then press Ctrl-C.)
# Expected:
#   - "Interrupt received. Saving checkpoint..." line from the signal handler
#   - One "Cancelled upstream: <provider>" line per non-completed provider
#   - Local checkpoint shows status=cancelled (verify via `doxa-research status <op-id>`)
# With a real OpenAI key this also stops the deep-research bill on the OpenAI side.
# K5 also works on `doxa-research resume <op-id>` — same shared polling loop.

# K6. --no-cancel-on-interrupt opts out for one run (and a hint reminds you)
uv run doxa-research ask "long topic" --mode deep_research --provider mock --no-cancel-on-interrupt
# (Wait a few seconds, then press Ctrl-C.)
# Expected:
#   - "Upstream job still running; run `doxa-research cancel <op-id>` to stop billing."
#   - No "Cancelled upstream:" lines (we did not call provider.cancel)
# Equivalent persistent setting:
#   doxa-research config set execution.cancel_upstream_on_interrupt false
# When --json is also set, the hint is suppressed (would corrupt the envelope).
```

### L. `doxa-research modes --kind <immediate|background>` filter

```bash
# L1. Immediate-only listing
uv run doxa-research modes list --kind immediate
# Expected: only modes with kind=immediate (e.g. default, thinking, …).

# L2. Background-only listing
uv run doxa-research modes list --kind background
# Expected: only modes with kind=background (e.g. deep_research, quick_research, …).

# L3. Invalid value rejected
uv run doxa-research modes list --kind sometimes 2>&1 | head -3
echo "exit=$?"
# Expected exit: 2 (BadParameter); message lists the valid choices.

# L4. Tab-completion offers immediate/background
# In a configured shell:  doxa-research modes list --kind <TAB>
# Expected: completes to `immediate` and `background` only.
```

### M. ModeKindMismatchError fast-fail

```bash
# M1. Immediate kind + a model with no synchronous mode -> rejected before any API call.
# Must use a *-deep-research model: gpt-5.6-sol defaults to background research
# but streams, so it can never mismatch. Uses the openai provider because the
# mock provider performs no kind validation.
uv run doxa-research ask "test" --mode openai_quick --model o3-deep-research --provider openai 2>&1 | head -8
echo "exit=$?"
# Expected: ModeKindMismatchError mentioning [modes.openai_quick],
#           the declared kind ("immediate"), the required kind ("background"),
#           and a suggestion to either drop --model or change kind.
# Expected exit: non-zero.

# M2. Force-background on an immediate-only model is allowed
uv run doxa-research ask "test" --mode deep_research --model o3 --provider mock 2>&1 | head -3
# Expected: no ModeKindMismatchError; the run is treated as background.
```

### N. `quick_research` (renamed from `mini_research`) alias deprecation

```bash
# N1. Old name still works, prints a one-time deprecation warning
uv run doxa-research ask "topic" --mode mini_research --provider mock 2>&1 | grep -i "deprecat"
# Expected: a single warning line referencing `mini_research` and pointing at
#           `quick_research`. Subsequent runs in the same process do NOT repeat it.

# N2. Canonical name has no warning
uv run doxa-research ask "topic" --mode quick_research --provider mock 2>&1 | grep -i "deprecat" \
  && echo "FAIL: canonical name should not warn" \
  || echo "ok: no warning on canonical name"
```

### O. User-mode missing-`kind` warn-once

```bash
# O1. Add a user mode without kind, then run
mkdir -p ~/.config/doxa-research
cat >> ~/.config/doxa-research/doxa-research.config.toml <<'TOML'

[modes.test_no_kind]
provider = "mock"
model = "mock-default"
TOML

uv run doxa-research ask "ping" --mode test_no_kind --provider mock 2>&1 | grep -i "kind"
# Expected: ONE warning line referencing `[modes.test_no_kind]` and recommending
#           an explicit `kind` field. The run still proceeds (warn-only in v3.1.0;
#           v4.0.0 will hard-error per the in-source TODO).

# O2. Cleanup — remove the test mode block from your config when done.
```

### P. `doxa-research resume --async`

> Drive-by progress check: one status tick per provider, downloads any
> newly-completed results, exits without polling. Distinct from
> `doxa-research resume --json` (pure read-only snapshot) and from default
> `doxa-research resume` (full polling loop).

```bash
# P1. Drive-by check on a still-running op
OPID=$(uv run doxa-research ask "long topic" --mode deep_research --async --provider mock --json | jq -r .data.operation_id)
uv run doxa-research resume "$OPID" --async
# Expected: prints "No providers completed since last check." and exits 0.
# NO new files written; checkpoint statuses unchanged. Did exactly ONE
# `provider.check_status()` call per non-completed provider.

# P2. Partial-completion download (one provider done, one still running)
# (use --combined openai,perplexity if you have both keys; or simulate with mock)
uv run doxa-research resume "$OPID" --async
# Expected: prints "Saved results from: openai" (or whichever flipped).
# That provider's result file is now on disk; the other is still pending.
# operation.status STAYS "running" (locked decision: aggregate flips only
# when ALL providers report completed).

# P3. Already-completed op is a no-op
uv run doxa-research resume "$COMPLETED_OPID" --async
# Expected: "Operation X already completed." Exits 0. Zero API calls.

# P4. JSON envelope shape
uv run doxa-research resume "$OPID" --async --json | jq
# Expected: {"status": "ok", "data": {operation_id, status, mode, providers,
#   newly_completed: [...], ...}}
# `newly_completed` is the list of provider names that flipped THIS tick.
# Stdout contains ONLY the JSON envelope; no Rich prose.
```

---

## Recent Changes — Targeted Tests

Files changed in the last 14 commits and the P14 feature each one covers. Use the matching feature section above to exercise them.

| Changed file | P14 feature | Test section |
|---|---|---|
| `src/doxa_research/errors.py` | `format_config_context` + APIKeyError enrichment | E |
| `src/doxa_research/cli.py` | `--input-file`/`--auto` help, `--api-key-*` softening, `--pick-model`, `providers` subgroup, `auth` help routing | A, C, D, H |
| `src/doxa_research/help.py` | epilog, workflow chain, `render_auth_help`, deprecation banner | B, D |
| `src/doxa_research/commands.py` | `providers_list`/`_models`/`_check` | A |
| `src/doxa_research/run.py` | spinner gate around poll loop, `model_override` plumbing | F, H |
| `src/doxa_research/progress.py` (new) | `should_show_spinner`, `run_with_spinner` | F |
| `src/doxa_research/interactive_picker.py` (new) | `pick_model`, `immediate_models_for_provider` | H |
| `src/doxa_research/signals.py` | resume-later hint on SIGINT | G |
| `pyproject.toml` / `uv.lock` | `thothspinner` dependency | F |
| `README.md` | Authentication section | D |
| `CHANGELOG.md` / `PROJECTS.md` | release tracking | (docs only) |

---

## Automated Verification

Run before considering manual testing complete:

```bash
just all              # format + lint + typecheck + tests (full gate)
./doxa_test -r       # full doxa_test integration suite (~50s with mock provider)
```

Expected:
- `just all` → all green
- `./doxa_test -r` → 63 passed, 1 skipped, 0 failed (the 1 skipped is pre-existing, not P14)

---

## Known Behaviors / Caveats

- Two resume hints print on Ctrl-C (the older Rich-formatted line + the new "Resume later:"). Functional but redundant; tracked as a follow-up nit.
- `doxa-research providers help` (without subcommand) currently shows the legacy provider help — examples there still mention `--list` / `--models` flags rather than the new subcommands. Will be cleaned up when the deprecation shim is removed in N+1.
- `--pick-model` over a non-TTY input (e.g. CI without `echo N |`) will hang on the prompt. Pipe a number in or skip the flag.

---

## Configuration Profiles (P21)

Hand-edit `~/.config/doxa-research/doxa-research.config.toml` first to add:

```toml
[profiles.fast.general]
default_mode = "thinking"

[general]
default_profile = "fast"
```

Then run:

```bash
doxa config get general.default_mode                      # expect "thinking" (from profile)
DOXA_PROFILE=fast doxa-research config get general.default_mode   # expect "thinking"
doxa --profile missing config get general.default_mode    # expect ConfigProfileError naming '--profile flag'
doxa config get general.default_profile                   # expect "fast" (persisted)
doxa --profile bar config get general.default_profile     # expect "fast" (NOT mutated by --profile)
```

### Shipped profile examples

`doxa-research init` writes these example profiles into your config so you can try them immediately:

```bash
doxa --profile daily "what should I focus on today?"      # default_mode=thinking, default_project=daily-notes
doxa --profile openai_deep "compare vector databases"     # deep_research, providers=["openai"]
doxa --profile all_deep "compare vector databases"        # deep_research, parallel openai+perplexity
doxa --profile deep_research "literature review of X"     # deep_research + a worked prompt_prefix
```

### Verifying `prompt_prefix` is applied

```bash
# With the shipped `deep_research` profile, the prompt that reaches the LLM is
# prepended with: "Be thorough. Cite primary sources. Include counter-arguments."
doxa --profile deep_research --verbose "research vector dbs"
# Look for the assembled prompt in the verbose output.
```

### Configuration Profile CRUD (P21b)

Smoke-check the `doxa-research config profiles ...` CLI introduced in P21b. Run from a
clean working directory (no `./doxa.config.toml`) so the user-tier config is
the active write target:

```bash
doxa config profiles add fast
doxa config profiles set fast general.default_mode thinking
doxa config profiles set-default fast
doxa config get general.default_mode
DOXA_PROFILE=fast doxa-research config get general.default_mode
doxa config profiles current
doxa --profile fast config profiles current
doxa --profile missing config get general.default_mode
doxa config profiles set-default ghost      # expect ConfigProfileError
doxa --profile foo config profiles add bar  # expect 'no such option' error
doxa config profiles add interactive
doxa config profiles set interactive general.default_mode interactive
doxa config profiles show interactive
```
