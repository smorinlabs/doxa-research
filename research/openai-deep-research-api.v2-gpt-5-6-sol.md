# OpenAI Deep Research API — gpt-5.6-sol migration (v2)

Primary-source research underpinning the migration from the retired
`o3-deep-research` / `o4-mini-deep-research` models to `gpt-5.6-sol`.

- **Produced**: 2026-09-08 by `gpt-5.6-sol` itself, background mode, 13 web
  searches, 177 s, USD 0.83 (122,823 input + 10,572 output tokens).
- **Response id**: `resp_010a057965dedbbf006aa06547c2c487d0a81e00c858ecc9dc`
- **Supersedes**: `openai-deep-research-api.v1.md`, which documents the
  retired models and the legacy `web_search_preview` tool.

The run doubled as the depth-parity test for the replacement model: 13
autonomous searches on a multi-part prompt confirm `gpt-5.6-sol` performs
agentic research rather than answering from parametric memory.

> Machine-generated research. Claims are cited to `developers.openai.com`
> inline. Findings acted on in code were verified independently against the
> live API; see the changelog for that release.

---

# Executive summary

As of **September 8, 2026**, OpenAI’s official deprecation table says the `o3-deep-research-2025-06-26` and `o4-mini-deep-research-2025-06-26` snapshots—and their corresponding aliases—were shut down on **July 23, 2026**, with `gpt-5.6-sol` as the recommended replacement. ([developers.openai.com](https://developers.openai.com/api/docs/deprecations))

The migration is **not just a model-name substitution**:

- The retired models were specialized research agents with a narrow tool contract and fixed research-oriented behavior.
- `gpt-5.6-sol` is a general-purpose flagship reasoning model. To reproduce deep research behavior, you must explicitly supply web/file/MCP tools, set a suitable reasoning effort, establish a research-and-citation policy in the prompt, and usually preserve the old clarification/rewrite frontend.
- Replace `web_search_preview` with `web_search`.
- Background mode and `max_tool_calls` remain available, but GPT‑5.6 adds configurable reasoning, pro mode, persisted reasoning, explicit prompt caching, Programmatic Tool Calling, multi-agent orchestration, and many additional tools.
- OpenAI’s current **Deep research guide is stale/internally inconsistent**: it still tells developers to call the now-retired models and still shows `web_search_preview`, even though the deprecation page and current web-search guide direct developers to GPT‑5.6 Sol and `web_search`. Treat the deprecation page, GPT‑5.6 model guide, model page, and current web-search guide as authoritative for migration decisions. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

---

# 1. Prompting guidance

## 1.1 Guidance published for the specialized deep-research models

OpenAI documented a three-stage architecture matching the ChatGPT Deep Research experience:

1. **Clarification**
2. **Prompt rewriting/enrichment**
3. **Research execution by the deep-research model**

The Responses API did **not** automatically perform stages 1 or 2. OpenAI said the specialized models expected a fully formed prompt upfront, would not fill in missing context, and would begin researching immediately. Developers were expected either to collect clarifications themselves or use a faster model for the first two stages. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### Clarifying-question prompt pattern

OpenAI’s sample `instructions` prompt assigned the preliminary model a narrow role:

- Gather the information required to conduct the research.
- Be concise and well structured.
- Use bullets or numbered questions where helpful.
- Do not ask for details already supplied.
- Do not conduct the research itself.

The result was meant to be shown to the user, with the answers subsequently fed into the rewriting stage. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

This was an **application-level flow**, not an ability built into `o3-deep-research` or `o4-mini-deep-research`.

### Prompt-rewriting/developer-instructions pattern

For the rewriting stage, OpenAI’s sample instructed the intermediate model to produce research instructions rather than perform the research. The prescribed rewrite behavior was:

- Preserve every user preference and requested dimension.
- Make the task maximally specific.
- Treat necessary but unspecified dimensions as open-ended rather than inventing preferences.
- Phrase the task in the first person from the user’s perspective.
- Request tables when they improve comparisons, budgets, project tracking, or similar structured analysis.
- Specify the desired report headers and output format.
- Preserve the user’s language unless another language was requested.
- State which sources should be prioritized. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### Source-selection instructions

OpenAI specifically recommended that rewritten research prompts prioritize:

- Official manufacturers, brands, or primary websites for product research.
- Original papers and official journal publications for academic and scientific work.
- Sources in the language of the request when relevant.
- Explicitly named sources or source categories supplied by the user.

The examples also asked for reliable, current sources, measurable outcomes, primary evidence, inline citations, and returned source metadata. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### Expected output structure

The specialized models returned an ordinary Responses API response, but the `output` array could contain a long research trace consisting of:

- `web_search_call`
- `file_search_call`
- `code_interpreter_call`
- `mcp_tool_call`
- A final `message`

The final message’s `output_text` contained inline citation annotations with URL, title, and character offsets. OpenAI instructed developers to render citations as clearly visible, clickable links rather than merely printing the underlying citation metadata. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### Research-specific system/developer prompt implications

The old specialized models already supplied much of the research policy through model specialization. The developer’s main responsibilities were therefore:

- Resolve ambiguity before invoking the model.
- Fully specify scope, preferences, exclusions, source priorities, and report structure.
- Ask explicitly for quantitative evidence, primary sources, inline citations, and source metadata where needed.
- Supply at least one research data source: web search, file search, or a compatible MCP server. Code Interpreter was optional for analysis. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

---

## 1.2 Does that guidance still apply to GPT‑5.6 Sol?

### What still applies

Most of the **research-design guidance** remains useful:

- Establish scope and success criteria.
- State which ambiguities require a question.
- Define evidence and source-quality rules.
- Specify whether conflicting evidence must be reconciled.
- Request the output structure, tables, caveats, and citation behavior.
- Use a clarification/rewrite stage when the user’s request is materially underspecified.

GPT‑5.6’s model guide similarly says to provide domain context, hard constraints, approval boundaries, and success criteria, and to tell the model when an important ambiguity should trigger a question. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

### What no longer applies literally

The claim that “the model will not ask for context and simply starts researching” was behavior documented for the specialized models. GPT‑5.6 Sol has stronger intent understanding and is a general-purpose conversational reasoning model. It can infer routine gaps and can be instructed to ask questions when ambiguity is outcome-determinative. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

Therefore:

- You no longer need the old clarification stage merely because the model is incapable of clarifying.
- You may still want it for a controlled product UX, reproducibility, cost control, or to prevent a long background job from being launched with inadequate requirements.
- If the application must never pause for questions, say so and define how to handle missing dimensions—for example, by treating them as unconstrained and documenting assumptions.

### Has OpenAI published a separate deep-research prompt template for GPT‑5.6 Sol?

OpenAI has published a **GPT‑5.6 model guide**, but not a dedicated “GPT‑5.6 Sol Deep Research” system-prompt template equivalent to a replacement specialized-model prompt.

The GPT‑5.6-specific guidance is broader:

- Use outcome-oriented prompting.
- Provide constraints, success criteria, and important context.
- Do not prescribe every intermediate step unless the process itself matters.
- Set reasoning effort intentionally.
- Tell the model when ambiguity should lead to a question.
- Use GPT‑5.6’s newer orchestration and reasoning features where useful. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

Meanwhile, the current Deep research page still names and demonstrates the retired models. That page remains useful as a description of the **research workflow and output anatomy**, but its model names and `web_search_preview` examples should not be copied into new production code. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### Recommended GPT‑5.6 Sol research instructions

A practical replacement developer prompt should explicitly cover areas that the specialized model previously supplied implicitly:

```text
You are a research agent. Produce a source-grounded report that answers
the user's complete request.

Research policy:
- Search the web for every material factual claim that may be current,
  uncertain, or disputed.
- Prefer primary and official sources. Use original papers for scientific
  claims and official regulatory or company publications where applicable.
- Follow important second-order leads and reconcile conflicting sources.
- Continue researching until further searches are unlikely to materially
  change the answer.
- Never invent facts or citations. Clearly identify unresolved uncertainty.
- Cite web-derived claims inline and preserve the source annotations returned
  by the web-search tool.

Interaction policy:
- Ask a concise clarifying question only if the missing information could
  materially change the result.
- Otherwise make reasonable assumptions, state them, and continue.

Output:
- Executive summary
- Findings organized by the user's requested questions
- Comparison tables where useful
- Limitations and unresolved questions
- Source-backed migration recommendations
```

This is an application recommendation synthesized from OpenAI’s old deep-research prompt guidance and current GPT‑5.6 guidance, rather than an official verbatim GPT‑5.6 template. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

---

# 2. Responses API differences

## Comparison table

| Area | Retired deep-research models | `gpt-5.6-sol` |
|---|---|---|
| Model behavior | Specialized, research-first agent | General flagship reasoning model; research behavior must be prompted and tooled |
| Background mode | Supported and strongly recommended | Supported through ordinary Responses API background mode |
| Web tool | Documentation examples used `web_search_preview` | Use `web_search`; preview remains legacy-compatible but lacks newer controls |
| Code Interpreter | Optional supported analysis tool | Supported |
| `max_tool_calls` | Supported; documented as primary cost/latency bound | Generic Responses parameter; cap applies across all built-in tool calls |
| Reasoning effort | No configurable effort levels documented; examples used `reasoning.summary` | `none`, `low`, `medium`, `high`, `xhigh`, `max`; default `medium` |
| Pro mode | Not documented | `reasoning.mode: "pro"` |
| Function calling / structured outputs | Not supported | Supported |
| Other tools | Web, file search, specialized MCP search/fetch, Code Interpreter | Web, file search, Code Interpreter, MCP, functions, hosted shell, apply patch, computer use, skills, tool search, image generation, and others |

---

## 2.1 Background mode

### Retired models

OpenAI strongly recommended:

```json
{
  "background": true
}
```

because specialized research calls could take tens of minutes. Developers could poll the response until it left `queued` or `in_progress`, or configure a webhook. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### GPT‑5.6 Sol

Background mode remains a generic Responses API facility; the invocation pattern does not fundamentally change:

```json
{
  "model": "gpt-5.6-sol",
  "background": true
}
```

You can poll `GET /v1/responses/{response_id}`, cancel an in-flight background response, or combine background execution with streaming. ([developers.openai.com](https://developers.openai.com/api/docs/guides/background))

Current background mode also supports resumable streaming. If the response was originally created with both `background: true` and `stream: true`, the client can reconnect using the response ID and `starting_after` set to the last received `sequence_number`. A background response cannot later be streamed unless it was initially created with streaming enabled. ([developers.openai.com](https://developers.openai.com/api/docs/guides/background))

### ZDR documentation conflict

There is a notable discrepancy in current official documentation:

- The Deep research guide says background mode temporarily retains data for roughly ten minutes and characterizes it as incompatible with ZDR requirements, even though `background=true` continues to be accepted on ZDR credentials for legacy reasons. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))
- The dedicated current background-mode guide says ZDR-project background requests run with `store=false`, while data is still temporarily written to disk for roughly ten minutes to support execution and polling. ([developers.openai.com](https://developers.openai.com/api/docs/guides/background))

For a strict ZDR or regulated deployment, do not infer compliance merely from successful API acceptance. Evaluate the temporary-retention behavior against your policy and confirm your project’s contractual configuration with OpenAI.

---

## 2.2 `web_search` versus `web_search_preview`

### Retired models

The deep-research examples consistently used:

```json
{"type": "web_search_preview"}
```

alongside optional file search and Code Interpreter. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

### GPT‑5.6 Sol

Use:

```json
{"type": "web_search"}
```

OpenAI’s current web-search migration table explicitly directs Responses API integrations from `web_search_preview` to `web_search`. Although Responses still accepts the preview type for legacy integrations, OpenAI says new integrations should use the non-preview tool. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

The current `web_search` tool adds controls unavailable or ineffective with the preview type:

- `filters`, including allowed and blocked domains.
- `external_web_access` to choose live internet access versus cached/indexed-only search.
- `return_token_budget`, including an unlimited setting for longer reasoning searches.
- Complete consulted-source lists through the search call’s `sources` field.

`web_search_preview` does not support `filters` or `return_token_budget`, and ignores `external_web_access`. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

Both types can expose a full `sources` list, distinct from inline citations: inline citations identify the most relevant references attached to answer text, while `sources` reports the larger set of URLs consulted. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

### Search is not automatically mandatory

With GPT‑5.6 and the Responses API, web search is a tool. Under `tool_choice: "auto"`, the model may decide not to search. If grounding is mandatory, use `tool_choice: "required"` or select the web-search tool specifically. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

This is a major behavioral difference from treating the old specialized model as intrinsically “deep research.”

---

## 2.3 Code Interpreter

The retired models supported Code Interpreter as an optional analysis tool:

```json
{
  "type": "code_interpreter",
  "container": {"type": "auto"}
}
```

OpenAI positioned it as the data-analysis side of the research workflow, complementing web, file, and MCP retrieval. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

GPT‑5.6 Sol also supports Code Interpreter, so the basic tool declaration can generally be retained. ([developers.openai.com](https://developers.openai.com/api/docs/models/gpt-5.6-sol))

The broader difference is that GPT‑5.6 Sol supports many tool categories that the specialized models did not. The old models explicitly did not support general function calling; Sol supports function calling, Structured Outputs, hosted shell, apply patch, skills, computer use, MCP, and tool search in addition to research tools. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

Do not automatically expose all those tools to a research call. A larger tool surface can increase routing variance, security exposure, and the number of calls consumed under a shared `max_tool_calls` budget.

---

## 2.4 `max_tool_calls`

For the retired models, OpenAI documented `max_tool_calls` as the primary mechanism for bounding research cost and latency. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

The parameter remains part of Responses API creation. Its current semantics are important: it is the **maximum total number of calls to built-in tools across the response**, not a separate maximum for each tool. Once reached, additional built-in tool attempts are ignored. ([developers.openai.com](https://developers.openai.com/api/reference/cli/resources/responses/methods/create?utm_source=openai))

Consequences for migration:

- A limit formerly consumed mainly by web searches, file searches, and MCP fetches may now also be consumed by Code Interpreter and other built-in tools.
- A low inherited value may truncate GPT‑5.6 before it verifies key claims or synthesizes the report.
- An unlimited or overly high value can create unexpectedly long or expensive calls.
- Set and evaluate the bound against report completeness, not merely request success.

---

## 2.5 Reasoning effort

### Retired specialized models

The deep-research guide did not document configurable `reasoning.effort` values for `o3-deep-research` or `o4-mini-deep-research`. Its examples sometimes enabled:

```json
{
  "reasoning": {
    "summary": "auto"
  }
}
```

That controls the returned reasoning summary, not the amount of reasoning effort. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

In practice, the models’ deep-research behavior was supplied by the specialized model variant rather than by choosing a public effort level.

### GPT‑5.6 Sol

GPT‑5.6 Sol supports:

- `none`
- `low`
- `medium`
- `high`
- `xhigh`
- `max`

The documented default is `medium`. OpenAI recommends `medium` as a balanced starting point, `low` for latency-sensitive work, `high` or `xhigh` where evaluations show a gain, and `max` only for the hardest quality-first tasks. ([developers.openai.com](https://developers.openai.com/api/docs/models/gpt-5.6-sol))

For a replacement deep-research workload, sensible initial tests are:

```json
"reasoning": {"effort": "high"}
```

and:

```json
"reasoning": {"effort": "xhigh"}
```

Use `max` only after measuring whether the incremental evidence quality justifies its latency and token use.

GPT‑5.6 also supports:

```json
"reasoning": {
  "effort": "xhigh",
  "mode": "pro"
}
```

Pro mode does additional model work and returns one final answer. Effort and mode are independent; omitting effort still defaults to `medium`. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

---

# 3. New parameters, tools, and practices for long-running research

The following are current capabilities that were absent from, or not documented in, the original specialized deep-research integration pattern.

## 3.1 Modern `web_search` controls

### Domain filtering

`web_search.filters` can constrain results to allowed domains or exclude blocked domains. This is useful for:

- Official-sources-only research.
- Regulatory research.
- Scientific literature.
- Excluding aggregators, forums, or known low-quality domains.

OpenAI documents up to 100 allowed or blocked domains and says domain filtering is available only with the Responses API `web_search` tool. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

### Live-access control

`external_web_access: false` makes the tool use cached/indexed results rather than fetching live content. The default is live access. The preview tool ignores this field. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

### Returned research token budget

`return_token_budget` lets reasoning-based search return more source material to the model; the current documentation includes an `unlimited` option for longer research. It does not apply to `web_search_preview`. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

### Complete source inventory

Use the search call’s `sources` field when you need an audit trail containing every consulted URL, rather than only the URLs cited in the final prose. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

---

## 3.2 Background streaming and reconnection

Long background calls can now be streamed while still surviving a client disconnect:

```json
{
  "background": true,
  "stream": true
}
```

Store each event’s `sequence_number`. On disconnect, resume from the last cursor using `starting_after`. ([developers.openai.com](https://developers.openai.com/api/docs/guides/background))

This is preferable when the UI should expose research progress or tool-call events without making successful completion depend on one uninterrupted HTTP connection.

## 3.3 Explicit cancellation

A long-running response can be cancelled through:

```http
POST /v1/responses/{response_id}/cancel
```

This should be wired to user cancellation, job expiration, account limits, or internal budget controls. ([developers.openai.com](https://developers.openai.com/api/docs/guides/background))

## 3.4 Programmatic Tool Calling

GPT‑5.6 can write and execute JavaScript in a hosted runtime to coordinate eligible tools, move results between calls, and process intermediate outputs. OpenAI recommends it for bounded tool-heavy workflows that do not need fresh model judgment after every step. It is ZDR-compatible and has no additional container charge. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

For research, it can help with:

- Repeated structured lookups.
- Deduplicating or joining retrieved results.
- Computing aggregate statistics.
- Parallelizing deterministic retrieval operations.

It should not replace model-level judgment for choosing follow-up research directions or reconciling conflicting evidence.

## 3.5 Multi-agent orchestration

GPT‑5.6 supports beta multi-agent orchestration, allowing one instance to coordinate parallel subagents and synthesize their results. OpenAI positions it for complex tasks that split cleanly into independent workstreams. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

Potential research decomposition:

- One subagent per jurisdiction.
- One per competitor.
- One for primary scientific evidence and another for regulation.
- Separate agents for factual collection, contradiction checking, and synthesis.

This may reduce wall-clock time, but it can increase total token and tool consumption. Your `max_tool_calls`, source policy, and final synthesis instructions should account for work performed by parallel agents.

## 3.6 Persisted reasoning

GPT‑5.6 can make prior reasoning items available in subsequent turns. Its default is effectively `all_turns`, unlike earlier models that defaulted to the current turn. Continue with `previous_response_id`, or manually replay all relevant response items when you manage history yourself. For stateless/ZDR use, replay the encrypted reasoning items returned by the API. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

This is useful for research workflows such as:

1. Initial broad report.
2. User narrows scope.
3. Model updates only affected sections.
4. Model conducts targeted follow-up searches without rebuilding all prior analysis.

Set `reasoning.context: "current_turn"` when old assumptions or priorities no longer apply.

## 3.7 Explicit prompt caching

GPT‑5.6 can use explicit cache boundaries for stable instructions and source-policy prefixes. Implicit caching still works, but cache writes are billed at 1.25 times uncached input while reads remain discounted. OpenAI advises monitoring `cached_tokens` and `cache_write_tokens`; the old `prompt_cache_retention` parameter is replaced by `prompt_cache_options.ttl`. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

A research integration should place stable material early:

- Research policy.
- Citation policy.
- Tool-use policy.
- Output schema.
- Organization-specific source rules.

Put the variable user question and transient data after the cache boundary.

## 3.8 Much larger model context—but not unlimited search context

GPT‑5.6 Sol has a 1,050,000-token context window and 128,000 maximum output tokens, compared with the retired models’ 200,000-token context and 100,000 maximum output. ([developers.openai.com](https://developers.openai.com/api/docs/models/o3-deep-research))

However, the hosted web-search context remains limited to 128,000 tokens even when the model context window is larger. Do not assume the million-token model context means one search operation can inject a million tokens of web material. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

---

# 4. Migration pitfalls

## 4.1 A model-name-only migration is insufficient

Changing:

```json
"model": "o3-deep-research"
```

to:

```json
"model": "gpt-5.6-sol"
```

may produce a valid call, but it does not recreate specialized deep-research behavior. You must retain or recreate:

- The clarification and prompt-enrichment stage.
- Explicit research depth and source-quality instructions.
- The web/file/MCP tool configuration.
- Citation requirements.
- Cost and latency bounds.
- Background-job handling.

## 4.2 Replace `web_search_preview`

Move to:

```json
{"type": "web_search"}
```

The preview type is still accepted for legacy Responses integrations, but it lacks domain filters, live-access controls, and returned-token-budget controls. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

## 4.3 Search may silently not happen

The old specialized model required a research source and was optimized to browse. GPT‑5.6 with `tool_choice: "auto"` may answer from model knowledge without searching. If the user expects current, cited research, require the tool or make the source-grounding policy unambiguous. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

## 4.4 The old “fully formed prompt, no questions” behavior is gone

GPT‑5.6 has better intent inference and can ask questions when instructed. Decide explicitly among three product behaviors:

1. Always run a separate clarification stage.
2. Permit Sol to ask only outcome-changing questions.
3. Forbid questions and require documented assumptions.

Without such a policy, background jobs may either launch prematurely or stop for a clarification that your asynchronous job system cannot deliver.

## 4.5 Do not carry over the entire old prompt mechanically

Some old instructions compensated for limitations of specialized or earlier models. GPT‑5.6’s guidance favors clear outcomes, hard constraints, evidence rules, and success criteria while leaving room for the model to choose the efficient path. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

Recommended process:

1. Preserve the old prompt and tool configuration as a baseline.
2. Change the model and web-tool type.
3. Pin a reasoning effort.
4. Run representative evaluations.
5. Simplify or revise prompt instructions only after identifying a measured regression.

## 4.6 Reasoning defaults can materially change cost and latency

The old specialized models had no documented public effort knob. GPT‑5.6 defaults to `medium`. Explicitly set the effort so SDK updates or defaults do not alter your latency profile. ([developers.openai.com](https://developers.openai.com/api/docs/models/gpt-5.6-sol))

For deep research, test at least:

- `medium`
- `high`
- `xhigh`

Reserve `max` and pro mode for cases in which evaluations show meaningful gains.

## 4.7 `max_tool_calls` is shared

The cap applies across all built-in tool calls, not independently per web, file, MCP, and Code Interpreter tool. An inherited limit may now be exhausted by a more varied GPT‑5.6 workflow. ([developers.openai.com](https://developers.openai.com/api/reference/cli/resources/responses/methods/create?utm_source=openai))

Log:

- Calls by tool type.
- Whether the cap was reached.
- Number of cited and consulted sources.
- Final-answer completeness.
- Latency and total cost.

## 4.8 Citation rendering must remain annotation-aware

Do not scrape citation markers from generated prose. Continue to use the `annotations` attached to `output_text`, and use search-call `sources` when you need the complete research trail. Render inline citations visibly and as clickable links. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

Expect citation locations and formatting to differ from the old models even when the underlying answer is equivalent.

## 4.9 Output arrays may contain more item types

The old parser may assume that output contains only research calls plus one final message. GPT‑5.6 can generate a broader set of tool calls, reasoning items, structured outputs, and potentially multi-agent events. Its model page confirms support for function calling and Structured Outputs, both unsupported by the retired models. ([developers.openai.com](https://developers.openai.com/api/docs/models/o3-deep-research))

Parse by item `type`; do not rely on fixed array positions or assume `output[0]` is the final message.

## 4.10 MCP assumptions may no longer be appropriate

The specialized models required a narrow research-compatible MCP interface with read-only `search` and `fetch` tools, and OpenAI required `require_approval: "never"` for that workflow. General function tools and arbitrary MCP servers were not supported. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

GPT‑5.6 supports general MCP and function calling. This gives you more flexibility, but it also means:

- Revisit approval settings.
- Do not expose write-capable tools unless required.
- Separate private data retrieval from external web access where exfiltration is a concern.
- Audit tool descriptions and returned data for prompt injection.
- Apply least privilege rather than retaining `require_approval: "never"` indiscriminately.

## 4.11 Cost direction differs depending on the retired model

The documented token prices were:

- `o3-deep-research`: $10 input / $40 output per million tokens.
- `o4-mini-deep-research`: $2 input / $8 output.
- Current GPT‑5.6 Sol: $4 input / $20 output. ([developers.openai.com](https://developers.openai.com/api/docs/models/o3-deep-research?utm_source=openai))

Therefore, before tool fees and reasoning-volume differences:

- Migrating from `o3-deep-research` lowers per-token prices.
- Migrating from `o4-mini-deep-research` increases per-token prices substantially.

GPT‑5.6 may be more token-efficient, but cost parity is not guaranteed. Also note that prompts above 272,000 input tokens receive higher full-request pricing, and cache writes have a premium. ([developers.openai.com](https://developers.openai.com/api/docs/models/gpt-5.6-sol))

## 4.12 Larger context can encourage accidental context bloat

Sol’s million-token context may tempt integrations to inject raw source corpora. Prefer:

- File search over wholesale file insertion.
- Web search source budgets.
- Prompt caching for stable instructions.
- Programmatic preprocessing for deterministic joins or deduplication.
- Compaction or staged research for multi-turn projects.

The web-search context itself remains 128,000 tokens, so loading more application context does not enlarge the search tool’s context. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))

## 4.13 Background/ZDR behavior needs policy review

Because background calls require temporary retention for asynchronous execution and polling—and the two current OpenAI pages describe the ZDR implications differently—strict ZDR customers should not treat the migration as compliance-neutral. ([developers.openai.com](https://developers.openai.com/api/docs/guides/deep-research))

## 4.14 Safeguards may affect long technical research

GPT‑5.6 runs real-time cyber and biology misuse classifiers during generation. Some legitimate dual-use requests may be blocked or paused for synchronous review. OpenAI also recommends supplying a stable, privacy-preserving `safety_identifier` when serving individual end users. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))

Account for this in:

- Background-job timeouts.
- Retry logic.
- User-facing failure states.
- Monitoring for incomplete or refused reports.

---

# Recommended replacement request

A conservative starting point is:

```json
{
  "model": "gpt-5.6-sol",
  "background": true,
  "store": true,
  "reasoning": {
    "effort": "high",
    "summary": "auto",
    "context": "all_turns"
  },
  "tools": [
    {
      "type": "web_search",
      "filters": {
        "allowed_domains": [
          "example-regulator.gov",
          "example-journal.org"
        ]
      }
    },
    {
      "type": "file_search",
      "vector_store_ids": ["vs_..."]
    },
    {
      "type": "code_interpreter",
      "container": {
        "type": "auto"
      }
    }
  ],
  "tool_choice": "required",
  "max_tool_calls": 80,
  "instructions": "Produce a comprehensive, source-grounded research report. Prefer primary and official sources, reconcile contradictory evidence, cite every material web-derived claim inline, preserve citation annotations, and clearly identify unresolved uncertainty. Follow important leads until further research is unlikely to materially change the conclusions. Use the requested report structure and tables.",
  "input": "The fully clarified and enriched user research request."
}
```

Adjustments:

- Use `tool_choice: "auto"` if search is optional.
- Remove domain filters when broad discovery is required.
- Test `xhigh` against `high`; do not assume `max` is cost-effective.
- Use `stream: true` from creation if resumable progress streaming is needed.
- Use `store: false` only after reviewing temporary background retention and your data-control requirements.
- Set `max_tool_calls` from evaluations rather than copying the example value.

---

# Bottom-line migration checklist

1. Replace the model with `gpt-5.6-sol` or the `gpt-5.6` alias. ([developers.openai.com](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6))
2. Replace `web_search_preview` with `web_search`. ([developers.openai.com](https://developers.openai.com/api/docs/guides/tools-web-search))
3. Preserve background execution; add cancellation and optionally resumable streaming. ([developers.openai.com](https://developers.openai.com/api/docs/guides/background))
4. Explicitly set `reasoning.effort`.
5. Recreate research behavior through developer instructions rather than relying on the model slug.
6. Keep a clarification/rewrite stage if the UX requires deterministic scoping before an expensive background job.
7. Require web search when current grounding is mandatory.
8. Re-evaluate `max_tool_calls` because the cap is global across built-in tools.
9. Parse response items by type and citations through annotations.
10. Revisit MCP permissions and prompt-injection controls.
11. Measure cost separately for former o3 and o4-mini workloads.
12. Validate background-mode retention against ZDR and compliance requirements.
13. Run report-quality evaluations covering source quality, citation correctness, contradiction resolution, completeness, latency, and cost.