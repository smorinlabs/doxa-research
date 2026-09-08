# Provider configuration

Doxa Research talks to three Deep Research backends. Each provider has its
own auth, configuration knobs, and model catalog. This document is the
detailed reference; the [README](../README.md) covers the 30-second setup.

All settings below live in `~/.config/doxa/doxa.config.toml`
unless noted otherwise. Run `doxa providers list` to see which providers
are currently configured.

## OpenAI provider

The OpenAI provider integrates with OpenAI's Chat Completions / Responses
API for AI-powered research.

### API key setup

Configure your OpenAI API key using one of these methods (in order of
precedence):

1. **Command-line flag** (highest priority):
   ```bash
   doxa "prompt" --api-key-openai "sk-..." --provider openai
   ```

2. **Environment variable**:
   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

3. **Configuration file** (`~/.config/doxa/doxa.config.toml`):
   ```toml
   [providers.openai]
   api_key = "${OPENAI_API_KEY}"  # Reference env var
   # Or directly:
   api_key = "sk-..."
   ```

### Configuration options

All OpenAI settings can be configured in
`~/.config/doxa/doxa.config.toml`:

```toml
[providers.openai]
api_key = "${OPENAI_API_KEY}"  # API key (required)
model = "o3"                    # Model to use (default: o3 for general modes)
timeout = 30.0                  # Request timeout in seconds (default: 30.0)
temperature = 0.7               # Creativity/randomness, 0.0–2.0. Omitted automatically where
                                # unsupported: o-series (o1/o3/o4-…) models, the specific IDs in
                                # NO_TEMPERATURE_MODELS (gpt-5, gpt-5-mini, gpt-5-nano, gpt-5.5,
                                # gpt-5.6/-sol/-luna/-terra, gpt-6-astra), and any request whose
                                # reasoning_effort is low/medium/high/xhigh/max. gpt-5.1, gpt-5.2
                                # and gpt-5.4 DO accept it at effort "none" or with no effort set.
max_tokens = 4000               # Maximum response tokens (default: 4000)
```

### Available models

Doxa Research ships these OpenAI models in its built-in catalog:

- `o3` — Reasoning model used by the `default`, `clarification`, and
  `openai_reasoning` modes.
- `gpt-5.6-sol` — General-purpose flagship used for Deep Research by the
  `deep_research`, `exploration`, `deep_dive`, `tutorial`, `solution`, `prd`,
  `tdd`, `comparison`, and `quick_research` modes. It replaced
  `o3-deep-research` and `o4-mini-deep-research`, which OpenAI shut down on
  2026-07-23. Because it is general-purpose rather than a research-first
  agent, Doxa supplies the research behaviour those models had built in: the
  `web_search` tool, `tool_choice = {type = "web_search"}` so the model cannot
  answer without searching, and `reasoning_effort = "high"` (the API default
  is `medium`). Setting `web_search = false` on a mode omits the tool and the
  default choice, for modes that synthesise from supplied material; an
  explicitly configured `tool_choice` is still sent. `quick_research` has no
  cheaper replacement model and no default tool-call cap; set `max_tool_calls`
  yourself to bound its cost.

> **These defaults apply to background submission only.** A `kind = "immediate"`
> mode goes through the streaming path, which defaults to no reasoning effort
> (so the API's own `medium`), no forced `tool_choice`, no `code_interpreter`,
> and `web_search = false`. Values you set explicitly — `reasoning_effort`,
> `tool_choice`, `temperature`, `web_search` — are honoured on both paths; only
> the defaults differ. If you want research-grade depth from an immediate mode,
> set those keys yourself rather than relying on the background defaults.

Run `doxa providers models -P openai` to list models live from the API
(includes any additional models your OpenAI account exposes — Doxa will
accept them but the built-in modes are tuned for the models above). Retired
models keep appearing in that listing, so the table marks them: a `Status` of
`retired <date>` means the ID resolves in the catalogue but every call to it
fails with `model_not_found`.

### CLI options

Override configuration via command-line:

```bash
# Set custom timeout
doxa "prompt" --provider openai --timeout 60.0

# Verbose mode shows configuration
doxa "prompt" --provider openai -v
```

### Performance tuning

**Temperature settings:**
- `0.0-0.3`: Factual, consistent responses
- `0.4-0.7`: Balanced creativity (default)
- `0.8-1.2`: Creative, varied responses

**Timeout recommendations:**
- Short prompts: 15–30 seconds
- Deep research: 60–120 seconds
- Complex analysis: 180+ seconds

## Perplexity provider

Perplexity's `sonar-deep-research` model powers the `perplexity_*_research`
modes. The provider uses an OpenAI-compatible async client.

Auth precedence mirrors OpenAI:

1. `--api-key-perplexity` flag
2. `PERPLEXITY_API_KEY` env var
3. `[providers.perplexity] api_key = "..."` in the config file

```toml
[providers.perplexity]
api_key = "${PERPLEXITY_API_KEY}"
model = "sonar-deep-research"
timeout = 120.0
```

Use `doxa providers models -P perplexity` for the live catalog.

## Gemini provider

Gemini Deep Research uses Google AI Studio's Interactions API. It requires
a **Tier 1+** Google AI Studio account.

Auth precedence:

1. `--api-key-gemini` flag
2. `GEMINI_API_KEY` env var
3. `[providers.gemini] api_key = "..."` in the config file

### Gemini Deep Research costs

Gemini Deep Research is a paid-tier feature. Estimated cost per task:

- `deep-research-preview-04-2026` (default for the 9 `gemini_*_research`
  modes): **$1–$3 per task**
- `deep-research-max-preview-04-2026` (deferred to a successor project):
  **$3–$7 per task**

Both agents are currently in **preview**; pricing and behavior may change.
The 60-minute hard research-time limit is enforced upstream — if you hit
this with longer prompts, set `[execution].max_wait = 60` in your config
(default is 30 minutes).

### Gemini modes

The 9 background deep-research modes each map to the
`deep-research-preview-04-2026` model via the Gemini Interactions API:

| Mode | Description |
|---|---|
| `gemini_quick_research` | Quick Gemini Deep Research — short summary. |
| `gemini_exploration` | Open-ended exploratory Gemini Deep Research. |
| `gemini_deep_dive` | In-depth Gemini Deep Research dive. |
| `gemini_tutorial` | Tutorial-format Gemini Deep Research. |
| `gemini_solution` | Solution-recommendation Gemini Deep Research. |
| `gemini_prd` | PRD-format Gemini Deep Research. |
| `gemini_tdd` | TDD-plan Gemini Deep Research. |
| `gemini_deep_research` | Exhaustive Gemini Deep Research. |
| `gemini_comparison` | Comparison-table Gemini Deep Research. |

## See also

- [README.md](../README.md) — start here
- [docs/COMMANDS.md](COMMANDS.md) — full CLI surface
- [docs/json-output.md](json-output.md) — `--json` envelope contract
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development workflow
