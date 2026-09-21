# Commands reference

Full CLI surface for `doxa` / `doxa-research`. Run `doxa <command> --help`
for the authoritative per-command help — this document is a cross-reference,
not a substitute.

## Main commands

| Command | Description | Example |
|---------|-------------|---------|
| (default) | Run research with prompt | `doxa "your research prompt"` |
| `ask` | Run research with an explicit subcommand | `doxa ask "your research prompt"` |
| `resume` | Resume a checkpointed operation | `doxa resume research-20260518-093412-xxx` |
| `cancel` | Cancel an in-flight background operation | `doxa cancel research-20260518-093412-xxx` |
| `init` | Setup wizard for API keys and config | `doxa init` |
| `status` | Show operation details | `doxa status research-20260518-093412-xxx` |
| `list` | Show recent operations | `doxa list` |
| `config` | Inspect and edit configuration | `doxa config get general.default_mode` |
| `modes` | List or mutate research modes | `doxa modes list` |
| `providers` | Manage providers and models | `doxa providers list` |
| `completion` | Generate shell completion scripts | `doxa completion zsh` |
| `help` | Show help information | `doxa help [COMMAND]` |

## `providers` subcommands

| Subcommand | Description | Example |
|------------|-------------|---------|
| `list` | Show available providers and key status | `doxa providers list` |
| `models` | List models from providers (use `--refresh-cache` to force live fetch) | `doxa providers models -P openai` |
| `check` | Show API key configuration sources | `doxa providers check` |

All three accept `--provider`, `-P <name>` to filter to a single provider.

## `config` subcommands

| Subcommand | Description | Example |
|------------|-------------|---------|
| `get KEY` | Read a configuration value (use `--layer` to pin to defaults/user/project/profile/env/cli) | `doxa config get providers.openai.model` |
| `set KEY VALUE` | Persist a value (defaults to the user-tier config file) | `doxa config set general.default_mode deep_research` |
| `unset KEY` | Remove a key from the target tier | `doxa config unset providers.openai.timeout` |
| `list` | Dump the merged configuration; supports `--json` | `doxa config list --json` |
| `path` | Show the resolved config file path | `doxa config path` |
| `edit` | Open the active config file in `$EDITOR` | `doxa config edit` |
| `validate [PATH] [--json]` | Validate a TOML config against the schema; drift-check the shipped starter when PATH resolves to it. | `doxa config validate` |
| `help` | Show config command help | `doxa config help` |

### `config profiles` subcommands

| Subcommand | Description | Example |
|------------|-------------|---------|
| `list` | List configuration profiles | `doxa config profiles list` |
| `show NAME` | Show a single profile's contents | `doxa config profiles show daily` |
| `current` | Show the runtime active profile and its source | `doxa config profiles current` |
| `add NAME` | Create profile `NAME` (idempotent) | `doxa config profiles add my_profile` |
| `set NAME KEY VALUE` | Set a key on profile `NAME` | `doxa config profiles set daily general.default_mode quick_research` |
| `unset NAME KEY` | Remove a key from profile `NAME` | `doxa config profiles unset daily general.default_mode` |
| `remove NAME` | Delete profile `NAME` (idempotent) | `doxa config profiles remove old_profile` |
| `set-default NAME` | Persist `general.default_profile = NAME` | `doxa config profiles set-default daily` |
| `unset-default` | Remove `general.default_profile` from the target file | `doxa config profiles unset-default` |

All `config` mutators accept `--config PATH`, `--profile NAME`, and `--json`.
Use `--project` to write to `./doxa.config.toml` instead of the user-tier file.

## `modes` subcommands

| Subcommand | Description | Example |
|------------|-------------|---------|
| `list` | List research modes (filter with `--kind`, `--source`, `--name`; supports `--json`) | `doxa modes list --kind background` |
| `add NAME --model MODEL [--description D] [--kind K]` | Define a new mode | `doxa modes add my_mode --model gpt-5.6-sol --kind background` |
| `set NAME KEY VALUE` | Set a key on mode `NAME` | `doxa modes set my_mode temperature 0.3` |
| `unset NAME KEY` | Remove a key from mode `NAME` | `doxa modes unset my_mode temperature` |
| `remove NAME` | Delete a custom mode | `doxa modes remove my_mode` |
| `rename OLD NEW` | Rename a custom mode | `doxa modes rename old new` |
| `copy SRC DST [--from-profile X] [--override]` | Copy a mode definition; use `--override` to shadow a builtin name | `doxa modes copy deep_research deep_research_lite` |
| `set-default NAME` | Persist `general.default_mode = NAME` | `doxa modes set-default deep_research` |
| `unset-default` | Remove `general.default_mode` from the target file | `doxa modes unset-default` |

