# Doxa Research

[![PyPI version](https://img.shields.io/pypi/v/doxa-research.svg)](https://pypi.org/project/doxa-research/)
[![Python versions](https://img.shields.io/pypi/pyversions/doxa-research)](https://pypi.org/project/doxa-research/)
[![Downloads](https://img.shields.io/pypi/dm/doxa-research)](https://pypi.org/project/doxa-research/)
[![CI](https://github.com/smorinlabs/doxa-research/actions/workflows/ci.yml/badge.svg)](https://github.com/smorinlabs/doxa-research/actions/workflows/ci.yml)
[![Last commit](https://img.shields.io/github/last-commit/smorinlabs/doxa-research)](https://github.com/smorinlabs/doxa-research/commits/main)
[![GitHub stars](https://img.shields.io/github/stars/smorinlabs/doxa-research?style=flat&logo=github)](https://github.com/smorinlabs/doxa-research/stargazers)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-FE5196?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org)

> **Deep Research across OpenAI, Perplexity, and Gemini — in parallel, from one command.**

Doxa Research is a CLI for AI-powered deep research automation. It fans a single prompt out to multiple LLM Deep-Research providers — OpenAI, Perplexity, and Gemini — concurrently and merges the results into one markdown report with citations. One command, multiple perspectives, comprehensive coverage.

```bash
uvx doxa-research init                                  # one-time setup
export OPENAI_API_KEY="sk-..."                          # any subset of providers works
doxa ask "What are the latest advances in distributed consensus?"     # streams to terminal

# Or fan out across all three providers in parallel (Deep Research, 5-15 min each):
doxa ask --mode all_deep_research "What are the latest advances in distributed consensus?"
```

Immediate modes stream to your terminal. Background Deep Research modes (`all_deep_research`, `deep_research`, the `gemini_*_research` family) write each provider's report to `./research-outputs/`. See [docs/COMMANDS.md](docs/COMMANDS.md) for the full mode catalog.

![Doxa Research fanning out to OpenAI, Perplexity, and Gemini in parallel](docs/assets/hero.svg)

> ⭐ **Like what you see?** [Star the repo](https://github.com/smorinlabs/doxa-research) — it helps others find Doxa and signals which Deep Research integrations to prioritize next.

<details>
<summary><strong>Table of contents</strong></summary>

**Get started**
- [Features](#features)
- [How it works](#how-it-works)
- [30-second quickstart](#30-second-quickstart)
- [What a Doxa report looks like](#what-a-doxa-report-looks-like)
- [Why Doxa?](#why-doxa)
- [How much does it cost?](#how-much-does-it-cost)
- [Example use cases](#example-use-cases)

**Configuration**
- [Authentication](#authentication)
- [Usage](#usage)
- [Configuration](#configuration)
- [Provider Configuration](#provider-configuration)

**Reference**
- [Error Handling](#error-handling)
- [Output Structure](#output-structure)
- [Environment Variables](#environment-variables)
- [Exit Codes](#exit-codes)
- [Commands Reference](#commands-reference)
- [Shell completion](#shell-completion)
- [JSON output](#json-output)

**Project**
- [Development](#development)
- [Version History](#version-history)
- [Roadmap](#roadmap)
- [Discussion & support](#discussion--support)
- [About the name](#about-the-name)
- [License](#license)

</details>

## Features

- 🔀 **Parallel multi-provider** — OpenAI, Perplexity, and Gemini run concurrently. One report, multiple perspectives.
- 🎯 **Mode chaining** — clarification → exploration → deep research. Each step feeds the next.
- 💬 **Interactive prompt mode** — slash commands, tab completion, and multiline input via an enhanced terminal UI.
- ↩️ **Resumable** — checkpoint/resume after Ctrl-C; reconnect to long-running background jobs across process restarts.
- 🚀 **Zero setup** — `uvx doxa-research` runs straight from PyPI. UV-native, no virtualenv juggling.

## How it works

```
                   ┌────────────┐         research-outputs/
   prompt  ──────▶ │    doxa    │ ──────▶  <ts>_all_deep_research_openai_<slug>.md
                   └────────────┘          <ts>_all_deep_research_perplexity_<slug>.md
                          │                <ts>_all_deep_research_gemini_<slug>.md
                          │  fans out      <ts>_all_deep_research_combined_<slug>.md (--combined)
                          │  concurrently
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
         OpenAI       Perplexity      Gemini
       (o3-deep-     (sonar-deep-   (deep-research-
        research)     research)      preview-04-2026)
```

Each enabled provider runs in parallel. Doxa polls each one until completion (or timeout / cancel), then merges the results into a single markdown report with per-provider sections and citation blocks. Long-running background jobs (Deep Research) are checkpointed: you can `Ctrl-C` and resume later, or fire-and-forget with `--async` and pick the result up from a different terminal session via `doxa resume <op-id>`.

## 30-second quickstart

```bash
# 1. Install (one-time)
uvx doxa-research init

# 2. Set keys for any provider(s) you want — Doxa skips providers without keys
export OPENAI_API_KEY="sk-..."
export PERPLEXITY_API_KEY="pplx-..."
export GEMINI_API_KEY="..."        # paid Tier 1+ required for Gemini Deep Research

# 3. Ask — immediate single-provider, streams to terminal:
doxa ask "Compare Paxos, Raft, and Viewstamped Replication."

# Or run Deep Research across all three providers in parallel:
doxa ask --mode all_deep_research --combined "Compare Paxos, Raft, and Viewstamped Replication."
# → ./research-outputs/<ts>_all_deep_research_openai_<slug>.md
# → ./research-outputs/<ts>_all_deep_research_perplexity_<slug>.md
# → ./research-outputs/<ts>_all_deep_research_gemini_<slug>.md
# → ./research-outputs/<ts>_all_deep_research_combined_<slug>.md
```

Prefer `pip install doxa-research` or `uv tool install doxa-research` if you want a permanent install instead of running via `uvx`.

For per-provider config and resumable / cancellable workflows see [Usage](#usage). For a full migration from the previous `thoth` releases see [MIGRATION.md](MIGRATION.md).

## Agent skill

Doxa ships an installable skill for executing approved prompt files, monitoring
background jobs, and preserving provider reports and provenance.

| Skill | Purpose |
| --- | --- |
| [`doxa-research`](docs/skills/doxa-research.md) | Run single- or multi-provider Deep Research through the Doxa CLI |

Claude Code:

```text
/plugin marketplace add smorinlabs/doxa-research
/plugin install doxa-research@doxa-research
```

Codex:

```bash
codex plugin marketplace add smorinlabs/doxa-research --ref main
codex plugin add doxa-research@doxa-research
```

See [the skill guide](docs/skills/doxa-research.md) for Doxa setup, direct-copy,
and development installation modes.

## What a Doxa report looks like

A combined multi-provider report (excerpt). The frontmatter shape and
section wrappers are what Doxa emits; the body underneath each
`## <Provider> Results` heading is the model's own output, including
its inline citations and sources.

```markdown
---
prompt: Compare Paxos, Raft, and Viewstamped Replication.
mode: default
provider: combined
model: Multiple
operation_id: research-20260517-103412-a38d159848984fa8
created_at: 2026-05-17T10:34:12.481593
---

### Prompt

```
Compare Paxos, Raft, and Viewstamped Replication.
```

# Combined Research Report: Compare Paxos, Raft, and Viewstamped Replication.

Generated: 2026-05-17T10:42:08.117244

## Openai Results

Paxos, Raft, and Viewstamped Replication (VR) are three foundational consensus
protocols that achieve agreement among distributed nodes despite failures.
[…3–8 pages of analysis from gpt-5.6-sol, with inline citation anchors…]

### Sources
- [Paxos Made Simple — Lamport (lamport.azurewebsites.net)](https://...)
- [In Search of an Understandable Consensus Algorithm (usenix.org)](https://...)
- [Viewstamped Replication Revisited (pmg.csail.mit.edu)](https://...)

---

## Perplexity Results

[parallel synthesis from sonar-deep-research with its own ### Sources block]

---

## Gemini Results

[parallel synthesis from deep-research-preview-04-2026 with its own ### Sources block]
```

Each provider contributes a self-contained section with its own citations. `--combined` is a boolean flag (no value): pass it to synthesize the unified report shown above, or omit it to get per-provider files only.

## Why Doxa?

If you only need a single Deep Research response, the vendor tools (ChatGPT Deep Research, Perplexity Pro, Gemini Deep Research) work fine. Doxa is for when you want **multiple perspectives in one report** — different models reason differently, cite different sources, and miss different things.

| Feature | ChatGPT DR | Perplexity Pro | Gemini DR | Manual orchestration | **Doxa Research** |
|---|---|---|---|---|---|
| Multi-provider in parallel | ❌ | ❌ | ❌ | ✅ (DIY) | ✅ |
| One merged markdown report | ❌ | ❌ | ❌ | ❌ | ✅ |
| Mode chaining (clarify → explore → research) | ❌ | ❌ | ❌ | ❌ | ✅ |
| Resumable after Ctrl-C / process restart | ❌ | ❌ | ❌ | ❌ | ✅ |
| CLI / scriptable / pipe-friendly | ❌ | ❌ | ❌ | ✅ (DIY) | ✅ |
| Local config, no vendor lock-in | ❌ | ❌ | ❌ | ✅ (DIY) | ✅ |
| Per-provider model selection per mode | ❌ | ❌ | ❌ | ✅ (DIY) | ✅ |
| Cost transparency (you pay APIs directly) | n/a — subscription | n/a — subscription | n/a — subscription | ✅ | ✅ |

The "Manual orchestration" column is what you'd build yourself by wiring the three Deep Research APIs together and merging the results. Doxa is that, packaged.

## How much does it cost?

You pay each provider's API directly — Doxa does not add cost or take a cut. Rough per-run cost (subject to provider pricing changes; see each provider's pricing page for current rates):

> **OpenAI cost changed with the 2026-07-23 deep-research shutdown.** There is
> no longer a cheaper OpenAI research tier: `o4-mini-deep-research` ($2/$8 per
> 1M input/output tokens) and `o3-deep-research` ($10/$40) were both replaced
> by `gpt-5.6-sol` ($4/$20). Former `quick_research` users therefore pay more
> per token, and former `deep_research` users less. Bound cost by setting
> `max_tool_calls` and `reasoning_effort` explicitly; the figures below are not
> measured against the new model.

| Provider | Model | Typical cost per Deep Research run |
|---|---|---|
| OpenAI | `gpt-5.6-sol` | $1–$8 (varies with depth and prompt complexity) |
| Perplexity | `sonar-deep-research` | $0.05–$0.50 |
| Gemini | `deep-research-preview-04-2026` | $1–$3 (preview pricing; paid Tier 1+ required) |
| Gemini | `deep-research-max-preview-04-2026` | $3–$7 (max comprehensiveness) |

A single `doxa ask` run with all three providers active typically costs **$2–$15** depending on the prompt and which models are enabled. Provider keys come from environment variables or `~/.config/doxa/doxa.config.toml`; providers without keys are skipped, so you only pay for what runs.

Immediate (non-Deep-Research) modes — `gemini_quick`, `gemini_pro`, plain chat completions — are much cheaper, typically **$0.001–$0.10 per run**.

## Example use cases

A handful of concrete prompts to get a feel for the tool. All assume the relevant API keys are set and `doxa init` has been run.

**Compare technical approaches across providers**

```bash
doxa ask "Compare WebGPU vs WebGL vs WebAssembly+SIMD for browser-based ML inference. \
  Focus on latency, browser support, and current production deployments."
```

Each provider's analysis becomes its own section in the report. Useful when no single provider's training cutoff is recent enough on its own.

**Survey recent papers**

```bash
doxa ask --mode deep_research "Survey the most-cited distributed-systems papers \
  published in 2025. Include venue, citation count, and a one-paragraph summary of each."
```

Sources blocks land per-provider, so you can see which model is grounding on which papers.

**Plan an implementation (PRD-style output)**

```bash
doxa ask --mode prd "Plan a multi-tenant SaaS architecture using Postgres \
  row-level security. Include schema, RLS policies, and a migration plan."
```

The `prd` / `gemini_prd` modes are tuned for design-document output rather than reference research.

**Quick single-provider research**

```bash
doxa ask --provider openai "What's the current best practice for OAuth refresh-token rotation?"
```

When you only need one perspective, scope to a single provider to cut cost and latency.

**Resume a long-running Deep Research job from another terminal**

```bash
# Terminal 1: kick off and exit
doxa ask --async --mode gemini_deep_research "Comprehensive review of consensus algorithms 1980-2025."
# → operation_id: research-20260518-093412-...

# Terminal 2 (any time later, even after process restart):
doxa resume research-20260518-093412-...
```

`doxa resume <op-id>` re-attaches to the in-flight provider jobs via the checkpoint stored under `~/.local/state/doxa/checkpoints/`.

## Authentication

Authentication — recommended order:

1. **Environment variables (recommended)**:
   ```bash
   export OPENAI_API_KEY=sk-...
   export PERPLEXITY_API_KEY=pplx-...
   ```

2. **Config file** (persistent, per-machine): `~/.config/doxa/doxa.config.toml`
   ```toml
   [providers.openai]
   api_key = "sk-..."
   ```

3. **CLI flags** (last resort — exposes keys in shell history; not recommended):
   ```bash
   doxa-research --api-key-openai sk-... deep_research "..."
   ```

For related command help, run `doxa config --help`.

> **🔐 Security note.** Never commit `.env`, `openai.env`, or your `~/.config/doxa/doxa.config.toml` to a public repository — these files contain provider API keys that grant paid access. The project's `.gitignore` already excludes `openai.env` at the repo root; if you store keys elsewhere, double-check they're ignored. Prefer environment variables in shell-rc files (sourced at session start, not committed) or `~/.config/doxa/` (in your home dir, not in any repo) over CLI flags (which leak into shell history).

## Usage

### Basic Research
```bash
# Quick research (uses default mode)
doxa "your research prompt"

# Run research with a specific mode
doxa deep_research "your research prompt"
doxa clarification "ambiguous topic needing clarity"
doxa exploration "broad topic to explore"
doxa thinking "quick analysis task"
doxa openai_reasoning "grounded OpenAI reasoning task"

# Use specific provider
doxa "explain quantum computing" --provider openai
doxa deep_research "AI safety" --provider openai --timeout 120
```

### Project-Based Research
```bash
# Save outputs to a project directory
doxa deep_research "quantum algorithms" --project quantum_research
```

### Mode Chaining
```bash
# Start with clarification
doxa clarification "quantum computing security"

# Then explore with auto-input from previous mode
doxa exploration --auto

# Finally, deep research with all context
doxa deep_research --auto
```

### Provider-Specific API Keys
```bash
# Use specific API key for a provider
doxa "prompt" --api-key-openai "sk-..." --provider openai

# Multiple provider keys for multi-provider modes
doxa deep_research "prompt" --api-key-openai "sk-..." --api-key-perplexity "pplx-..."

# Testing with mock provider
doxa "test prompt" --api-key-mock "test-key" --provider mock
```

### Output Control
```bash
# Generate combined report from multiple providers
doxa "prompt" --combined

# Disable metadata headers and prompt section
doxa "prompt" --no-metadata

# Quiet mode for minimal output
doxa "prompt" --quiet
```

### Streaming output for immediate modes (v3.1.0+)

Immediate-kind modes (`default`, `thinking`, `clarification`, `openai_reasoning`,
`perplexity_quick`, `perplexity_pro`, `perplexity_reasoning`, `gemini_quick`,
`gemini_pro`, `gemini_reasoning`) stream tokens
to stdout as they arrive — no progress bar, no operation-ID echo, no
resume hint, and no default result file. Use `--out` to redirect or tee:

```bash
# Stream to stdout (default)
doxa ask "what is X" --mode thinking

# Write to a file (truncate)
doxa ask "what is X" --mode thinking --out answer.md
doxa --out answer.md --provider mock "what is X"

# Tee to stdout AND a file
doxa ask "what is X" --mode thinking --out -,answer.md

# Append instead of truncating
doxa ask "what is X" --mode thinking --out answer.md --append
```

`openai_reasoning` is the built-in OpenAI immediate mode for grounded answers:
it sends `[modes.openai_reasoning.openai].reasoning_summary = "auto"` and
`web_search = true`. Custom OpenAI immediate modes can opt in or out with the
same namespace:

```toml
[modes.my_openai_reasoning]
provider = "openai"
model = "o3"
kind = "immediate"

[modes.my_openai_reasoning.openai]
reasoning_summary = "auto"
web_search = false  # set true to enable web_search
```

Background-kind modes (e.g. `deep_research`, `quick_research`,
`exploration`, and the Gemini Deep Research modes: `gemini_quick_research`,
`gemini_exploration`, `gemini_deep_dive`, `gemini_tutorial`,
`gemini_solution`, `gemini_prd`, `gemini_tdd`, `gemini_deep_research`,
`gemini_comparison`) continue to use `--project` / `--output-dir` for
persistent output. `--out` is currently immediate-only.

### Cancelling a running operation

```bash
# Cancel an in-flight background operation by ID
doxa cancel a1b2c3d4-...

# Returns exit 6 if the operation isn't found; 0 otherwise.
# JSON envelope available:
doxa cancel a1b2c3d4-... --json
```

`doxa-research cancel` calls the provider's upstream cancel endpoint where
supported (OpenAI Responses API), then marks the local checkpoint as
cancelled. Providers without upstream cancel (e.g., Perplexity at the
time of writing) have the local checkpoint marked cancelled but the
upstream job runs to completion.

### Filtering modes by execution kind

Each mode is declared as `kind = "immediate"` (synchronous, streaming)
or `kind = "background"` (async, polling-loop). User-defined modes in
`~/.config/doxa/doxa.config.toml` should declare `kind` explicitly; missing
`kind` warns once and falls back to a substring heuristic on the model
name.

```bash
# Show only immediate-kind modes
doxa modes --kind immediate

# Show only background-kind modes
doxa modes --kind background
```

A misconfigured mode (e.g., declared `immediate` but using a
deep-research model) raises `ModeKindMismatchError` at submit time
with a config-edit suggestion — before any HTTP call hits the
provider.

### Interactive Mode
```bash
# Enter interactive prompt mode with enhanced UI
doxa -i
# or
doxa --interactive

# Interactive mode with specific provider
doxa -i --provider openai --api-key-openai "sk-..."

# Start with pre-configured settings and initial prompt
doxa -i --mode deep_research --provider openai "initial prompt text"

# Pipe prompt into interactive mode
echo "prompt from stdin" | doxa-research -i --prompt-file -

# Combined settings - all CLI arguments initialize the session
doxa -i --mode exploration --provider perplexity --async "test prompt"
```

Interactive mode features:
- **Command-line initialization**: All CLI arguments (mode, provider, prompt, async) initialize the session
- **Pre-populated prompt**: Initial prompt text appears in the input area, ready to edit or submit
- **Bordered text box**: Input appears in a blue-bordered frame with clear visual separation
- **Multiline input**: Enter to submit, multiple options for new lines:
  - **Shift+Return** - Works in modern terminals with CSI-u support (iTerm2, Warp, Windows Terminal, VSCode)
  - **Ctrl+J** - Universal option that works in all terminals (recommended)
  - **Option+Return** (Mac) or **Alt+Enter** (Linux/Windows) - Traditional fallback
- **Slash commands**:
  - `/help` - Show available commands
  - `/keybindings` - Show keyboard shortcuts (full-screen prompt UI)
  - `/mode [<name>]` - Change research mode or list available modes
  - `/provider [<name>]` - Set provider or list available providers
  - `/async` - Toggle async mode on/off
  - `/multiline` - Toggle multiline input mode (basic fallback prompt)
  - `/status` - Check last operation status
  - `/exit` or `/quit` - Exit interactive mode
- **Tab completion**: Start typing a slash command and press Tab for auto-completion
- **Unix shortcuts**: Ctrl+A (start), Ctrl+E (end), Ctrl+K (kill to end), Ctrl+U (kill to start)
- **Status bar**: Shows current mode and provider settings in help text
- **Override capability**: All CLI settings can be overridden using slash commands
- **Fallback mode**: Automatically switches to basic input when not in a terminal (e.g., piped input)

### Async Operations
```bash
# Submit research and exit immediately
doxa deep_research "long research topic" --async
# Output: Operation ID: research-20240803-143022-a1b2c3d4e5f6g7h8

# Check status later
doxa status research-20240803-143022-a1b2c3d4e5f6g7h8

# Resume operation
doxa resume research-20240803-143022-a1b2c3d4e5f6g7h8
```

### Provider Management
```bash
# List available providers and their status
doxa providers list

# Show API key configuration
doxa providers check

# List available models from all providers
doxa providers models

# List models from specific provider
doxa providers models --provider openai
doxa providers models -P perplexity
```

### List Operations
```bash
# List active operations
doxa list

# List all operations
doxa list --all
```

## Configuration

Configuration file is stored at `~/.config/doxa/doxa.config.toml`. Key settings:

- `default_project`: Default project name for outputs
- `default_mode`: Default research mode
- `base_output_dir`: Base directory for project outputs
- `poll_interval`: Seconds between status checks (default: 30)
- `max_wait`: Maximum wait time in minutes (default: 30)
- `parallel_providers`: Enable parallel provider execution
- `combine_reports`: Generate combined reports from multiple providers
- `execution.prompt_max_bytes`: Max bytes accepted from `--prompt-file` (file path or stdin). Files exceeding this are rejected before reading. Default: `1048576` (1 MiB).

### Configuration Profiles

Profiles let you keep shared config at the top level and define named overlays for different work contexts.

```toml
[general]
default_mode = "deep_research"

[profiles.fast.general]
default_mode = "thinking"
```

Selection precedence is `--profile` → `DOXA_PROFILE` → `general.default_profile` → no profile.

`doxa-research config get general.default_profile` reflects the **persisted pointer** in the file. `--profile` and `DOXA_PROFILE` are read-only runtime inputs — they never write back to `general.default_profile`. With persisted `general.default_profile = "fast"`, running `doxa-research --profile bar config get general.default_profile` returns `"fast"`; the runtime active selection is `bar`.

Profile CLI management is available through `doxa-research config profiles ...`.
Manual editing of `~/.config/doxa/doxa.config.toml` (or project-scoped
`./doxa.config.toml` / `./.doxa.config.toml`) still works when you need to
make larger structural changes.

#### Managing profiles from the CLI

The same profile from the hand-edit example above can be created end-to-end with:

```bash
doxa config profiles add fast
doxa config profiles set fast general.default_mode thinking
doxa config profiles set-default fast    # persists general.default_profile = "fast"
doxa config profiles current             # shows fast (from general.default_profile)
doxa config profiles list                # lists all profiles, marks active
doxa config profiles list --show-shadowed  # also shows user profiles shadowed by project profiles
doxa config profiles show fast --json    # full profile contents
doxa config profiles unset fast general.default_mode  # remove a single key
doxa config profiles remove fast         # delete the entire profile
doxa config profiles unset-default       # clear the persisted pointer
```

`--profile` is honored only by `list` and `current`. `show NAME` and mutator commands reject `--profile` because the profile they inspect or operate on is the positional argument.

### Migrating from `thoth`

The `thoth` → `doxa-research` rename in v3.0.0 changed config paths,
env var prefixes, and the test-runner script. See
[MIGRATION.md](MIGRATION.md) for the full migration steps.

If both `./doxa.config.toml` and `./.doxa.config.toml` exist in the same
project, config-loading commands refuse to start until one is deleted.

#### Change the default mode for a profile

```toml
[profiles.daily.general]
default_mode = "thinking"
default_project = "daily-notes"
```

```bash
doxa --profile daily "summarize today's notes"
```

#### Run all available deep-research providers

```toml
[profiles.all_deep.general]
default_mode = "deep_research"

[profiles.all_deep.modes.deep_research]
providers = ["openai", "perplexity"]
parallel = true
```

```bash
doxa --profile all_deep "compare vector databases"
```

> **Gemini support.** The `gemini` provider supports immediate grounded modes such as `gemini_quick`, `gemini_pro`, and `gemini_reasoning`, plus nine background deep-research modes (`gemini_quick_research`, `gemini_exploration`, `gemini_deep_dive`, `gemini_tutorial`, `gemini_solution`, `gemini_prd`, `gemini_tdd`, `gemini_deep_research`, `gemini_comparison`).

#### Use one deep-research provider

```toml
[profiles.openai_deep.general]
default_mode = "deep_research"

[profiles.openai_deep.modes.deep_research]
providers = ["openai"]
parallel = false
```

```bash
doxa --profile openai_deep "research model routing"
```

#### Use an immediate default mode

```toml
[profiles.quick.general]
default_mode = "thinking"
```

```bash
doxa --profile quick "give me the short version"
```

#### Reserve an interactive default profile

```toml
[profiles.interactive.general]
default_mode = "interactive"
```

This profile can be stored, listed, and selected today via the configuration-profile commands (or hand-edit). Command behavior for choosing a default interactive mode ships in a later release.

#### Prepending a prompt prefix

A `prompt_prefix` value is prepended (with a blank line) to the user's prompt before it reaches the LLM. The mode's `system_prompt` is unaffected. Resolution walks a 4-level hierarchy from most-specific to least:

1. `[profiles.<active>.modes.<MODE>] prompt_prefix`
2. `[profiles.<active>] prompt_prefix`
3. `[modes.<MODE>] prompt_prefix`
4. `[general] prompt_prefix`

More-specific values **replace** less-specific ones (no concatenation). An empty string is treated as unset, so an inner-empty value falls through to the outer level.

```toml
[general]
prompt_prefix = "Be precise."

[modes.deep_research]
prompt_prefix = "Cite primary sources."

[profiles.deep.general]
default_mode = "deep_research"
prompt_prefix = "Be thorough. Cite primary sources where possible."

[profiles.deep.modes.deep_research]
prompt_prefix = "Be thorough. Cite primary sources. Include counter-arguments."
```

Resolution outcomes:

| Active profile | Mode | Resolved prefix |
|---|---|---|
| (none) | `default` | `Be precise.` (general) |
| (none) | `deep_research` | `Cite primary sources.` (modes.deep_research) |
| `deep` | `default` | `Be thorough. Cite primary sources where possible.` (profiles.deep) |
| `deep` | `deep_research` | `Be thorough. Cite primary sources. Include counter-arguments.` (profiles.deep.modes.deep_research) |

`doxa-research init` ships these profiles pre-populated in your config (`~/.config/doxa/doxa.config.toml`): `daily`, `quick`, `openai_deep`, `all_deep`, `interactive`, and `deep_research` — the last one demonstrates the `prompt_prefix` hierarchy end-to-end. Edit or delete them as you like.

## Provider configuration

Each provider (OpenAI, Perplexity, Gemini) has its own auth, models, and
tunable knobs. The 30-second setup is in the
[Authentication](#authentication) section above. For the full reference —
config-file keys, model lists, Gemini cost tiers, the 9 `gemini_*_research`
modes — see **[docs/providers.md](docs/providers.md)**.

Quick check that your provider is wired up correctly:

```bash
doxa providers list           # show which providers are configured
doxa providers models -P openai   # list models live from the API
```

## Error Handling

### Authentication Errors
- **Invalid API Key**: Verify key at https://platform.openai.com/account/api-keys
- **Missing API Key**: Set via environment variable or config file

### Rate Limiting
- Automatic retry with exponential backoff (up to 3 attempts)
- Check usage at https://platform.openai.com/usage

### Timeout Errors
- Increase timeout: `--timeout 120`
- Check network connection
- Try simpler prompt first

### Network Errors
- Automatic connection retry
- Check API status at https://status.openai.com/

### Multi-Provider Failures
When running with multiple providers, a single provider failure does not abort the operation:
- The failed provider is logged with a warning (`⚠ Provider failed: <reason>`)
- Remaining providers continue polling normally
- Partial results from successful providers are saved to disk
- Only when **all** providers fail does the operation transition to a failed state

## Output Structure

### Ad-hoc Mode (default)
```
./2024-08-03_143022_default_openai_quantum-computing.md
./2024-08-03_143022_default_perplexity_quantum-computing.md
./2024-08-03_143022_default_combined_quantum-computing.md  # With --combined flag
```

### Output File Format

Each output file includes (unless `--no-metadata` is used):

```yaml
---
prompt: What is Python?
mode: default
provider: openai
model: o3
operation_id: research-20250802-154755-a38d159848984fa8
created_at: 2025-08-02T15:47:55.468596
---

### Prompt

```
What is Python?
```

[Research content follows...]
```

For modes with system prompts:
```yaml
---
prompt: explain kubernetes
mode: deep_research
provider: openai
model: gpt-5.6-sol
operation_id: research-20250802-154755-a38d159848984fa8
created_at: 2025-08-02T15:47:55.468596
---

### Prompt

```
System: Conduct comprehensive research with citations and multiple perspectives.
Organize findings clearly and highlight key insights.

User: explain kubernetes
```

[Research content follows...]
```

### Project Mode
```
./research-outputs/quantum_research/
  ├── 2024-08-03_143022_clarification_openai_quantum-security.md
  ├── 2024-08-03_150122_exploration_openai_quantum-security.md
  └── 2024-08-03_153022_deep_research_combined_quantum-security.md
```

## Development

Setup, the full `just` task list, the `doxa_test` matrix, and the
live-API marker workflow are all in **[CONTRIBUTING.md](CONTRIBUTING.md)**.
Quickstart for contributors:

```bash
git clone https://github.com/smorinlabs/doxa-research.git
cd doxa-research
make env-check && lefthook install && uv sync
just check && ./doxa_test -r
```

## Environment Variables

**Provider API keys** (read by both `doxa ask` and the test suite):
- `OPENAI_API_KEY` — OpenAI API key
- `PERPLEXITY_API_KEY` — Perplexity API key
- `GEMINI_API_KEY` — Gemini API key
- `MOCK_API_KEY` — Mock provider key (test harness auto-generates one if unset)

**Runtime / debug:**
- `DOXA_DEBUG=1` — Enable debug output
- `DOXA_PROFILE=<name>` — Select an active profile for this process

**Config-key overrides** (each maps to a `doxa.config.toml` key; env wins
over the file but loses to a `--flag` of the same name):
- `DOXA_DEFAULT_MODE` — overrides `general.default_mode`
- `DOXA_DEFAULT_PROJECT` — overrides `general.default_project`
- `DOXA_OUTPUT_DIR` — overrides `paths.base_output_dir`
- `DOXA_POLL_INTERVAL` — overrides `execution.poll_interval`
- `DOXA_MAX_WAIT` — overrides `execution.max_wait`

### API Key Precedence

API keys are resolved in the following order (highest to lowest priority):
1. Command-line arguments (`--api-key-openai`, `--api-key-perplexity`, `--api-key-gemini`, `--api-key-mock`)
2. Environment variables (`OPENAI_API_KEY`, `PERPLEXITY_API_KEY`, `GEMINI_API_KEY`, `MOCK_API_KEY`)
3. Configuration file (`~/.config/doxa/doxa.config.toml`)

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Validation error, config error, mode/profile lookup failure, or user abort |
| 2 | Missing API key, or Click usage error (bad arguments) |
| 3 | Upstream provider failure |
| 6 | Operation not found (for `resume` / `cancel` / `status`) |
| 8 | Disk space error |
| 9 | API quota exceeded |
| 10 | API rate limit exceeded |
| 127 | Unexpected error |

## Commands Reference

See [`docs/COMMANDS.md`](docs/COMMANDS.md) for the full CLI surface, or run `doxa <command> --help` for authoritative per-command help.

Most-used commands:

| Command | What it does |
|---|---|
| `doxa ask "<prompt>"` | Run research across all configured providers |
| `doxa resume <op-id>` | Re-attach to a checkpointed operation |
| `doxa cancel <op-id>` | Cancel an in-flight background operation |
| `doxa status <op-id>` | Show operation details |
| `doxa providers list` | Show available providers and key status |
| `doxa modes list` | List research modes |
| `doxa init` | Setup wizard for keys and config |

## Version History

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

## Shell completion

Generate an `eval`-able script:

```bash
eval "$(doxa-research completion bash)"   # or: zsh, fish
```

Persistent install (writes a fenced block to your shell's rc file):

```bash
doxa completion bash --install         # interactive: detect + prompt before overwrite
doxa completion bash --install --force # CI-friendly: write/overwrite silently
doxa completion bash --install --manual # print block + instructions; never write
```

After install, `doxa-research resume <TAB>`, `doxa-research status <TAB>`, `doxa-research config get <TAB>`,
`doxa-research modes list --name <TAB>`, and `doxa-research providers list --provider <TAB>` complete
with live data.

## JSON output

Every data/action admin command supports `--json`:

```bash
doxa status OP_ID --json | jq '.data.status'
doxa cancel OP_ID --json | jq '.data.status'
doxa providers list --json | jq '.data.providers[].name'
doxa list --json | jq '.data.operations[]'
```

See `docs/json-output.md` for the envelope contract and per-command schemas.

## Roadmap

In rough order of priority:

- **More providers** — Anthropic Claude, Cohere, Mistral. The `ResearchProvider` contract is structured so adding a new provider is a contained ~600-line addition (see `src/doxa_research/providers/CLAUDE.md` for conventions).
- **Per-provider polling tunables** — wire `[providers.<name>].poll_interval` and `.max_wait_minutes` into the runtime polling loop so DR-heavy workloads can override the global `[execution]` defaults.
- **Interactive prompt refiner** — guided clarification before sending a Deep Research request, so the prompt-engineering step doesn't fall on the user.
- **MCP server interface** — expose Doxa via Model Context Protocol so IDEs and agents (Claude Code, Cursor) can call `doxa ask` as a tool.
- **VCR cassette replay** — record provider interactions for fully-offline testing and CI without API spend.
- **Architecture review & cleanup** — cross-provider refactor of `openai.py`, `perplexity.py`, `gemini.py` once all three immediate paths and all three background paths are in place.

See `PROJECTS.md` for the granular task trunk and `archive/` for completed work.

## Discussion & support

- 🐛 **Bug reports** — [GitHub Issues](https://github.com/smorinlabs/doxa-research/issues)
- 💡 **Feature requests & general discussion** — [GitHub Discussions](https://github.com/smorinlabs/doxa-research/discussions)
- 📋 **Changelog** — [`CHANGELOG.md`](CHANGELOG.md)
- 🛠️ **Contributing** — see the [Development](#development) section, or [`CONTRIBUTING.md`](CONTRIBUTING.md) if present
- 🔄 **Migrating from `thoth`** — [`MIGRATION.md`](MIGRATION.md)

## About the name

*Doxa* (Greek: δόξα) means "opinion", "belief", or "received wisdom" in ancient Greek philosophy. The name reflects what the tool does: it gathers multiple AI perspectives — OpenAI, Perplexity, Gemini — on a single question and merges them into one report, surfacing consensus and divergence across views in the spirit of dialectical inquiry.

> Previously published as `thoth` (versions ≤ 2.5.0 on PyPI). See [MIGRATION.md](MIGRATION.md) for the rename details and migration guide.

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).

In short: you may use, modify, and redistribute doxa-research, but if you offer a
modified version as a network service to others (a SaaS), you must also
make your full modified source code available to those users. See the
[GNU AGPL FAQ](https://www.gnu.org/licenses/why-affero-gpl.html) for the
rationale behind the network-service clause.

Copyright © 2025-2026 Steve Morin. Contributions are accepted under the
same AGPL-3.0-or-later terms.