All mutators accept `--project`, `--config PATH`, `--profile X`, and `--json`.

## Built-in modes catalog

These ship with `doxa init`. List them at runtime with `doxa modes list`
(add `--kind immediate` or `--kind background` to filter).

### General modes (provider-agnostic)

| Mode | Default model | Notes |
|---|---|---|
| `default` | `o3` (OpenAI) | Plain question answering, no system prompt |
| `clarification` | `o3` | Asks Doxa for clarifying questions before research |
| `quick_research` | `gpt-5.6-sol` | Concise Deep Research; set `max_tool_calls` to bound cost |
| `exploration` | `gpt-5.6-sol` | Open-ended exploratory research |
| `deep_dive` | `gpt-5.6-sol` | In-depth research dive |
| `tutorial` | `gpt-5.6-sol` | Tutorial-format output |
| `solution` | `gpt-5.6-sol` | Solution-recommendation output |
| `prd` | `gpt-5.6-sol` | PRD-style design document |
| `tdd` | `gpt-5.6-sol` | TDD plan / implementation outline |
| `comparison` | `gpt-5.6-sol` | Comparison-table output |
| `deep_research` | `gpt-5.6-sol` | Exhaustive Deep Research |
| `thinking` | `o3` | Reasoning model, no deep-research overhead |

The chain `exploration` → `deep_dive` → `tutorial` → `solution` → `prd` → `tdd`
mirrors the Gemini chain (below) and is intended for multi-stage Deep
Research workflows where each stage's output can feed the next.

### Multi-provider fan-out

| Mode | Kind | Per-provider models | Notes |
|---|---|---|---|
| `all_deep_research` | background | openai → `gpt-5.6-sol`, perplexity → `sonar-deep-research`, gemini → `deep-research-preview-04-2026` | Parallel **Deep Research** across all three providers. Each provider runs concurrently with its own Deep Research model and writes its own output file under `./research-outputs/`. Used by the `all_deep` starter profile. |

Multi-provider fan-out requires `kind: background`. Immediate modes are
single-provider by design — the CLI rejects any immediate mode declared
with multiple providers at preflight.

### Provider-pinned variants

Single-provider immediate built-ins. Symmetric `*_quick` naming so the
fast variant of any provider is easy to discover (`doxa modes list | grep quick`).

| Mode | Provider | Default model | Notes |
|---|---|---|---|
| `openai_quick` | OpenAI | `gpt-4.1-mini` | Fast immediate mode with web search grounding |
| `openai_reasoning` | OpenAI | `o3` | Reasoning model with surfaced reasoning summaries + web grounding |
| `perplexity_quick` | Perplexity | `sonar` | Cheapest Perplexity immediate |
| `perplexity_pro` | Perplexity | `sonar-pro` | More capable Perplexity immediate |
| `perplexity_reasoning` | Perplexity | `sonar-reasoning-pro` | Perplexity reasoning model |
| `perplexity_deep_research` | Perplexity | `sonar-deep-research` | Background DR |
| `gemini_quick` | Gemini | `gemini-2.5-flash-lite` | Fast Gemini with web grounding |
| `gemini_pro` | Gemini | `gemini-2.5-pro` | Gemini Pro with web grounding |
| `gemini_reasoning` | Gemini | `gemini-2.5-pro` | Gemini Pro with surfaced thoughts |

### Gemini Deep Research modes

All map to `deep-research-preview-04-2026`:

| Mode | Description |
|---|---|
| `gemini_quick_research` | Quick Gemini Deep Research — short summary |
| `gemini_exploration` | Open-ended exploratory Gemini Deep Research |
| `gemini_deep_dive` | In-depth Gemini Deep Research dive |
| `gemini_tutorial` | Tutorial-format Gemini Deep Research |
| `gemini_solution` | Solution-recommendation Gemini Deep Research |
| `gemini_prd` | PRD-format Gemini Deep Research |
| `gemini_tdd` | TDD-plan Gemini Deep Research |
| `gemini_deep_research` | Exhaustive Gemini Deep Research |
| `gemini_comparison` | Comparison-table Gemini Deep Research |

## See also

- [README.md](../README.md) — start here
- [docs/providers.md](providers.md) — per-provider configuration details
- [docs/json-output.md](json-output.md) — `--json` envelope contract
- [docs/HERO-RECORDING.md](HERO-RECORDING.md) — re-recording the README asciinema hero
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development workflow
